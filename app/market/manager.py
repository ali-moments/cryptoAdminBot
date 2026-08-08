import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.dto import PriceTick
from app.market.events import ProviderChangedEvent, ProviderConnectedEvent, ProviderDisconnectedEvent
from app.market.providers.base import BaseProvider


class ProviderManager:
    """
    Manages market data providers with automatic failover and recovery.

    Failover hierarchy:
    1. Primary (Binance) - main provider
    2. Fallback (Bybit) - used when primary fails
    3. Disaster (OKX) - used when both primary and fallback fail

    Recovery strategy:
    - Always attempts to reconnect to primary (Binance)
    - When primary recovers, switches back automatically
    - Transfers all subscriptions during provider switch
    """

    # Reconnection settings
    RECONNECT_DELAY = 5  # seconds between reconnection attempts
    HEALTH_CHECK_INTERVAL = 10  # seconds between health checks

    def __init__(
        self,
        dispatcher: EventDispatcher,
        cache: PriceCache,
        providers: dict[Provider, BaseProvider],
        primary: Provider = Provider.BINANCE,
        fallback: Provider = Provider.BYBIT,
        disaster: Provider = Provider.OKX,
    ) -> None:
        self._dispatcher = dispatcher
        self._cache = cache

        self._providers = providers

        self._primary = primary
        self._fallback = fallback
        self._disaster = disaster

        self._active = primary
        self._target = primary  # The provider we want to be using

        # Track subscriptions (symbol -> reference count)
        self._subscriptions: dict[str, int] = {}

        # Background tasks
        self._health_check_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

        self._running = False
        self._reconnecting = False

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

    async def start(self) -> None:
        """Start the manager and connect to the primary provider."""
        if self._running:
            logger.warning("ProviderManager already running")
            return

        self._running = True
        self._target = self._primary

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

    async def subscribe(
        self,
        symbol: str,
    ) -> None:
        """Subscribe to a symbol. Uses reference counting for multiple subscriptions."""
        count = self._subscriptions.get(symbol, 0)

        if count == 0:
            # First subscription to this symbol
            await self.active_provider.subscribe(symbol)
            logger.debug(f"Subscribed to {symbol} on {self._active.value}")

        self._subscriptions[symbol] = count + 1

    async def unsubscribe(
        self,
        symbol: str,
    ) -> None:
        """Unsubscribe from a symbol. Uses reference counting."""
        count = self._subscriptions.get(symbol)

        if count is None:
            return

        if count == 1:
            # Last subscription to this symbol
            await self.active_provider.unsubscribe(symbol)
            del self._subscriptions[symbol]
            logger.debug(f"Unsubscribed from {symbol} on {self._active.value}")
            return

        self._subscriptions[symbol] = count - 1

    async def sync(self, required_symbols: set[str]) -> None:
        """Synchronize current subscriptions with required symbol set.

        Args:
            required_symbols: Complete set of symbols that should be subscribed

        Compares current subscriptions with required symbols and:
        - Subscribes to missing symbols
        - Unsubscribes from unused symbols

        Uses existing subscribe/unsubscribe methods to preserve reference counting.
        """
        if not self._running:
            logger.warning("Cannot sync subscriptions: ProviderManager not running")
            return

        # Get current subscriptions
        current_symbols = set(self._subscriptions.keys())

        # Calculate differences
        missing_symbols = required_symbols - current_symbols
        unused_symbols = current_symbols - required_symbols

        # Log sync operation with provider context
        if missing_symbols or unused_symbols:
            logger.info(
                f"SUBSCRIPTION SYNC [{self._active.value}]: +{len(missing_symbols)} -{len(unused_symbols)} "
                f"(current: {len(current_symbols)}, required: {len(required_symbols)})"
            )

            if missing_symbols:
                logger.debug(f"Subscribing to: {sorted(missing_symbols)}")
            if unused_symbols:
                logger.debug(f"Unsubscribing from: {sorted(unused_symbols)}")
        # else:
        #     logger.debug(f"SUBSCRIPTION SYNC [{self._active.value}]: No changes needed (current: {len(current_symbols)} symbols)")

        # Subscribe to missing symbols
        for symbol in missing_symbols:
            try:
                await self.subscribe(symbol)
                logger.debug(f"✓ Subscribed to {symbol}")
            except Exception as e:
                logger.error(f"✗ Failed to subscribe to {symbol}: {e}")

        # Unsubscribe from unused symbols
        for symbol in unused_symbols:
            try:
                await self.unsubscribe(symbol)
                logger.debug(f"✓ Unsubscribed from {symbol}")
            except Exception as e:
                logger.error(f"✗ Failed to unsubscribe from {symbol}: {e}")

        # Final state check
        final_symbols = set(self._subscriptions.keys())
        if final_symbols != required_symbols:
            missing_after = required_symbols - final_symbols
            extra_after = final_symbols - required_symbols
            logger.warning(
                f"SUBSCRIPTION SYNC INCOMPLETE: missing={sorted(missing_after)}, extra={sorted(extra_after)}"
            )

    def get_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
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

            # Wait briefly to verify connection
            await asyncio.sleep(0.5)

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
        """
        Switch from current provider to a new one.
        Transfers all subscriptions to the new provider.
        """
        if new_provider == self._active:
            return True

        old_provider = self._active

        logger.info(f"Switching from {old_provider.value} to {new_provider.value}")

        # Save current subscriptions before switching
        subscriptions_to_transfer = dict(self._subscriptions)

        # Connect to new provider (this sets self._active = new_provider)
        if not await self._try_connect(new_provider):
            logger.error(f"Failed to switch to {new_provider.value}")
            return False

        # Clear ProviderManager subscription state - it will be rebuilt during transfer
        self._subscriptions.clear()

        # Transfer subscriptions to the new provider
        if subscriptions_to_transfer:
            logger.info(f"Transferring {len(subscriptions_to_transfer)} subscriptions to {new_provider.value}")

            failed_subscriptions = []
            for symbol, count in subscriptions_to_transfer.items():
                try:
                    # Subscribe once on the new provider - subscribe() method handles reference counting
                    await self.active_provider.subscribe(symbol)
                    # Restore the reference count in our state
                    self._subscriptions[symbol] = count
                    logger.debug(f"Transferred {symbol} to {new_provider.value} (ref count: {count})")
                except Exception as e:
                    logger.error(f"Failed to transfer {symbol} to {new_provider.value}: {e}")
                    failed_subscriptions.append(symbol)

            if failed_subscriptions:
                logger.warning(f"Failed to transfer {len(failed_subscriptions)} subscriptions: {failed_subscriptions}")

        # Disconnect old provider
        await self._disconnect_provider(old_provider)

        # Publish provider changed event
        await self._dispatcher.publish(
            ProviderChangedEvent(
                previous=old_provider,
                current=new_provider,
            )
        )

        logger.success(f"Switched to {new_provider.value} with {len(self._subscriptions)} active subscriptions")
        return True

    async def _health_check_loop(self) -> None:
        """
        Periodically check the health of the active provider.
        If it disconnects, failover to the next available provider.
        """
        while self._running:
            try:
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

                if not self.active_provider.is_connected:
                    logger.warning(f"Active provider {self._active.value} disconnected")

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

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in health check loop: {e}")

    async def _reconnect_loop(self) -> None:
        """
        Continuously try to reconnect to the primary provider.
        When successful, switch back to primary.
        """
        if self._reconnecting:
            return

        self._reconnecting = True

        logger.info(f"Starting reconnection attempts to primary provider {self._primary.value}")

        while self._running and not self.is_using_primary:
            try:
                await asyncio.sleep(self.RECONNECT_DELAY)

                logger.debug(f"Attempting to reconnect to {self._primary.value}")

                # Try to connect to primary
                primary_instance = self._providers.get(self._primary)
                if primary_instance and not primary_instance.is_connected:
                    if await self._try_connect(self._primary):
                        # Primary is back! Switch to it
                        logger.info(f"Primary provider {self._primary.value} recovered, switching back")
                        if await self._switch_provider(self._primary):
                            logger.success(f"Successfully switched back to primary {self._primary.value}")
                            break
                        else:
                            logger.error("Failed to switch back to primary")
                            # Disconnect the primary we just connected
                            await self._disconnect_provider(self._primary)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in reconnect loop: {e}")

        self._reconnecting = False
        self._reconnect_task = None
        logger.info("Reconnection loop stopped")
