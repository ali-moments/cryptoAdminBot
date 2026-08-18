from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider

OKX_TICKER_URL = "https://www.okx.com/api/v5/market/tickers"


def _normalize_okx_symbol(inst_id: str) -> str:
    """OKX uses 'BTC-USDT-SWAP'. Canonical internal format is 'BTCUSDT'."""
    parts = inst_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}{parts[1]}"
    return inst_id.replace("-", "")


class OKXProvider(BaseProvider):
    @property
    def name(self) -> Provider:
        return Provider.OKX

    async def fetch_all_tickers(self) -> list[PriceTick]:
        now = datetime.now(timezone.utc)
        async with self._session.get(OKX_TICKER_URL, params={"instType": "SWAP"}) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data.get('msg')}")

        ticks: list[PriceTick] = []
        for item in data.get("data", []):
            try:
                ticks.append(PriceTick(
                    symbol=_normalize_okx_symbol(item["instId"]),
                    price=Decimal(item["last"]),
                    provider=Provider.OKX,
                    timestamp=now,
                ))
            except (KeyError, ValueError) as e:
                logger.warning(f"OKX: skipping malformed ticker {item}: {e}")
        return ticks