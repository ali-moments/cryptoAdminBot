import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Set
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.events import ProviderConnectedEvent, ProviderChangedEvent
from app.market.providers.base import BaseProvider


class ProviderManager:
    """
    Per-symbol provider manager with automatic failover.
    
    Each symbol can be routed to different providers independently:
    1. Primary (Binance) - preferred provider for all symbols
    2. Fallback (Bybit) - used when primary fails for specific symbols  
    3. Disaster (OKX) - used when both primary and fallback fail for specific symbols
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        cache: PriceCache,
        providers: dict[Provider, BaseProvider],
        primary: Provider = Provider.BINANCE,
        fallback: Provider = Provider.BYBIT,
        disaster: Provider = Provider.OKX,
        consecutive_miss_threshold: int = 2,
    ) -> None:
        logger.info(f"Creating ProviderManager with primary: {primary.value}")
        
        self._dispatcher = dispatcher
        self._cache = cache
        self._providers = providers
        self._primary = primary
        self._fallback = fallback
        self._disaster = disaster
        self._consecutive_miss_threshold = consecutive_miss_threshold

        # Per-symbol provider routing
        self._symbol_providers: dict[str, Provider] = {}  # symbol -> current provider
        self._required_symbols: Set[str] = set()
        
        self._sync_lock = asyncio.Lock()
        self._running = False

    @property
    def active_provider(self) -> BaseProvider:
        """Get the primary provider (for backward compatibility)"""
        return self._providers[self._primary]

    @property
    def active_provider_name(self) -> Provider:
        """Get the primary provider name (for backward compatibility)"""
        return self._primary

    @property
    def is_using_primary(self) -> bool:
        """Check if all symbols are using primary provider"""
        return all(
            provider == self._primary 
            for provider in self._symbol_providers.values()
        )

    async def start(self) -> None:
        """Start all providers and set up failure callbacks"""
        if self._running:
            logger.warning("ProviderManager already running")
            return

        self._running = True
        logger.info("Starting ProviderManager")

        # Start all providers and set up failure callbacks
        for provider_enum, provider_instance in self._providers.items():
            try:
                # Set up failure callback before starting
                provider_instance.set_failure_callback(self._on_provider_failure)
                await provider_instance.start_polling()
                logger.info(f"Started {provider_enum.value} provider")
            except Exception as e:
                logger.error(f"Failed to start {provider_enum.value}: {e}")

        logger.success("ProviderManager started")

    async def stop(self) -> None:
        """Stop all providers"""
        if not self._running:
            return

        logger.info("Stopping ProviderManager")
        self._running = False

        # Stop all providers
        for provider_enum, provider_instance in self._providers.items():
            try:
                await provider_instance.stop_polling()
                logger.info(f"Stopped {provider_enum.value} provider")
            except Exception as e:
                logger.error(f"Failed to stop {provider_enum.value}: {e}")

        logger.info("ProviderManager stopped")

    async def subscribe(self, symbol: str) -> None:
        """Subscribe to a symbol (adds it to required symbols)"""
        async with self._sync_lock:
            # Add to required symbols and route to primary provider initially
            self._required_symbols.add(symbol)
            if symbol not in self._symbol_providers:
                self._symbol_providers[symbol] = self._primary

            await self._update_provider_symbols()
            logger.debug(f"Subscribed {symbol} -> {self._symbol_providers[symbol].value}")

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from a symbol (removes it from required symbols)"""
        async with self._sync_lock:
            self._required_symbols.discard(symbol)
            self._symbol_providers.pop(symbol, None)
            
            await self._update_provider_symbols()
            logger.debug(f"Unsubscribed {symbol}")

    async def sync(self, required_symbols: Set[str]) -> None:
        """Synchronize required symbols set and check for failures"""
        async with self._sync_lock:
            self._required_symbols = required_symbols.copy()
            
            # Initialize routing for new symbols to primary provider
            for symbol in required_symbols:
                if symbol not in self._symbol_providers:
                    self._symbol_providers[symbol] = self._primary
            
            # Remove routing for symbols no longer needed
            symbols_to_remove = set(self._symbol_providers.keys()) - required_symbols
            for symbol in symbols_to_remove:
                del self._symbol_providers[symbol]

            await self._update_provider_symbols()
            
        # Check for failures after updating provider symbols (outside lock to avoid deadlock)
        await self._check_and_handle_failures()

    async def _update_provider_symbols(self) -> None:
        """Update each provider's required symbols set"""
        # Group symbols by provider
        provider_symbols = defaultdict(set)
        for symbol, provider in self._symbol_providers.items():
            provider_symbols[provider].add(symbol)

        # Update each provider
        for provider_enum, provider_instance in self._providers.items():
            symbols_for_provider = provider_symbols.get(provider_enum, set())
            provider_instance.update_required_symbols(symbols_for_provider)

    async def _check_and_handle_failures(self) -> None:
        """Check for symbol failures and route them to fallback providers"""
        symbols_switched = False
        symbols_to_check = None
        
        # Get current symbol state (read-only, so safe to copy)
        async with self._sync_lock:
            symbols_to_check = list(self._required_symbols)
        
        switches_to_make = []
        
        for symbol in symbols_to_check:
            current_provider_enum = self._symbol_providers.get(symbol)
            if not current_provider_enum:
                continue
                
            current_provider = self._providers[current_provider_enum]
            miss_count = current_provider.get_consecutive_misses(symbol)
            
            if miss_count >= self._consecutive_miss_threshold:
                # Symbol is failing on current provider, try to switch
                new_provider = self._get_next_provider(current_provider_enum)
                
                if new_provider != current_provider_enum:
                    switches_to_make.append((symbol, current_provider_enum, new_provider, miss_count))
            else:
                # Check if we can switch back to a preferred provider
                preferred_provider = self._get_preferred_provider_for_symbol(symbol)
                if (preferred_provider != current_provider_enum and 
                    self._providers[preferred_provider].get_consecutive_misses(symbol) == 0):
                    
                    switches_to_make.append((symbol, current_provider_enum, preferred_provider, 0))

        # Apply switches (if any) under lock
        if switches_to_make:
            async with self._sync_lock:
                for symbol, old_provider, new_provider, miss_count in switches_to_make:
                    if miss_count >= self._consecutive_miss_threshold:
                        logger.warning(
                            f"Symbol {symbol} failed {miss_count} times on {old_provider.value}, "
                            f"switching to {new_provider.value}"
                        )
                    else:
                        logger.info(
                            f"Symbol {symbol} switching back from {old_provider.value} to "
                            f"preferred {new_provider.value}"
                        )
                    
                    self._symbol_providers[symbol] = new_provider
                    symbols_switched = True
                    
                    # Publish provider change event for this symbol
                    await self._dispatcher.publish(
                        ProviderChangedEvent(
                            previous=old_provider,
                            current=new_provider,
                        )
                    )

                if symbols_switched:
                    await self._update_provider_symbols()

    async def _on_provider_failure(self) -> None:
        """Callback from providers when they detect failures - trigger immediate failure check"""
        if self._running:
            await self._check_and_handle_failures()

    def _get_next_provider(self, failed_provider: Provider) -> Provider:
        """Get the next provider in the hierarchy for a failed provider"""
        if failed_provider == self._primary:
            return self._fallback
        elif failed_provider == self._fallback:
            return self._disaster
        else:
            # Already on disaster (OKX) - stay on disaster, no more switching
            # Symbol will remain stuck until OKX recovers
            return self._disaster

    def _get_preferred_provider_for_symbol(self, symbol: str) -> Provider:
        """Get the most preferred provider for a symbol (currently always primary)"""
        return self._primary

    def get_price(self, symbol: str) -> "PriceTick | None":
        """Get the latest price for a symbol from the cache"""
        return self._cache.get(symbol)