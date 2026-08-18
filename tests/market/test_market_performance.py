#!/usr/bin/env python3
"""
Performance and benchmark tests for market module.

Tests system performance under various loads and validates
that the health system doesn't introduce significant overhead.
"""

import asyncio
import pytest
import time
import statistics
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.base import BaseProvider
from app.market.dto import PriceTick


class PerformanceProvider(BaseProvider):
    """High-performance mock provider for benchmarking"""
    
    def __init__(self, name: Provider, dispatcher: EventDispatcher):
        super().__init__(dispatcher)
        self._name = name
        self._subscription_count = 0
        self._price_update_count = 0
        
    @property
    def name(self) -> Provider:
        return self._name
        
    async def connect(self):
        self.mark_connected()
        
    async def disconnect(self):
        self.mark_disconnected()
        self._subscription_count = 0
        
    async def subscribe(self, symbol: str):
        self._subscription_count += 1
        # Immediate data simulation
        await self._fast_price_update(symbol)
        
    async def unsubscribe(self, symbol: str):
        self._subscription_count = max(0, self._subscription_count - 1)
        
    async def current_price(self, symbol: str):
        return None
        
    async def _fast_price_update(self, symbol: str):
        """Fast price update simulation"""
        self._price_update_count += 1
        tick = PriceTick(
            provider=self._name,
            symbol=symbol,
            price=50000.0,
            timestamp=datetime.now(timezone.utc)
        )
        await self._publish_price(tick)


