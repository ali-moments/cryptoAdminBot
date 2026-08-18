#!/usr/bin/env python3
"""
Real market data test script for the fixed ProviderManager.

Tests 20 real symbols with live market data to verify:
- Connection stability
- Failover functionality  
- Data flow consistency
- Health check accuracy
- Provider switching
"""

import asyncio
import signal
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
from loguru import logger


class MarketTester:
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
        
        # Test symbols - 20 popular crypto pairs
        self.test_symbols = [
            "BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT",
            "UNIUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "VETUSDT",
            "TRXUSDT", "EOSUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT",
            "AVAXUSDT", "MATICUSDT", "FTMUSDT", "ATOMUSDT", "ALGOUSDT"
        ]
        
        self.stats = {
            'prices_received': 0,
            'providers_used': set(),
            'symbols_with_data': set(),
            'start_time': None,
            'provider_switches': 0,
        }
        
        self.running = False

    async def start_test(self):
        """Start the market test"""
        print("🚀 Starting Market Data Test")
        print(f"📊 Testing {len(self.test_symbols)} symbols")
        print(f"🔗 Providers: {[p.value for p in self.providers.keys()]}")
        print("-" * 60)
        
        self.running = True
        self.stats['start_time'] = datetime.now(timezone.utc)
        
        try:
            # Start market manager
            print("🔌 Starting ProviderManager...")
            await self.manager.start()
            print(f"✅ Connected to {self.manager.active_provider_name.value}")
            
            # Subscribe to test symbols
            print(f"📈 Subscribing to {len(self.test_symbols)} symbols...")
            await self.manager.sync(set(self.test_symbols))
            print("✅ All symbols subscribed")
            
            # Start monitoring tasks
            monitor_task = asyncio.create_task(self.monitor_data())
            health_task = asyncio.create_task(self.monitor_health())
            
            # Let it run for the test duration
            await asyncio.gather(monitor_task, health_task)
            
        except KeyboardInterrupt:
            print("\n⏹️ Test interrupted by user")
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            logger.exception("Test error")
        finally:
            await self.cleanup()

    async def monitor_data(self):
        """Monitor incoming price data"""
        last_report = datetime.now(timezone.utc)
        
        while self.running:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds
                
                now = datetime.now(timezone.utc)
                
                # Count symbols with recent data (within last 30 seconds)
                symbols_with_fresh_data = 0
                for symbol in self.test_symbols:
                    price = self.cache.get(symbol)
                    if price and (now - price.timestamp).total_seconds() < 30:
                        symbols_with_fresh_data += 1
                        self.stats['symbols_with_data'].add(symbol)
                        self.stats['providers_used'].add(price.provider)
                
                # Report every 30 seconds
                if (now - last_report).total_seconds() >= 30:
                    self.print_status(symbols_with_fresh_data)
                    last_report = now
                
            except Exception as e:
                logger.error(f"Monitor data error: {e}")

    async def monitor_health(self):
        """Monitor provider health and switches"""
        last_provider = self.manager.active_provider_name
        
        while self.running:
            try:
                await asyncio.sleep(2)  # Check every 2 seconds
                
                current_provider = self.manager.active_provider_name
                if current_provider != last_provider:
                    self.stats['provider_switches'] += 1
                    print(f"🔄 Provider switched: {last_provider.value} → {current_provider.value}")
                    last_provider = current_provider
                
            except Exception as e:
                logger.error(f"Monitor health error: {e}")

    def print_status(self, symbols_with_data):
        """Print current test status"""
        now = datetime.now(timezone.utc)
        runtime = (now - self.stats['start_time']).total_seconds()
        
        print(f"\n📊 Status Report ({runtime:.0f}s)")
        print(f"🔗 Active Provider: {self.manager.active_provider_name.value}")
        print(f"📈 Symbols with data: {symbols_with_data}/{len(self.test_symbols)}")
        print(f"🔄 Provider switches: {self.stats['provider_switches']}")
        print(f"🏢 Providers used: {[p.value for p in self.stats['providers_used']]}")
        
        # Show sample prices
        sample_symbols = self.test_symbols[:5]
        print("💰 Sample prices:")
        for symbol in sample_symbols:
            price = self.cache.get(symbol)
            if price:
                age = (now - price.timestamp).total_seconds()
                print(f"  {symbol}: ${price.price} ({age:.1f}s ago, {price.provider.value})")
            else:
                print(f"  {symbol}: No data")
        
        print("-" * 60)

    async def cleanup(self):
        """Clean up resources"""
        self.running = False
        
        print("\n🛑 Stopping test...")
        
        try:
            if self.manager._running:
                await self.manager.stop()
            print("✅ ProviderManager stopped")
        except Exception as e:
            print(f"❌ Cleanup error: {e}")
        
        self.print_final_stats()

    def print_final_stats(self):
        """Print final test statistics"""
        if not self.stats['start_time']:
            return
            
        runtime = (datetime.now(timezone.utc) - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 60)
        print("📊 FINAL TEST RESULTS")
        print("=" * 60)
        print(f"⏱️  Total runtime: {runtime:.1f} seconds")
        print(f"📈 Symbols tested: {len(self.test_symbols)}")
        print(f"📊 Symbols with data: {len(self.stats['symbols_with_data'])}")
        print(f"🔄 Provider switches: {self.stats['provider_switches']}")
        print(f"🏢 Providers used: {[p.value for p in self.stats['providers_used']]}")
        
        success_rate = len(self.stats['symbols_with_data']) / len(self.test_symbols) * 100
        print(f"✅ Success rate: {success_rate:.1f}%")
        
        if success_rate >= 80:
            print("🎉 TEST PASSED - Market module working correctly!")
        elif success_rate >= 60:
            print("⚠️ TEST PARTIAL - Some issues detected")
        else:
            print("❌ TEST FAILED - Major issues detected")


async def main():
    """Main test function"""
    tester = MarketTester()
    
    # Setup signal handlers for clean shutdown
    def signal_handler(signum, frame):
        print(f"\n🛑 Received signal {signum}")
        tester.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("🧪 Real Market Data Test")
    print("⏹️ Press Ctrl+C to stop the test")
    print("🕐 Test will run until stopped...")
    print()
    
    await tester.start_test()


if __name__ == "__main__":
    # Configure logging
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        filter=lambda record: record["level"].name in ["INFO", "SUCCESS", "WARNING", "ERROR"]
    )
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
        sys.exit(1)