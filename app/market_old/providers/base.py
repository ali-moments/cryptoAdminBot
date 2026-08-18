from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional, Set
import asyncio
import aiohttp
from loguru import logger

from app.database.enums import Provider
from app.market.dispatcher import EventDispatcher
from app.market.dto import PriceTick
from app.market.events import PriceUpdatedEvent


class BaseProvider(ABC):
    """Abstract base provider for market data polling.
    
    Each provider runs its own independent polling loop that:
    1. Fetches all available symbols from the exchange
    2. Updates PriceCache for symbols that are present
    3. Tracks consecutive misses per symbol for failover logic
    4. Does NOT use WebSocket connections, subscriptions, or ping/pong
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        polling_interval: float,
        request_timeout: float = 10.0,
    ) -> None:
        self._dispatcher = dispatcher
        self._polling_interval = polling_interval
        self._request_timeout = request_timeout
        
        # Polling state
        self._polling_active = False
        self._polling_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_poll_time: Optional[datetime] = None
        
        # Per-symbol consecutive miss tracking - key requirement from Part 4
        self._consecutive_misses: dict[str, int] = {}
        
        # Symbols currently required from this provider - set by ProviderManager
        self._required_symbols: Set[str] = set()

    @property
    @abstractmethod
    def name(self) -> Provider:
        """Return the provider enum for this provider"""
        raise NotImplementedError

    def get_consecutive_misses(self, symbol: str) -> int:
        """Get consecutive miss count for a specific symbol on this provider"""
        return self._consecutive_misses.get(symbol, 0)

    def update_required_symbols(self, symbols: Set[str]) -> None:
        """Update the set of symbols this provider should poll for"""
        self._required_symbols = symbols.copy()

    async def start_polling(self) -> None:
        """Start the polling loop for this provider"""
        if self._polling_task is not None:
            return

        # Create HTTP session with timeout
        timeout = aiohttp.ClientTimeout(total=self._request_timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)
        
        self._polling_active = True
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info(f"{self.name.value} provider started polling every {self._polling_interval}s")

    async def stop_polling(self) -> None:
        """Stop the polling loop for this provider"""
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

        logger.info(f"{self.name.value} provider stopped polling")

    async def _polling_loop(self) -> None:
        """Main polling loop - implements Part 3 specification exactly"""
        while self._polling_active:
            try:
                # Step 1: Make the single full-market HTTP GET call
                all_ticks = await self.fetch_all_tickers()
                self._last_poll_time = datetime.now(timezone.utc)
                
                # Step 3: Parse response into dict keyed by symbol for fast lookup
                received_symbols = {tick.symbol: tick for tick in all_ticks}
                
                # Step 4: For each symbol currently required from this provider
                for symbol in self._required_symbols:
                    if symbol in received_symbols:
                        # Present: update PriceCache, reset consecutive-miss counter
                        self._consecutive_misses[symbol] = 0
                        await self._publish_price(received_symbols[symbol])
                    else:
                        # Absent: increment consecutive-miss counter, do NOT update PriceCache
                        self._consecutive_misses[symbol] = self._consecutive_misses.get(symbol, 0) + 1
                        logger.debug(f"{self.name.value}: {symbol} missing from response (miss count: {self._consecutive_misses[symbol]})")

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Step 2: HTTP call failed - treat as total miss for every required symbol
                logger.error(f"{self.name.value} polling failed: {e}")
                for symbol in self._required_symbols:
                    self._consecutive_misses[symbol] = self._consecutive_misses.get(symbol, 0) + 1

            # Step 6: Sleep for configured poll interval, then repeat
            await asyncio.sleep(self._polling_interval)

    @abstractmethod
    async def fetch_all_tickers(self) -> list[PriceTick]:
        """Fetch all available tickers from the exchange.
        
        Must implement the exact API calls specified in Part 2:
        - Binance: GET https://fapi.binance.com/fapi/v2/ticker/price (no params)
        - Bybit: GET https://api.bybit.com/v5/market/tickers?category=linear (no symbol param)  
        - OKX: GET https://www.okx.com/api/v5/market/tickers?instType=SWAP (no instId param)
        
        CRITICAL: Must normalize symbols to canonical format ("BTCUSDT") before 
        returning PriceTick objects. OKX returns "BTC-USDT-SWAP" format but 
        PriceTick.symbol must be "BTCUSDT" to match required_symbols.
        
        Returns list of PriceTick objects for ALL symbols from the exchange
        with normalized symbol names.
        """
        raise NotImplementedError

    async def _publish_price(self, tick: PriceTick) -> None:
        """Publish price update event to update PriceCache"""
        await self._dispatcher.publish(PriceUpdatedEvent(tick=tick))
