import asyncio
from collections import defaultdict
from typing import Set
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.providers.base import BaseProvider


class ProviderManager:
    """
    Per-symbol provider manager with automatic failover.

    Binance (primary) is always preferred. If a symbol misses
    `consecutive_miss_threshold` polls in a row on its current provider,
    THAT SYMBOL ALONE reroutes to the next provider: Binance -> Bybit ->
    OKX -> stays on OKX.

    Binance always polls the FULL required symbol set (even for symbols
    currently routed elsewhere) so it can detect recovery. Bybit/OKX only
    poll the symbols currently routed to them.

    No events, no callbacks — a simple 1s loop checks miss counts and
    switches routing directly.
    """

    def __init__(
        self,
        cache: PriceCache,
        providers: dict[Provider, BaseProvider],
        primary: Provider = Provider.BINANCE,
        fallback: Provider = Provider.BYBIT,
        disaster: Provider = Provider.OKX,
        consecutive_miss_threshold: int = 2,
        check_interval: float = 1.0,
    ) -> None:
        self._cache = cache
        self._providers = providers
        self._primary = primary
        self._fallback = fallback
        self._disaster = disaster
        self._consecutive_miss_threshold = consecutive_miss_threshold
        self._check_interval = check_interval

        self._symbol_providers: dict[str, Provider] = {}
        self._required_symbols: Set[str] = set()

        self._lock = asyncio.Lock()
        self._running = False
        self._check_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        for provider_enum, provider in self._providers.items():
            await provider.start_polling()
            logger.info(f"Started {provider_enum.value} provider")

        self._check_task = asyncio.create_task(self._check_loop())
        logger.success("ProviderManager started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
            self._check_task = None

        for provider_enum, provider in self._providers.items():
            await provider.stop_polling()
            logger.info(f"Stopped {provider_enum.value} provider")

        logger.info("ProviderManager stopped")

    async def sync(self, required_symbols: Set[str]) -> None:
        """Update the set of symbols we care about. New symbols start on primary."""
        async with self._lock:
            self._required_symbols = set(required_symbols)

            for symbol in required_symbols:
                if symbol not in self._symbol_providers:
                    self._symbol_providers[symbol] = self._primary

            stale = set(self._symbol_providers) - self._required_symbols
            for symbol in stale:
                del self._symbol_providers[symbol]

            self._push_symbols_to_providers()

    def _push_symbols_to_providers(self) -> None:
        by_provider: dict[Provider, set[str]] = defaultdict(set)
        for symbol, provider in self._symbol_providers.items():
            by_provider[provider].add(symbol)

        for provider_enum, provider in self._providers.items():
            if provider_enum == self._primary:
                provider.update_required_symbols(set(self._required_symbols))
            else:
                provider.update_required_symbols(by_provider.get(provider_enum, set()))

    async def _check_loop(self) -> None:
        while self._running:
            try:
                await self._check_and_switch()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Provider failover check failed, continuing")
            await asyncio.sleep(self._check_interval)

    async def _check_and_switch(self) -> None:
        switches: list[tuple[str, Provider, Provider]] = []

        async with self._lock:
            for symbol, current in self._symbol_providers.items():
                misses = self._providers[current].get_consecutive_misses(symbol)

                if misses >= self._consecutive_miss_threshold:
                    next_provider = self._next_provider(current)
                    if next_provider != current:
                        switches.append((symbol, current, next_provider))
                elif current != self._primary:
                    primary_misses = self._providers[self._primary].get_consecutive_misses(symbol)
                    if primary_misses == 0:
                        switches.append((symbol, current, self._primary))

            if switches:
                for symbol, old, new in switches:
                    self._symbol_providers[symbol] = new
                    if new == self._primary:
                        logger.info(f"{symbol}: recovered, {old.value} -> {new.value}")
                    else:
                        logger.warning(f"{symbol}: failing on {old.value}, switching to {new.value}")
                self._push_symbols_to_providers()

    def _next_provider(self, failed: Provider) -> Provider:
        if failed == self._primary:
            return self._fallback
        if failed == self._fallback:
            return self._disaster
        return self._disaster  # already on disaster, stays put

    def get_price(self, symbol: str):
        return self._cache.get(symbol)
