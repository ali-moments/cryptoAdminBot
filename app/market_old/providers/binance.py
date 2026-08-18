import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Dict

import aiohttp
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider


class BinanceProvider(BaseProvider):
    """
    Binance Futures REST provider.
    
    Uses fapi/v2/ticker/price endpoint which returns all symbols.
    Normalizes symbols to "BTCUSDT" format.
    """
    
    REST_URL = "https://fapi.binance.com/fapi/v2/ticker/price"

    def __init__(
        self,
        dispatcher,
        polling_interval: float = 3.0,  # Binance has high rate limits
    ) -> None:
        super().__init__(dispatcher, polling_interval)
        self._session: aiohttp.ClientSession | None = None

    @property
    def name(self) -> Provider:
        return Provider.BINANCE

    async def start_polling(self) -> None:
        """Start the polling loop"""
        if self._polling_task is not None:
            logger.warning(f"{self.name.value} provider already polling")
            return
            
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10.0)
            )
        
        self._stop_event.clear()
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info(f"{self.name.value} provider started polling")

    async def stop_polling(self) -> None:
        """Stop the polling loop and cleanup"""
        if self._polling_task is None:
            return
            
        self._stop_event.set()
        
        try:
            await asyncio.wait_for(self._polling_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        
        self._polling_task = None
        
        if self._session is not None:
            await self._session.close()
            self._session = None
            
        logger.info(f"{self.name.value} provider stopped polling")

    async def fetch_all_tickers(self) -> Dict[str, PriceTick]:
        """
        Fetch all tickers from Binance fapi/v2/ticker/price endpoint.
        
        Returns dict mapping symbol -> PriceTick for efficient lookups.
        Binance symbols are already in "BTCUSDT" format (no normalization needed).
        """
        if self._session is None:
            raise RuntimeError("Provider session not initialized")

        try:
            async with self._session.get(self.REST_URL) as response:
                response.raise_for_status()
                data = await response.json()

            # Convert to dict for efficient symbol lookup
            tickers = {}
            timestamp = datetime.now(UTC)
            
            for ticker_data in data:
                symbol = ticker_data["symbol"]
                # Binance symbols are already in "BTCUSDT" format
                tickers[symbol] = PriceTick(
                    provider=self.name,
                    symbol=symbol,
                    price=Decimal(ticker_data["price"]),
                    timestamp=timestamp,
                )

            logger.debug(f"{self.name.value}: Fetched {len(tickers)} tickers")
            return tickers
            
        except Exception as e:
            logger.error(f"{self.name.value}: Failed to fetch tickers: {e}")
            return {}

    async def _polling_loop(self) -> None:
        """Main polling loop that fetches and processes tickers"""
        while not self._stop_event.is_set():
            try:
                # Get all tickers from exchange
                received_tickers = await self.fetch_all_tickers()
                
                if not received_tickers:
                    logger.warning(f"{self.name.value}: Empty response from API")
                    # Still update miss counts for required symbols
                    for symbol in list(self._required_symbols):
                        self._update_miss_count(symbol, found=False)
                else:
                    # Update price cache and miss counts for required symbols only
                    for symbol in list(self._required_symbols):
                        if symbol in received_tickers:
                            # Symbol found - update cache and reset miss count
                            await self._cache.set(received_tickers[symbol])
                            self._update_miss_count(symbol, found=True)
                        else:
                            # Symbol missing - increment miss count
                            self._update_miss_count(symbol, found=False)
                            
                    logger.debug(
                        f"{self.name.value}: Updated {len(self._required_symbols)} required symbols, "
                        f"received {len(received_tickers)} total tickers"
                    )

            except Exception as e:
                logger.error(f"{self.name.value}: Polling error: {e}")
                # Mark all required symbols as missed on polling error
                for symbol in list(self._required_symbols):
                    self._update_miss_count(symbol, found=False)

            # Wait for next poll cycle
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), 
                    timeout=self._polling_interval
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                continue  # Continue to next poll cycle