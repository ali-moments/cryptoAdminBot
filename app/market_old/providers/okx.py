import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import aiohttp
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider


class OKXProvider(BaseProvider):
    REST_URL = "https://www.okx.com/api/v5/market/tickers"

    def __init__(
        self,
        dispatcher,
        polling_interval: float = 5.0,  # OKX conservative rate limits
    ) -> None:
        super().__init__(dispatcher, polling_interval)

    @staticmethod
    def _normalize_from_okx(inst_id: str) -> str:
        """
        Convert OKX instrument ID (BTC-USDT-SWAP) to standard format (BTCUSDT).
        """
        # Remove -SWAP suffix and dashes
        parts = inst_id.split("-")
        if len(parts) >= 2:
            return f"{parts[0]}{parts[1]}"
        return inst_id

    @staticmethod
    def _normalize_to_okx(symbol: str) -> str:
        """
        Convert standard format (BTCUSDT) to OKX instrument ID (BTC-USDT-SWAP).
        For perpetual futures, we use the SWAP suffix.
        """
        symbol = symbol.upper()

        # If already in OKX format, return as-is
        if "-" in symbol:
            return symbol

        # Convert BTCUSDT -> BTC-USDT-SWAP
        if symbol.endswith("USDT"):
            base = symbol[:-4]  # Remove "USDT"
            return f"{base}-USDT-SWAP"
        elif symbol.endswith("USDC"):
            base = symbol[:-4]  # Remove "USDC"
            return f"{base}-USDC-SWAP"

        # Fallback: assume it's base-USDT
        return f"{symbol}-USDT-SWAP"

    @property
    def name(
        self,
    ) -> Provider:
        return Provider.OKX

    async def fetch_all_tickers(self) -> list[PriceTick]:
        """Fetch all SWAP tickers from OKX API"""
        if self._session is None:
            raise RuntimeError("Provider session not initialized")

        async with self._session.get(
            self.REST_URL,
            params={
                "instType": "SWAP",  # Get all perpetual swap contracts
            },
        ) as response:
            response.raise_for_status()
            data = await response.json()

        ticks = []
        for ticker in data["data"]:
            # Convert OKX instrument ID back to standard format
            normalized_symbol = self._normalize_from_okx(ticker["instId"])
            
            ticks.append(PriceTick(
                provider=self.name,
                symbol=normalized_symbol,
                price=Decimal(ticker["last"]),
                timestamp=datetime.fromtimestamp(
                    int(ticker["ts"]) / 1000,
                    UTC,
                ),
            ))

        logger.debug(f"OKX: Fetched {len(ticks)} tickers")
        return ticks

    async def current_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

        normalized_symbol = symbol.upper()
        okx_symbol = self._normalize_to_okx(normalized_symbol)

        async with self._session.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={
                "instId": okx_symbol,
            },
        ) as response:
            response.raise_for_status()
            data = await response.json()

        result = data["data"][0]

        return PriceTick(
            provider=self.name,
            symbol=normalized_symbol,  # Return normalized symbol
            price=Decimal(result["last"]),
            timestamp=datetime.now(UTC),
        )
