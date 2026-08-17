import asyncio
import aiohttp
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
    
    Supports per-symbol provider selection: symbols can be distributed across
    multiple providers simultaneously. If a symbol is unavailable on the primary
    provider, it will automatically be routed to an available fallback provider.

    Failover hierarchy:
    1. Primary (Binance) - preferred provider
    2. Fallback (Bybit) - used when primary fails or doesn't support a symbol
    3. Disaster (OKX) - used when both primary and fallback fail

    Recovery strategy:
    - Always attempts to reconnect to primary (Binance)
    - Provider-level failures trigger failover for affected symbols only
    - Symbols on other providers remain unaffected during failover
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
        
        # NEW: Track which provider owns which symbol
        self._symbol_providers: dict[str, Provider] = {}
        
        # NEW: Protect concurrent subscription modifications
        self._sync_lock = asyncio.Lock()

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
        """Subscribe to a symbol. Uses reference counting for multiple subscriptions.
        
        Routes symbols to providers intelligently:
        - First checks if symbol already has an assigned provider
        - If not, tries providers in preference order (primary first)
        - Automatically falls back if primary doesn't support the symbol
        """
        async with self._sync_lock:
            count = self._subscriptions.get(symbol, 0)
            
            if count == 0:
                # First subscription - route to appropriate provider
                if symbol in self._symbol_providers:
                    # Symbol already has an assigned provider (e.g., from previous subscription)
                    provider_enum = self._symbol_providers[symbol]
                    provider = self._providers[provider_enum]
                    
                    if provider.is_connected:
                        try:
                            await provider.subscribe(symbol)
                            logger.debug(f"Subscribed {symbol} to existing provider {provider_enum.value}")
                        except Exception as e:
                            logger.error(f"Failed to subscribe {symbol} to {provider_enum.value}: {e}")
                            # Provider failed, try others
                            del self._symbol_providers[symbol]
                            # Release lock before network I/O in helper
                            self._sync_lock.release()
                            try:
                                if not await self._try_subscribe_symbol(symbol):
                                    raise RuntimeError(f"No provider supports {symbol}")
                            finally:
                                await self._sync_lock.acquire()
                    else:
                        # Assigned provider is disconnected, reassign
                        logger.warning(f"Assigned provider {provider_enum.value} for {symbol} is disconnected, reassigning")
                        del self._symbol_providers[symbol]
                        # Release lock before network I/O in helper
                        self._sync_lock.release()
                        try:
                            if not await self._try_subscribe_symbol(symbol):
                                raise RuntimeError(f"No provider supports {symbol}")
                        finally:
                            await self._sync_lock.acquire()
                else:
                    # New symbol - try providers in preference order
                    # Release lock before network I/O in helper
                    self._sync_lock.release()
                    try:
                        if not await self._try_subscribe_symbol(symbol, preferred_provider=self._active):
                            raise RuntimeError(f"No provider supports {symbol}")
                    finally:
                        await self._sync_lock.acquire()
            
            self._subscriptions[symbol] = count + 1

    async def unsubscribe(
        self,
        symbol: str,
    ) -> None:
        """Unsubscribe from a symbol. Uses reference counting.
        
        Unsubscribes from the specific provider that owns this symbol.
        """
        async with self._sync_lock:
            count = self._subscriptions.get(symbol)
            
            if count is None:
                return
            
            if count == 1:
                # Last subscription - actually unsubscribe from owning provider
                provider_enum = self._symbol_providers.get(symbol)
                if provider_enum:
                    provider = self._providers[provider_enum]
                    try:
                        await provider.unsubscribe(symbol)
                        logger.debug(f"Unsubscribed {symbol} from {provider_enum.value}")
                    except Exception as e:
                        logger.error(f"Failed to unsubscribe {symbol} from {provider_enum.value}: {e}")
                    
                    del self._symbol_providers[symbol]
                
                del self._subscriptions[symbol]
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

        # Get current subscriptions under lock
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

            if missing_symbols:
                logger.debug(f"Subscribing to: {sorted(missing_symbols)}")
            if unused_symbols:
                logger.debug(f"Unsubscribing from: {sorted(unused_symbols)}")

        # Release lock before network I/O
        # Subscribe to missing symbols (uses subscribe() which handles locking internally)
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

        # Final state check
        async with self._sync_lock:
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

    async def _try_subscribe_symbol(
        self,
        symbol: str,
        preferred_provider: Provider | None = None
    ) -> bool:
        """Try to subscribe symbol to a provider, trying multiple providers if needed.
        
        Returns True if successful, False if no provider supports this symbol.
        
        This method should be called WITHOUT holding self._sync_lock to avoid
        blocking during network I/O. It updates self._symbol_providers when successful.
        """
        # Determine provider order
        if preferred_provider and preferred_provider in self._providers:
            providers_to_try = [preferred_provider, self._primary, self._fallback, self._disaster]
        else:
            providers_to_try = [self._primary, self._fallback, self._disaster]
        
        # Remove duplicates while preserving order
        seen = set()
        providers_to_try = [
            p for p in providers_to_try
            if p not in seen and not seen.add(p)
        ]
        
        for provider_enum in providers_to_try:
            provider = self._providers[provider_enum]
            
            # Skip disconnected providers
            if not provider.is_connected:
                logger.debug(f"{provider_enum.value} not connected, skipping for {symbol}")
                continue
            
            # Check if provider supports symbol using REST API with timeout
            try:
                # Add timeout to prevent indefinite blocking
                await asyncio.wait_for(provider.current_price(symbol), timeout=5.0)
                # Symbol exists on this provider
                logger.debug(f"{provider_enum.value} supports {symbol}")
            except asyncio.TimeoutError:
                logger.warning(f"{provider_enum.value} availability check timed out for {symbol}")
                continue
            except aiohttp.ClientResponseError as e:
                if e.status == 400:
                    # Bad symbol - provider doesn't support it
                    logger.debug(f"{provider_enum.value} does not support {symbol} (HTTP 400)")
                    continue
                else:
                    # Other HTTP error - provider might be having issues
                    logger.warning(f"{provider_enum.value} API error checking {symbol}: HTTP {e.status}")
                    continue
            except Exception as e:
                logger.warning(f"{provider_enum.value} failed availability check for {symbol}: {e}")
                continue
            
            # Try to subscribe via WebSocket with timeout
            try:
                await asyncio.wait_for(provider.subscribe(symbol), timeout=10.0)
                # Update mapping under lock
                async with self._sync_lock:
                    self._symbol_providers[symbol] = provider_enum
                logger.info(f"✓ Subscribed {symbol} to {provider_enum.value}")
                return True
            except asyncio.TimeoutError:
                logger.warning(f"✗ Subscription timeout for {symbol} to {provider_enum.value}")
                continue
            except Exception as e:
                logger.warning(f"✗ Failed to subscribe {symbol} to {provider_enum.value}: {e}")
                continue
        
        # No provider could subscribe this symbol
        logger.error(f"✗ No provider supports {symbol}")
        return False

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
        """Switch provider for connection-level failure.
        
        Only transfers symbols from the failed provider.
        Symbols on other providers remain untouched.
        """
        old_provider = self._active
        
        if new_provider == old_provider:
            return True
        
        logger.info(f"Switching from {old_provider.value} to {new_provider.value}")
        
        # Identify which symbols need to move (only those on failed provider)
        async with self._sync_lock:
            symbols_to_transfer = [
                symbol for symbol, provider in self._symbol_providers.items()
                if provider == old_provider
            ]
        
        if not symbols_to_transfer:
            logger.info("No symbols to transfer (no symbols on failed provider)")
            # Still update _active for default provider selection
            self._active = new_provider
            return True
        
        # Connect to new provider
        if not await self._try_connect(new_provider):
            logger.error(f"Failed to connect to {new_provider.value}")
            return False
        
        # Transfer affected symbols
        logger.info(f"Transferring {len(symbols_to_transfer)} symbols from {old_provider.value} to {new_provider.value}")
        failed_transfers = []
        
        for symbol in symbols_to_transfer:
            # Get reference count before removal
            async with self._sync_lock:
                ref_count = self._subscriptions.get(symbol, 0)
            
            if ref_count == 0:
                continue
            
            # Unsubscribe from old provider (best effort)
            try:
                old_provider_instance = self._providers[old_provider]
                await old_provider_instance.unsubscribe(symbol)
            except Exception as e:
                logger.warning(f"Failed to unsubscribe {symbol} from {old_provider.value}: {e}")
            
            # Subscribe to new provider
            new_provider_instance = self._providers[new_provider]
            try:
                await new_provider_instance.subscribe(symbol)
                async with self._sync_lock:
                    self._symbol_providers[symbol] = new_provider
                logger.debug(f"✓ Transferred {symbol} to {new_provider.value}")
            except Exception as e:
                logger.error(f"✗ Failed to transfer {symbol} to {new_provider.value}: {e}")
                failed_transfers.append(symbol)
                # Remove from mapping - will be retried in next sync cycle
                async with self._sync_lock:
                    if symbol in self._symbol_providers:
                        del self._symbol_providers[symbol]
        
        if failed_transfers:
            logger.warning(f"Failed to transfer {len(failed_transfers)} symbols: {failed_transfers}")
        
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
