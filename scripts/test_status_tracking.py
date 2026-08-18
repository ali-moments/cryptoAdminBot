#!/usr/bin/env python3
"""
Status tracking verification test - specifically checks for the timing bug where
active_provider_name doesn't match the actual provider sending data.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.events import PriceUpdatedEvent
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider
from app.database.enums import Provider
from app.config.logging import setup_logging
from loguru import logger


class StatusTrackingTester:
    def __init__(self):
        self.dispatcher = EventDispatcher()
        self.cache = PriceCache()
        
        # Setup event handling
        self.dispatcher.subscribe(PriceUpdatedEvent, self.cache.on_price_updated)
        
        # Create providers
        self.providers = {
            Provider.BINANCE: BinanceProvider(self.dispatcher),
            Provider.BYBIT: BybitProvider(self.dispatcher),
            Provider.OKX: OKXProvider(self.dispatcher),
        }
        
        # Create manager
        self.manager = ProviderManager(
            dispatcher=self.dispatcher,
            cache=self.cache,
            providers=self.providers,
        )
        
        self.test_symbols = ["BTCUSDT", "ETHUSDT"]
        self.status_checks = []

    async def run_test(self):
        """Run status tracking verification test"""
        logger.info("🧪 STATUS TRACKING VERIFICATION TEST")
        logger.info("=" * 60)
        
        try:
            await self.manager.start()
            
            # Subscribe to symbols
            for symbol in self.test_symbols:
                await self.manager.subscribe(symbol)
            
            # Phase 1: Normal operation - check status consistency
            await self.check_status_consistency("Phase 1: Normal Operation")
            
            # Phase 2: Force failure and check during transition
            logger.info("Forcing Binance failure...")
            binance_instance = self.manager.active_provider
            binance_instance.mark_disconnected()
            
            # Check status immediately after failure
            await self.check_status_consistency("Phase 2: Immediately After Failure")
            
            # Wait for health check (7 second interval)
            await asyncio.sleep(8)
            
            # Check status after health check should have run
            await self.check_status_consistency("Phase 3: After Health Check")
            
            # Wait for data to flow from new provider
            await asyncio.sleep(3)
            
            # Final check - this is where the bug would show
            await self.check_status_consistency("Phase 4: After Data Flow")
            
        except Exception as e:
            logger.error(f"Test failed: {e}")
        finally:
            await self.manager.stop()
        
        self.analyze_results()

    async def check_status_consistency(self, phase_name):
        """Check if reported active provider matches actual data source"""
        logger.info(f"📍 {phase_name}")
        logger.info("-" * 40)
        
        # Get reported active provider
        reported_provider = self.manager.active_provider_name
        logger.info(f"Reported active provider: {reported_provider.value}")
        
        # Check actual data sources in cache
        actual_providers = set()
        symbols_with_data = 0
        
        for symbol in self.test_symbols:
            price = self.cache.get(symbol)
            if price:
                actual_providers.add(price.provider)
                symbols_with_data += 1
                logger.debug(f"  {symbol}: ${price.price} from {price.provider.value}")
        
        logger.info(f"Actual data providers: {[p.value for p in actual_providers]}")
        logger.info(f"Symbols with data: {symbols_with_data}/{len(self.test_symbols)}")
        
        # Record this check
        check_result = {
            'phase': phase_name,
            'reported_provider': reported_provider,
            'actual_providers': list(actual_providers),
            'symbols_with_data': symbols_with_data,
            'consistent': len(actual_providers) == 0 or reported_provider in actual_providers
        }
        
        self.status_checks.append(check_result)
        
        if check_result['consistent']:
            logger.success("✓ Status is consistent")
        else:
            logger.error(f"✗ STATUS BUG: Reported {reported_provider.value} but data from {[p.value for p in actual_providers]}")
        
        logger.info("")

    def analyze_results(self):
        """Analyze all status checks for timing issues"""
        logger.info("=" * 60)
        logger.info("📊 STATUS TRACKING ANALYSIS")
        logger.info("=" * 60)
        
        bugs_found = []
        
        for i, check in enumerate(self.status_checks):
            status = "✅ CONSISTENT" if check['consistent'] else "❌ INCONSISTENT"
            logger.info(f"{i+1}. {check['phase']:<30} {status}")
            
            if not check['consistent']:
                bugs_found.append(check)
                logger.error(f"   Bug: Reported {check['reported_provider'].value}, "
                           f"actual data from {[p.value for p in check['actual_providers']]}")
        
        logger.info("-" * 60)
        
        if bugs_found:
            logger.error(f"🐛 STATUS TRACKING BUGS FOUND: {len(bugs_found)}")
            logger.error("The active_provider_name doesn't match actual data providers")
            
            # Identify the specific timing issue
            for bug in bugs_found:
                if bug['phase'].startswith("Phase 4"):
                    logger.error("🔍 TIMING BUG: Status not updated after provider switch completed")
                elif bug['phase'].startswith("Phase 2"):
                    logger.warning("⚠ Expected: Status may be stale immediately after failure")
                elif bug['phase'].startswith("Phase 3"):
                    logger.error("🔍 HEALTH CHECK BUG: Status not updated after health check")
        else:
            logger.success("✅ NO STATUS TRACKING BUGS FOUND")
            logger.success("active_provider_name correctly tracks actual data provider")


async def main():
    setup_logging()
    tester = StatusTrackingTester()
    await tester.run_test()


if __name__ == "__main__":
    asyncio.run(main())