class TestMarketPerformance:
    """Performance benchmarks for market system"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
        
    @pytest.fixture
    async def perf_manager(self):
        """High-performance manager setup"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        providers = {
            Provider.BINANCE: PerformanceProvider(Provider.BINANCE, dispatcher),
            Provider.BYBIT: PerformanceProvider(Provider.BYBIT, dispatcher),
            Provider.OKX: PerformanceProvider(Provider.OKX, dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers
        )
        
        yield manager, providers
        
        if manager._running:
            await manager.stop()
    
    async def test_subscription_performance(self, perf_manager):
        """Benchmark subscription performance"""
        manager, providers = perf_manager
        
        print("⚡ Benchmarking subscription performance...")
        
        await manager.start()
        
        # Generate symbol list
        symbols = [f"SYMBOL{i}USDT" for i in range(1000)]
        
        print(f"📊 Subscribing to {len(symbols)} symbols...")
        
        start_time = time.time()
        
        # Batch subscribe
        tasks = []
        for symbol in symbols:
            task = asyncio.create_task(manager.subscribe(symbol))
            tasks.append(task)
            
        await asyncio.gather(*tasks)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # Performance metrics
        subscriptions_per_second = len(symbols) / total_time
        avg_time_per_subscription = total_time / len(symbols) * 1000  # ms
        
        print(f"📈 Performance Results:")
        print(f"   Total time: {total_time:.2f}s")
        print(f"   Subscriptions/sec: {subscriptions_per_second:.1f}")
        print(f"   Avg time per subscription: {avg_time_per_subscription:.2f}ms")
        
        # Verify all subscriptions worked
        assert len(manager._symbol_providers) == len(symbols)
        assert len(manager._subscriptions) == len(symbols)
        
        # Performance assertions
        assert subscriptions_per_second > 100, f"Too slow: {subscriptions_per_second:.1f} subs/sec"
        assert avg_time_per_subscription < 50, f"Too slow: {avg_time_per_subscription:.2f}ms per sub"
        
        print("✅ Subscription performance acceptable")
    
    async def test_health_check_overhead(self, perf_manager):
        """Measure health check system overhead"""
        manager, providers = perf_manager
        
        print("🩺 Measuring health check overhead...")
        
        await manager.start()
        
        # Subscribe to symbols
        symbols = [f"SYMBOL{i}USDT" for i in range(100)]
        for symbol in symbols:
            await manager.subscribe(symbol)
        
        print(f"📊 Running health checks on {len(symbols)} symbols...")
        
        # Benchmark health check performance
        iterations = 1000
        
        # Time health checks
        start_time = time.time()
        
        for _ in range(iterations):
            for symbol in symbols:
                provider_enum = manager._symbol_providers[symbol]
                provider_instance = providers[provider_enum]
                
                # This is what the health check loop does
                manager._is_provider_healthy(provider_instance)
                provider_instance.is_symbol_healthy(symbol, 60)
                
        end_time = time.time()
        total_time = end_time - start_time
        
        # Calculate overhead
        checks_per_second = (iterations * len(symbols)) / total_time
        time_per_check = total_time / (iterations * len(symbols)) * 1000000  # microseconds
        
        print(f"📈 Health Check Performance:")
        print(f"   Total checks: {iterations * len(symbols)}")
        print(f"   Total time: {total_time:.4f}s")
        print(f"   Checks/sec: {checks_per_second:.0f}")
        print(f"   Time per check: {time_per_check:.1f}μs")
        
        # Overhead should be minimal
        assert checks_per_second > 10000, f"Health checks too slow: {checks_per_second:.0f}/sec"
        assert time_per_check < 1000, f"Health check overhead too high: {time_per_check:.1f}μs"
        
        print("✅ Health check overhead acceptable")
    
    async def test_concurrent_operations_performance(self, perf_manager):
        """Test performance under concurrent operations"""
        manager, providers = perf_manager
        
        print("🏁 Testing concurrent operations performance...")
        
        await manager.start()
        
        # Setup initial subscriptions
        base_symbols = [f"BASE{i}USDT" for i in range(50)]
        for symbol in base_symbols:
            await manager.subscribe(symbol)
        
        # Concurrent operation functions
        async def subscribe_worker():
            symbols = [f"SUB{i}USDT" for i in range(100)]
            for symbol in symbols:
                await manager.subscribe(symbol)
                
        async def health_check_worker():
            for _ in range(1000):
                for symbol in base_symbols:
                    provider_enum = manager._symbol_providers.get(symbol)
                    if provider_enum:
                        provider_instance = providers[provider_enum]
                        manager._is_provider_healthy(provider_instance)
                        
        async def price_update_worker():
            binance = providers[Provider.BINANCE]
            for _ in range(500):
                for symbol in base_symbols[:10]:  # Subset for performance
                    await binance._fast_price_update(symbol)
        
        print("⚡ Running concurrent workers...")
        
        start_time = time.time()
        
        # Run all workers concurrently
        await asyncio.gather(
            subscribe_worker(),
            health_check_worker(),
            price_update_worker(),
            return_exceptions=True
        )
        
        end_time = time.time()
        total_time = end_time - start_time
        
        print(f"📈 Concurrent Performance:")
        print(f"   Total time: {total_time:.2f}s")
        print(f"   Final symbol count: {len(manager._symbol_providers)}")
        
        # Should complete in reasonable time
        assert total_time < 30, f"Concurrent operations too slow: {total_time:.2f}s"
        
        # Verify system integrity
        assert len(manager._symbol_providers) >= 50  # At least base symbols
        
        print("✅ Concurrent operations performance acceptable")
    
    async def test_memory_usage_stability(self, perf_manager):
        """Test memory usage remains stable under load"""
        manager, providers = perf_manager
        
        print("💾 Testing memory usage stability...")
        
        await manager.start()
        
        # Simulate realistic trading session
        cycles = 20
        symbols_per_cycle = 50
        
        initial_provider_states = []
        for provider in providers.values():
            initial_provider_states.append({
                'symbol_health_count': len(provider._symbol_health),
                'connection_time': provider.connection_time,
            })
        
        print(f"🔄 Running {cycles} subscription cycles...")
        
        for cycle in range(cycles):
            # Subscribe to symbols
            symbols = [f"CYCLE{cycle}_SYMBOL{i}USDT" for i in range(symbols_per_cycle)]
            
            for symbol in symbols:
                await manager.subscribe(symbol)
                
            # Simulate some activity
            binance = providers[Provider.BINANCE]
            for symbol in symbols[:10]:  # Update subset
                await binance._fast_price_update(symbol)
                
            # Unsubscribe all symbols from this cycle
            for symbol in symbols:
                await manager.unsubscribe(symbol)
                
            # Memory should be cleaned up
            if cycle % 5 == 0:  # Check every 5 cycles
                active_subscriptions = len(manager._subscriptions)
                print(f"   Cycle {cycle}: {active_subscriptions} active subscriptions")
                
        # Final state should be clean
        final_subscriptions = len(manager._subscriptions)
        final_symbol_providers = len(manager._symbol_providers)
        
        print(f"📊 Final State:")
        print(f"   Active subscriptions: {final_subscriptions}")
        print(f"   Symbol provider mappings: {final_symbol_providers}")
        
        # Memory should be cleaned up
        assert final_subscriptions == 0, f"Memory leak: {final_subscriptions} remaining subscriptions"
        assert final_symbol_providers == 0, f"Memory leak: {final_symbol_providers} remaining mappings"
        
        # Provider internal state should be reasonable
        for i, provider in enumerate(providers.values()):
            symbol_health_count = len(provider._symbol_health)
            print(f"   Provider {i+1} symbol health entries: {symbol_health_count}")
            # Should not accumulate indefinitely
            assert symbol_health_count < 100, f"Provider health memory leak: {symbol_health_count} entries"
        
        print("✅ Memory usage remains stable")
    
    async def test_latency_distribution(self, perf_manager):
        """Test latency distribution of operations"""
        manager, providers = perf_manager
        
        print("📊 Testing operation latency distribution...")
        
        await manager.start()
        
        # Measure subscription latencies
        subscription_times = []
        symbols = [f"LATENCY{i}USDT" for i in range(200)]
        
        for symbol in symbols:
            start = time.perf_counter()
            await manager.subscribe(symbol)
            end = time.perf_counter()
            subscription_times.append((end - start) * 1000)  # milliseconds
        
        # Measure health check latencies
        health_check_times = []
        
        for _ in range(1000):
            symbol = symbols[0]  # Use first symbol
            provider_enum = manager._symbol_providers[symbol]
            provider_instance = providers[provider_enum]
            
            start = time.perf_counter()
            manager._is_provider_healthy(provider_instance)
            provider_instance.is_symbol_healthy(symbol, 60)
            end = time.perf_counter()
            
            health_check_times.append((end - start) * 1000000)  # microseconds
        
        # Calculate statistics
        sub_stats = {
            'mean': statistics.mean(subscription_times),
            'median': statistics.median(subscription_times),
            'p95': sorted(subscription_times)[int(0.95 * len(subscription_times))],
            'p99': sorted(subscription_times)[int(0.99 * len(subscription_times))],
            'max': max(subscription_times)
        }
        
        health_stats = {
            'mean': statistics.mean(health_check_times),
            'median': statistics.median(health_check_times),
            'p95': sorted(health_check_times)[int(0.95 * len(health_check_times))],
            'p99': sorted(health_check_times)[int(0.99 * len(health_check_times))],
            'max': max(health_check_times)
        }
        
        print(f"📈 Subscription Latency (ms):")
        print(f"   Mean: {sub_stats['mean']:.2f}")
        print(f"   Median: {sub_stats['median']:.2f}")
        print(f"   P95: {sub_stats['p95']:.2f}")
        print(f"   P99: {sub_stats['p99']:.2f}")
        print(f"   Max: {sub_stats['max']:.2f}")
        
        print(f"📈 Health Check Latency (μs):")
        print(f"   Mean: {health_stats['mean']:.1f}")
        print(f"   Median: {health_stats['median']:.1f}")
        print(f"   P95: {health_stats['p95']:.1f}")
        print(f"   P99: {health_stats['p99']:.1f}")
        print(f"   Max: {health_stats['max']:.1f}")
        
        # Performance assertions
        assert sub_stats['p95'] < 100, f"Subscription P95 too high: {sub_stats['p95']:.2f}ms"
        assert health_stats['p95'] < 1000, f"Health check P95 too high: {health_stats['p95']:.1f}μs"
        
        print("✅ Latency distribution acceptable")
    
    async def test_throughput_under_load(self, perf_manager):
        """Test system throughput under sustained load"""
        manager, providers = perf_manager
        
        print("🔥 Testing throughput under sustained load...")
        
        await manager.start()
        
        # Setup base load
        base_symbols = [f"LOAD{i}USDT" for i in range(100)]
        for symbol in base_symbols:
            await manager.subscribe(symbol)
        
        print("📊 Applying sustained load...")
        
        # Sustained load test
        duration = 10  # 10 seconds
        start_time = time.time()
        
        operations_count = 0
        
        async def sustained_load():
            nonlocal operations_count
            end_time = start_time + duration
            
            while time.time() < end_time:
                # Mix of operations
                symbol = f"TEMP{operations_count}USDT"
                
                # Subscribe
                await manager.subscribe(symbol)
                operations_count += 1
                
                # Health check
                provider_enum = manager._symbol_providers[symbol]
                provider_instance = providers[provider_enum]
                manager._is_provider_healthy(provider_instance)
                operations_count += 1
                
                # Price update
                await provider_instance._fast_price_update(symbol)
                operations_count += 1
                
                # Unsubscribe
                await manager.unsubscribe(symbol)
                operations_count += 1
                
                # Brief pause
                await asyncio.sleep(0.001)
        
        # Run load test
        await sustained_load()
        
        actual_duration = time.time() - start_time
        operations_per_second = operations_count / actual_duration
        
        print(f"📈 Throughput Results:")
        print(f"   Duration: {actual_duration:.2f}s")
        print(f"   Total operations: {operations_count}")
        print(f"   Operations/sec: {operations_per_second:.1f}")
        
        # Verify system remained stable
        assert len(manager._subscriptions) == len(base_symbols), "System became unstable under load"
        
        # Throughput should be reasonable
        assert operations_per_second > 500, f"Throughput too low: {operations_per_second:.1f} ops/sec"
        
        print("✅ System maintains throughput under sustained load")


if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])