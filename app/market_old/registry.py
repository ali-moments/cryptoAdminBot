"""
Provider registry for managing market data provider instances.

This module provides a factory for creating and managing provider instances.
It ensures proper dependency injection and configuration.
"""
from app.database.enums import Provider
from app.market.dispatcher import EventDispatcher
from app.market.providers.base import BaseProvider
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider


class ProviderRegistry:
    """
    Registry for creating and managing market data providers.
    
    Provides a centralized way to instantiate providers with proper
    dependency injection.
    """

    @staticmethod
    def create_provider(
        provider_type: Provider,
        dispatcher: EventDispatcher,
    ) -> BaseProvider:
        """
        Create a provider instance.
        
        Args:
            provider_type: The type of provider to create
            dispatcher: Event dispatcher for price updates
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: If provider type is not supported
        """
        if provider_type == Provider.BINANCE:
            return BinanceProvider(dispatcher)
        elif provider_type == Provider.BYBIT:
            return BybitProvider(dispatcher)
        elif provider_type == Provider.OKX:
            return OKXProvider(dispatcher)
        else:
            raise ValueError(f"Unsupported provider type: {provider_type}")

    @staticmethod
    def create_all_providers(
        dispatcher: EventDispatcher,
    ) -> dict[Provider, BaseProvider]:
        """
        Create instances of all supported providers.
        
        Args:
            dispatcher: Event dispatcher for price updates
            
        Returns:
            Dictionary mapping provider enum to provider instance
        """
        return {
            Provider.BINANCE: BinanceProvider(dispatcher),
            Provider.BYBIT: BybitProvider(dispatcher),
            Provider.OKX: OKXProvider(dispatcher),
        }

    @staticmethod
    def get_supported_providers() -> list[Provider]:
        """Get list of all supported providers."""
        return [Provider.BINANCE, Provider.BYBIT, Provider.OKX]
