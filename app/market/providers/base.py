from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
import asyncio
import aiohttp
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.market.events import PriceUpdatedEvent


class BaseProvider(ABC):
    """
    Polls one exchange's full-market REST ticker endpoint on a fixed
    interval and writes prices directly into PriceCache.

    Per poll cycle:
      - fetch ALL tickers in one request (no symbol filter)
      - for each symbol we currently need from this provider:
          - present in response -> write to cache, reset miss counter
          - absent from response -> increment miss counter, leave cache alone
      - on request failure -> every required symbol counts as a miss

    No WebSockets. No subscriptions. No event bus. Just: poll -> diff -> write.
    """

    def __init__(
        self,
        cache: PriceCache,
        polling_interval: float,
        request_timeout: float = 10.0,
    ) -> None:
        self._cache = cache
        self._polling_interval = polling_interval
        self._request_timeout = request_timeout

        self._polling_active = False
        self._polling_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_poll_time: Optional[datetime] = None

        self._consecutive_misses: dict[str, int] = {}
        self._required_symbols: set[str] = set()

    @property
    @abstractmethod
    def name(self) -> Provider:
        raise NotImplementedError

    def get_consecutive_misses(self, symbol: str) -> int:
        return self._consecutive_misses.get(symbol, 0)

    def update_required_symbols(self, symbols: set[str]) -> None:
        self._required_symbols = symbols.copy()

    async def start_polling(self) -> None:
        if self._polling_task is not None:
            return
        timeout = aiohttp.ClientTimeout(total=self._request_timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._polling_active = True
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info(f"{self.name.value} started polling every {self._polling_interval}s")

    async def stop_polling(self) -> None:
        self._polling_active = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info(f"{self.name.value} stopped polling")

    async def _polling_loop(self) -> None:
        while self._polling_active:
            try:
                all_ticks = await self.fetch_all_tickers()
                self._last_poll_time = datetime.now(timezone.utc)
                received = {tick.symbol: tick for tick in all_ticks}

                for symbol in self._required_symbols:
                    if symbol in received:
                        self._consecutive_misses[symbol] = 0
                        await self._cache.on_price_updated(PriceUpdatedEvent(tick=received[symbol]))
                    else:
                        self._consecutive_misses[symbol] = self._consecutive_misses.get(symbol, 0) + 1
                        logger.debug(
                            f"{self.name.value}: {symbol} missing "
                            f"(miss count: {self._consecutive_misses[symbol]})"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{self.name.value} poll failed: {e}")
                for symbol in self._required_symbols:
                    self._consecutive_misses[symbol] = self._consecutive_misses.get(symbol, 0) + 1

            await asyncio.sleep(self._polling_interval)

    @abstractmethod
    async def fetch_all_tickers(self) -> list[PriceTick]:
        """
        Fetch ALL tickers from the exchange in one request (no symbol
        filter param). Returned PriceTick.symbol MUST be normalized to
        canonical no-hyphen format, e.g. "BTCUSDT".
        """
        raise NotImplementedError