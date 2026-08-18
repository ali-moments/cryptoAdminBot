import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Set
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.dto import PriceTick
from app.market.events import ProviderChangedEvent, ProviderConnectedEvent, ProviderDisconnectedEvent
from app.market.providers.base import BaseProvider


class ProviderManager:
    """
    Simple market data provider manager with automatic failover.
    
    Uses a single active provider at a time with automatic failover:
    1. Primary (Binance) - preferred provider
    2. Fallback (Bybit) - used when primary fails
    3. Disaster (OKX) - used when both primary and fallback fail
    """

    # Reconnection settings
    RECONNECT_DELAY = 3  # seconds between reconnection attempts
    HEALTH_CHECK_INTERVAL = 7  # seconds between health checks
    GRACE_PERIOD = 12  # Grace period after connection (increased for better stability)

    def __init__(
        self,
        dispatcher: EventDispatcher,
        cache: PriceCache,
        providers: dict[Provider, BaseProvider],
        primary: Provider = Provider.BINANCE,
        fallback: Provider = Provider.BYBIT,
        disaster: Provider = Provider.OKX,
    ) -> None:
        logger.info(f"Creating ProviderManager with primary: {primary.value}")
        
        self._dispatcher = dispatcher
        self._cache = cache
        self._providers = providers
        self._primary = primary
        self._fallback = fallback
        self._disaster = disaster
        self._active = primary

        # Track subscriptions (symbol -> reference count)
        self._subscriptions: dict[str, int] = {}
        self._sync_lock = asyncio.Lock()
        self._active_lock = asyncio.Lock()

        # Background tasks
        self._health_check_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._running = False

    @property
    def active_provider(self) -> BaseProvider:
        """Get the currently active provider."""
        return self._providers[self._active]

    @property
    def active_provider_name(self) -> Provider:
        """Get the name of the currently active provider."""
        return self._active

    @property
    def is_using_primary(self) -> bool:
        """Check if currently using the primary provider."""
        return self._active == self._primary

    def _is_provider_healthy(self, provider: BaseProvider) -> bool:
        """Check if provider is healthy (connected + recent data OR within grace period)"""
        if not provider.is_connected:
            return False
            
        # If provider just connected, allow grace period without requiring data
        if provider.connection_time:
            connection_age = (datetime.now(timezone.utc) - provider.connection_time).total_seconds()
            if connection_age < self.GRACE_PERIOD:
                return True  # Grace period - connection is enough
                
        # After grace period, require actual data flow
        return provider.is_healthy

    async def start(self) -> None:
        """Start the manager and connect to the primary provider."""
        if self._running:
            logger.warning("ProviderManager already running")
            return

        self._running = True
        logger.info(f"Starting ProviderManager with primary provider: {self._primary.value}")

        # Try to connect to primary first
        connected = await self._try_connect(self._primary)

        if not connected:
            # Primary failed, try fallback
            logger.warning(f"Primary provider {self._primary.value} failed to connect, trying fallback")
            connected = await self._try_connect(self._fallback)

            if not connected:
                # Fallback failed, try disaster
                logger.error(f"Fallback provider {self._fallback.value} failed, trying disaster provider")
                connected = await self._try_connect(self._disaster)

                if not connected:
                    self._running = False
                    raise RuntimeError("All providers failed to connect")

        # Start background tasks
        self._health_check_task = asyncio.create_task(self._health_check_loop())

        # Start reconnection task if not using primary
        if not self.is_using_primary:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        logger.success(f"ProviderManager started with {self._active.value}")

    async def stop(self) -> None:
        """Stop the manager and disconnect from all providers."""
        if not self._running:
            return

        logger.info("Stopping ProviderManager")
        self._running = False

        # Cancel background tasks
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        # Disconnect active provider
        if self._active in self._providers:
            await self._disconnect_provider(self._active)

        logger.info("ProviderManager stopped")

    async def subscribe(self, symbol: str) -> None:
        """Subscribe to a symbol on the active provider."""
        async with self._sync_lock:
            count = self._subscriptions.get(symbol, 0)

            if count == 0:
                # First subscription - subscribe on active provider
                active_provider = self.active_provider
                await active_provider.subscribe(symbol)
                logger.debug(f"Subscribed {symbol} to {self._active.value}")

            self._subscriptions[symbol] = count + 1

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from a symbol. Uses reference counting."""
        async with self._sync_lock:
            count = self._subscriptions.get(symbol)

            if count is None:
                return

            if count == 1:
                # Last subscription - actually unsubscribe
                active_provider = self.active_provider
                try:
                    await active_provider.unsubscribe(symbol)
                    logger.debug(f"Unsubscribed {symbol} from {self._active.value}")
                except Exception as e:
                    logger.error(f"Failed to unsubscribe {symbol}: {e}")

                del self._subscriptions[symbol]
                return

            self._subscriptions[symbol] = count - 1

    async def sync(self, required_symbols: set[str]) -> None:
        """Synchronize current subscriptions with required symbol set."""
        if not self._running:
            logger.warning("Cannot sync subscriptions: ProviderManager not running")
            return

        # Get current subscriptions
        async with self._sync_lock:
            current_symbols = set(self._subscriptions.keys())

        # Calculate differences
        missing_symbols = required_symbols - current_symbols
        unused_symbols = current_symbols - required_symbols

        # Log sync operation
        if missing_symbols or unused_symbols:
            logger.info(
                f"SUBSCRIPTION SYNC: +{len(missing_symbols)} -{len(unused_symbols)} "
                f"(current: {len(current_symbols)}, required: {len(required_symbols)})"
            )

        # Subscribe to missing symbols
        for symbol in missing_symbols:
            try:
                await self.subscribe(symbol)
                logger.debug(f"✓ Subscribed {symbol}")
            except Exception as e:
                logger.error(f"✗ Failed to subscribe {symbol}: {e}")

        # Unsubscribe from unused symbols
        for symbol in unused_symbols:
            try:
                await self.unsubscribe(symbol)
                logger.debug(f"✓ Unsubscribed {symbol}")
            except Exception as e:
                logger.error(f"✗ Failed to unsubscribe {symbol}: {e}")

    def get_price(self, symbol: str) -> PriceTick | None:
        """Get the latest price for a symbol from the cache."""
        return self._cache.get(symbol)

    async def _try_connect(self, provider: Provider) -> bool:
        """Try to connect to a provider. Returns True if successful."""
        try:
            provider_instance = self._providers.get(provider)
            if not provider_instance:
                logger.error(f"Provider {provider.value} not configured")
                return False

            logger.info(f"Connecting to {provider.value}...")
            await provider_instance.connect()

            # Wait for connection and initial data to establish
            await asyncio.sleep(2.0)

            if provider_instance.is_connected:
                self._active = provider
                await self._dispatcher.publish(
                    ProviderConnectedEvent(provider=provider)
                )
                logger.success(f"Connected to {provider.value}")
                return True

            logger.warning(f"Connection to {provider.value} failed")
            return False

        except Exception as e:
            logger.error(f"Failed to connect to {provider.value}: {e}")
            return False

    async def _disconnect_provider(self, provider: Provider) -> None:
        """Disconnect from a provider."""
        try:
            provider_instance = self._providers.get(provider)
            if provider_instance and provider_instance.is_connected:
                logger.info(f"Disconnecting from {provider.value}")
                await provider_instance.disconnect()
                await self._dispatcher.publish(
                    ProviderDisconnectedEvent(provider=provider)
                )
        except Exception as e:
            logger.error(f"Error disconnecting from {provider.value}: {e}")

    async def _switch_provider(self, new_provider: Provider) -> bool:
        """Switch to a different provider and transfer all subscriptions."""
        old_provider = self._active

        if new_provider == old_provider:
            return True

        logger.info(f"Switching from {old_provider.value} to {new_provider.value}")

        # Connect to new provider
        if not await self._try_connect(new_provider):
            logger.error(f"Failed to connect to {new_provider.value}")
            return False

        # Transfer all subscriptions
        async with self._sync_lock:
            symbols_to_transfer = list(self._subscriptions.keys())

        logger.info(f"Transferring {len(symbols_to_transfer)} symbols to {new_provider.value}")

        new_provider_instance = self._providers[new_provider]
        for symbol in symbols_to_transfer:
            try:
                await new_provider_instance.subscribe(symbol)
                logger.debug(f"✓ Transferred {symbol} to {new_provider.value}")
            except Exception as e:
                logger.error(f"✗ Failed to transfer {symbol} to {new_provider.value}: {e}")

        # Update active provider
        self._active = new_provider

        # Disconnect old provider
        await self._disconnect_provider(old_provider)

        # Publish event
        await self._dispatcher.publish(
            ProviderChangedEvent(previous=old_provider, current=new_provider)
        )

        logger.success(f"Switched to {new_provider.value}")
        return True

    async def _health_check_loop(self) -> None:
        """Periodically check provider health and failover if needed."""
        while self._running:
            try:
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
                
                async with self._active_lock:
                    current_provider = self.active_provider
                    
                    if not self._is_provider_healthy(current_provider):
                        logger.warning(f"Active provider {self._active.value} disconnected")
                        await self._handle_provider_failure()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in health check loop: {e}")

    async def _handle_provider_failure(self) -> None:
        """Handle provider failure by switching to next available provider."""
        # Determine next provider in hierarchy
        if self._active == self._primary:
            next_provider = self._fallback
        elif self._active == self._fallback:
            next_provider = self._disaster
        else:
            # Already on disaster, try to go back to fallback
            next_provider = self._fallback

        # Try to switch
        if await self._switch_provider(next_provider):
            # Start reconnection task if not using primary
            if not self.is_using_primary and self._reconnect_task is None:
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        else:
            logger.critical("All providers failed")

    async def _reconnect_loop(self) -> None:
        """Continuously try to reconnect to the primary provider."""
        logger.info(f"Starting reconnection attempts to primary provider {self._primary.value}")

        while self._running and not self.is_using_primary:
            try:
                await asyncio.sleep(self.RECONNECT_DELAY)

                # Try to connect to primary
                primary_instance = self._providers.get(self._primary)
                if primary_instance and not self._is_provider_healthy(primary_instance):
                    if await self._try_connect(self._primary):
                        # Wait for grace period
                        await asyncio.sleep(self.GRACE_PERIOD)
                        
                        # Re-check if primary is actually healthy with data
                        if not self._is_provider_healthy(primary_instance):
                            logger.warning(f"Primary provider {self._primary.value} connected but no data flow after grace period")
                            await self._disconnect_provider(self._primary)
                            continue
                        
                        # Switch back to primary
                        async with self._active_lock:
                            if self._is_provider_healthy(primary_instance):
                                if await self._switch_provider(self._primary):
                                    logger.success(f"Successfully switched back to primary {self._primary.value}")
                                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in reconnect loop: {e}")

        logger.info("Reconnection loop stopped")


