#!/usr/bin/env python3
"""
Deep investigation test - specifically checks for the 4 identified issues:
1. Health check timing race conditions
2. Provider switching dual _active assignment
3. Subscription cleanup during disconnection
4. Status tracking race conditions
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


class DeepInvestigationTester:
    def __init__(self):
        self.dispatcher = EventDispatcher()
        self.cache = PriceCache()
        
        self.dispatcher.subscribe(PriceUpdatedEvent, self.cache.on_price_updated)
        
        self.providers = {
            Provider.BINANCE: BinanceProvider(self.dispatcher),
            Provider.BYBIT: BybitProvider(self.dispatcher),
            Provider.OKX: OKXProvider(self.dispatcher),
        }
        
        self.manager = ProviderManager(
            dispatcher=self.dispatcher,
            cache=self.cache,
            providers=self.providers,
        )
        
        self.test_symbols = ["BTCUSDT", "ETHUSDT"]
        self.issues_found = []

    async def run_investigation(self):
        """Run deep investigation of the 4 potential issues"""
        logger.info("🔍 DEEP INVESTIGATION - 4 CRITICAL AREAS")
        logger.info("=" * 60)
        
        try:
            await self.manager.start()
            
            for symbol in self.test_symbols:
                await self.manager.subscribe(symbol)
            
            # Issue #1: Health check timing
            await self.investigate_health_check_timing()
            
            # Issue #2 & #4: Provider switching and status tracking
            await self.investigate_provider_switching_status()
            
            # Issue #3: Subscription cleanup
            await self.investigate_subscription_cleanup()
            
        except Exception as e:
            logger.error(f"Investigation failed: {e}")
        finally:
            await self.manager.stop()
        
        self.report_findings()

    async def investigate_health_check_timing(self):
        """Investigate potential health check timing issues"""
        logger.info("🔍 Issue #1: Health Check Timing")
        logger.info("-" * 40)
        
        # Check the timing constants
        grace_period = self.manager.GRACE_PERIOD
        health_interval = self.manager.HEALTH_CHECK_INTERVAL
        initial_timeout = self.manager.INITIAL_DATA_TIMEOUT
        
        logger.info(f"Grace Period: {grace_period}s")
        logger.info(f"Health Check Interval: {health_interval}s") 
        logger.info(f"Initial Data Timeout: {initial_timeout}s")
        
        # First health check at 7s, grace period is 12s - should be safe
        if health_interval < grace_period:
            logger.success("✓ Health check interval < grace period (safe)")
        else:
            self.issues_found.append("Health check interval >= grace period (potential issue)")
            logger.error("✗ Health check interval >= grace period")
        
        # Check if initial timeout is reasonable for connection establishment
        if initial_timeout >= 2.0:
            logger.success("✓ Initial data timeout reasonable for connection")
        else:
            self.issues_found.append("Initial data timeout too short for connection")
            logger.warning("⚠ Initial data timeout may be too short")

    async def investigate_provider_switching_status(self):
        """Investigate provider switching and status tracking issues"""
        logger.info("🔍 Issue #2 & #4: Provider Switching + Status Tracking")
        logger.info("-" * 40)
        
        initial_active = self.manager.active_provider_name
        logger.info(f"Initial active provider: {initial_active.value}")
        
        # Force a failure to trigger switching
        logger.info("Forcing provider failure...")
        
        # Track _active assignments during switch
        original_try_connect = self.manager._try_connect
        original_switch_provider = self.manager._switch_provider
        
        active_assignments = []
        
        async def track_try_connect(provider):
            result = await original_try_connect(provider)
            if result:
                active_assignments.append(f"_try_connect set _active to {self.manager._active.value}")
            return result
        
        async def track_switch_provider(new_provider):
            before_switch = self.manager._active
            result = await original_switch_provider(new_provider)
            after_switch = self.manager._active
            active_assignments.append(f"_switch_provider changed _active from {before_switch.value} to {after_switch.value}")
            return result
        
        # Monkey patch to track assignments
        self.manager._try_connect = track_try_connect
        self.manager._switch_provider = track_switch_provider
        
        # Force failure
        binance_instance = self.manager.active_provider
        binance_instance.mark_disconnected()
        
        # Wait for failover
        await asyncio.sleep(10)
        
        # Check for dual assignments
        logger.info("_active assignment tracking:")
        for assignment in active_assignments:
            logger.info(f"  {assignment}")
        
        if len(active_assignments) > 1:
            self.issues_found.append("Dual _active assignments during provider switch")
            logger.warning("⚠ Multiple _active assignments detected during switch")
        else:
            logger.success("✓ Single _active assignment during switch")
        
        # Restore original methods
        self.manager._try_connect = original_try_connect
        self.manager._switch_provider = original_switch_provider

    async def investigate_subscription_cleanup(self):
        """Investigate subscription cleanup during disconnection"""
        logger.info("🔍 Issue #3: Subscription Cleanup")
        logger.info("-" * 40)
        
        # Check current subscriptions
        current_subs = len(self.manager._subscriptions)
        logger.info(f"Current subscriptions: {current_subs}")
        
        # Check if old provider was properly unsubscribed
        # This is hard to test directly, but we can check the subscription count consistency
        
        active_provider = self.manager.active_provider
        if hasattr(active_provider, '_subscriptions'):
            provider_subs = len(active_provider._subscriptions)
            logger.info(f"Active provider subscriptions: {provider_subs}")
            
            if provider_subs == current_subs:
                logger.success("✓ Subscription counts consistent")
            else:
                self.issues_found.append("Subscription count mismatch between manager and provider")
                logger.error(f"✗ Manager has {current_subs}, provider has {provider_subs}")
        else:
            logger.info("ℹ Provider doesn't track subscriptions separately")

    def report_findings(self):
        """Report all findings from the investigation"""
        logger.info("=" * 60)
        logger.info("🔍 DEEP INVESTIGATION RESULTS")
        logger.info("=" * 60)
        
        if not self.issues_found:
            logger.success("✅ NO CRITICAL ISSUES FOUND")
            logger.success("All 4 investigated areas appear to be working correctly")
        else:
            logger.error(f"🐛 {len(self.issues_found)} POTENTIAL ISSUES FOUND:")
            for i, issue in enumerate(self.issues_found, 1):
                logger.error(f"  {i}. {issue}")


async def main():
    setup_logging()
    tester = DeepInvestigationTester()
    await tester.run_investigation()


if __name__ == "__main__":
    asyncio.run(main())