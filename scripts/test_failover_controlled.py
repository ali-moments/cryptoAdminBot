#!/usr/bin/env python3
"""
Controlled failover test - simulates provider failure to verify failover logic.

This test exercises the complete failover path:
1. BINANCE active and providing data
2. BINANCE failure simulation 
3. Manager detects failure
4. Manager switches to BYBIT
5. BYBIT provides data
6. PriceCache continues receiving updates
7. Recovery to BINANCE (if implemented)
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from decimal import Decimal

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
from app.market.dto import PriceTick
from app.database.enums import Provider
from app.config.logging import setup_logging
from loguru import logger


class FailoverTester:
    def __init__(self):
        self.dispatcher = EventDispatcher()
        self.cache = PriceCache()
        
        # Setup event handling
        self.dispatcher.subscribe(PriceUpdatedEvent, self.cache.on_price_updated)
        
        # Create providers
        self.binance_provider = BinanceProvider(self.dispatcher)
        self.bybit_provider = BybitProvider(self.dispatcher)
        
        self.providers = {
            Provider.BINANCE: self.binance_provider,
            Provider.BYBIT: self.bybit_provider,
            Provider.OKX: OKXProvider(self.dispatcher),
        }
        
        # Create manager
        self.manager = ProviderManager(
            dispatcher=self.dispatcher,
            cache=self.cache,
            providers=self.providers,
        )
        
        # Test symbols - smaller set for faster testing
        self.test_symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
        
        self.test_results = {
            'phase_1_binance_active': False,
            'phase_1_binance_data': False,
            'phase_2_failure_detected': False,
            'phase_3_bybit_active': False,
            'phase_3_bybit_data': False,
            'phase_4_recovery': False,
            'subscriptions_transferred': False,
            'cache_continuous': False,
        }

    async def run_test(self):
        """Run the complete controlled failover test"""
        logger.info("🧪 CONTROLLED FAILOVER TEST")
        logger.info("=" * 60)
        
        try:
            # Phase 1: Normal Binance operation
            await self.phase_1_binance_normal()
            
            # Phase 2: Simulate Binance failure
            await self.phase_2_simulate_failure()
            
            # Phase 3: Verify Bybit failover
            await self.phase_3_verify_bybit()
            
            # Phase 4: Test recovery (if implemented)
            await self.phase_4_test_recovery()
            
        except Exception as e:
            logger.error(f"Test failed with exception: {e}")
            raise
        finally:
            await self.cleanup()
        
        self.print_results()

    async def phase_1_binance_normal(self):
        """Phase 1: Start with Binance and verify normal operation"""
        logger.info("📍 Phase 1: Normal Binance Operation")
        logger.info("-" * 40)
        
        # Start manager (should connect to Binance as primary)
        await self.manager.start()
        
        # Verify Binance is active
        if self.manager.active_provider_name == Provider.BINANCE:
            self.test_results['phase_1_binance_active'] = True
            logger.success(f"✓ Binance is active provider")
        else:
            logger.error(f"✗ Expected BINANCE, got {self.manager.active_provider_name}")
            return
        
        # Subscribe to test symbols
        for symbol in self.test_symbols:
            await self.manager.subscribe(symbol)
        
        logger.info(f"Subscribed to {len(self.test_symbols)} symbols")
        
        # Wait for data and verify
        await asyncio.sleep(3)
        
        # Check if we received data from Binance
        binance_data_count = 0
        for symbol in self.test_symbols:
            price = self.cache.get(symbol)
            if price and price.provider == Provider.BINANCE:
                binance_data_count += 1
                logger.debug(f"✓ {symbol}: ${price.price} from {price.provider.value}")
        
        if binance_data_count > 0:
            self.test_results['phase_1_binance_data'] = True
            logger.success(f"✓ Received data from Binance for {binance_data_count}/{len(self.test_symbols)} symbols")
        else:
            logger.warning(f"⚠ No Binance data received - may affect failover test")

    async def phase_2_simulate_failure(self):
        """Phase 2: Simulate Binance provider failure"""
        logger.info("📍 Phase 2: Simulate Binance Failure")
        logger.info("-" * 40)
        
        # Force disconnect Binance to simulate failure
        # This exercises the same path as a real failure
        binance_instance = self.manager.active_provider
        
        logger.info("Forcing Binance disconnect to simulate failure...")
        
        # Disconnect the WebSocket and mark as disconnected
        if hasattr(binance_instance, '_ws') and binance_instance._ws:
            await binance_instance._ws.close()
        
        # Mark as disconnected (simulates connection failure)
        binance_instance.mark_disconnected()
        
        logger.info("Binance marked as disconnected")
        
        # Wait for health check to detect the failure
        logger.info("Waiting for health check to detect failure...")
        
        # Wait up to 15 seconds for failover to occur
        for i in range(15):
            await asyncio.sleep(1)
            
            if self.manager.active_provider_name != Provider.BINANCE:
                self.test_results['phase_2_failure_detected'] = True
                logger.success(f"✓ Failure detected after {i+1} seconds")
                logger.success(f"✓ Switched to {self.manager.active_provider_name.value}")
                break
        else:
            logger.error("✗ Failure not detected within 15 seconds")

    async def phase_3_verify_bybit(self):
        """Phase 3: Verify Bybit becomes active and provides data"""
        logger.info("📍 Phase 3: Verify Bybit Failover")
        logger.info("-" * 40)
        
        # Verify Bybit is now active
        if self.manager.active_provider_name == Provider.BYBIT:
            self.test_results['phase_3_bybit_active'] = True
            logger.success(f"✓ Bybit is now active provider")
        else:
            logger.error(f"✗ Expected BYBIT, got {self.manager.active_provider_name}")
            return
        
        # Verify subscriptions were transferred
        active_subscriptions = len(self.manager._subscriptions)
        if active_subscriptions == len(self.test_symbols):
            self.test_results['subscriptions_transferred'] = True
            logger.success(f"✓ All {active_subscriptions} subscriptions transferred")
        else:
            logger.error(f"✗ Expected {len(self.test_symbols)} subscriptions, got {active_subscriptions}")
        
        # Wait for Bybit data
        logger.info("Waiting for Bybit data...")
        await asyncio.sleep(5)
        
        # Check if we're receiving data from Bybit
        bybit_data_count = 0
        for symbol in self.test_symbols:
            price = self.cache.get(symbol)
            if price and price.provider == Provider.BYBIT:
                bybit_data_count += 1
                logger.debug(f"✓ {symbol}: ${price.price} from {price.provider.value}")
        
        if bybit_data_count > 0:
            self.test_results['phase_3_bybit_data'] = True
            self.test_results['cache_continuous'] = True
            logger.success(f"✓ Receiving data from Bybit for {bybit_data_count}/{len(self.test_symbols)} symbols")
            logger.success(f"✓ PriceCache continues working after failover")
        else:
            logger.error(f"✗ No Bybit data received after failover")

    async def phase_4_test_recovery(self):
        """Phase 4: Test recovery to primary provider (if implemented)"""
        logger.info("📍 Phase 4: Test Recovery to Primary")
        logger.info("-" * 40)
        
        # Check if there's an active reconnection task
        if hasattr(self.manager, '_reconnect_task') and self.manager._reconnect_task:
            logger.info("✓ Reconnection task is active")
            
            # Wait a bit to see if it tries to recover
            logger.info("Waiting to see if recovery is attempted...")
            await asyncio.sleep(10)
            
            # Check current state
            if self.manager.active_provider_name == Provider.BINANCE:
                self.test_results['phase_4_recovery'] = True
                logger.success("✓ Successfully recovered to Binance")
            else:
                logger.info(f"ℹ Still on {self.manager.active_provider_name.value} (recovery may take longer or not implemented)")
        else:
            logger.info("ℹ No automatic recovery detected (may be intended behavior)")

    async def cleanup(self):
        """Clean up test resources"""
        logger.info("🛑 Cleaning up...")
        try:
            await self.manager.stop()
            logger.success("✓ Manager stopped")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    def print_results(self):
        """Print comprehensive test results"""
        logger.info("=" * 60)
        logger.info("📊 CONTROLLED FAILOVER TEST RESULTS")
        logger.info("=" * 60)
        
        results = [
            ("Phase 1: Binance Active", self.test_results['phase_1_binance_active']),
            ("Phase 1: Binance Data Flow", self.test_results['phase_1_binance_data']), 
            ("Phase 2: Failure Detection", self.test_results['phase_2_failure_detected']),
            ("Phase 3: Bybit Active", self.test_results['phase_3_bybit_active']),
            ("Phase 3: Bybit Data Flow", self.test_results['phase_3_bybit_data']),
            ("Subscriptions Transferred", self.test_results['subscriptions_transferred']),
            ("Cache Continuity", self.test_results['cache_continuous']),
            ("Phase 4: Recovery", self.test_results['phase_4_recovery']),
        ]
        
        passed = 0
        for name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            logger.info(f"{name:<25} {status}")
            if result:
                passed += 1
        
        logger.info("-" * 60)
        logger.info(f"Overall: {passed}/{len(results)} checks passed")
        
        if all([r[1] for r in results[:7]]):  # Exclude recovery as it may not be implemented
            logger.success("🎉 FAILOVER SYSTEM WORKING CORRECTLY!")
        else:
            logger.error("💥 FAILOVER SYSTEM HAS ISSUES!")


async def main():
    """Main test function"""
    setup_logging()
    
    tester = FailoverTester()
    await tester.run_test()


if __name__ == "__main__":
    asyncio.run(main())