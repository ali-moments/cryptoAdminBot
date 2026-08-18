from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.providers.base import BaseProvider
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider


class ProviderRegistry:
    @staticmethod
    def create_all_providers(
        cache: PriceCache,
        polling_intervals: dict[Provider, float] | None = None,
    ) -> dict[Provider, BaseProvider]:
        intervals = polling_intervals or {}
        return {
            Provider.BINANCE: BinanceProvider(cache, intervals.get(Provider.BINANCE, 5.0)),
            Provider.BYBIT: BybitProvider(cache, intervals.get(Provider.BYBIT, 5.0)),
            Provider.OKX: OKXProvider(cache, intervals.get(Provider.OKX, 5.0)),
        }

    @staticmethod
    def get_supported_providers() -> list[Provider]:
        return [Provider.BINANCE, Provider.BYBIT, Provider.OKX]