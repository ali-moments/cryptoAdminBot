#!/usr/bin/env python3
"""
Market system stress tests.

Tests the health system and per-symbol routing under high load,
concurrent operations, and stress conditions.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import time

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.base import BaseProvider
from app.market.dto import PriceTick


class HighThroughputMockProvider(BaseProvider):
    """Mock provider that simulates high-throughput market data"""
    
    def __init__(self, name: Provider, dispatcher: EventDispatcher):
        super().__init__(dispatcher)
        self._name = name
        self._running = False
        self._data_tasks = []
        self._update_interval = 0.1  # 10 updates per second
        
    @property
    def name(self) -> Provider:
        return self._name
        
    async def connect(self):
        self.mark_connected()
        self._running = True
        
    async def disconnect(self):
        self._running = False
        # Cancel all data generation tasks
        for task in self._data_tasks:
            if not task.done():
                task.cancel()
        self._data_tasks.clear()
        self.mark_disconnected()
        
    async def subscribe(self, symbol: str):
        """Start high-frequency data generation for symbol"""
        task = asyncio.create_task(self._generate_price_stream(symbol))
        self._data_tasks.append(task)
        
    async def unsubscribe(self, symbol: str):
        # In real implementation, would stop specific symbol stream
        pass
        
    async def current_price(self, symbol: str):
        return 50000.0 + hash(symbol) % 10000  # Deterministic price
        
    async def _generate_price_stream(self, symbol: str):
        """Generate continuous price updates"""
        base_price = 50000.0 + hash(symbol) % 10000
        
        while self._running:
            try:
                # Simulate price variation
                variation = (time.time() % 100 - 50) * 10  # ±500 variation
                current_price = base_price + variation
                
                tick = PriceTick(
                    provider=self._name,
                    symbol=symbol,
                    price=current_price,
                    timestamp=datetime.now(timezone.utc)
                )
                
                await self._publish_price(tick)
                await asyncio.sleep(self._update_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Continue on error to maintain stream
                await asyncio.sleep(self._update_interval)


class TestMarketStress:
    """Stress tests for market system"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
        
    @pytest.fixture
    async def stress_manager(self):
        """Create manager with high-throughput mock providers"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        providers = {
            Provider.BINANCE: HighThroughputMockProvider(Provider.BINANCE, dispatcher),
            Provider.BYBIT: HighThroughputMockProvider(Provider.BYBIT, dispatcher),
            Provider.OKX: HighThroughputMockProvider(Provider.OKX, dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers
        )
        
        yield manager, providers, cache
        
        if manager._running:
            await manager.stop()
    
    @pytest.mark.asyncio
    async def test_high_volume_symbol_subscriptions(self, stress_manager):
        """Test handling many concurrent symbol subscriptions"""
        manager, providers, cache = stress_manager
        
        print("📈 Testing high-volume symbol subscriptions...")
        
        await manager.start()
        
        # Generate many symbols
        symbols = [f"SYM{i:04d}USDT" for i in range(100)]
        
        # Subscribe to all symbols concurrently
        start_time = time.time()
        
        subscription_tasks = []
        for symbol in symbols:
            task = asyncio.create_task(manager.subscribe(symbol))
            subscription_tasks.append(task)
        
        # Wait for all subscriptions
        results = await asyncio.gather(*subscription_tasks, return_exceptions=True)
        
        subscription_time = time.time() - start_time
        
        # Count successful subscriptions
        successful = sum(1 for r in results if not isinstance(r, Exception))
        failed = len(results) - successful
        
        print(f"📊 Subscription results:")
        print(f"  Total symbols: {len(symbols)}")
        print(f"  Successful: {successful}")
        print(f"  Failed: {failed}")
        print(f"  Time taken: {subscription_time:.2f}s")
        print(f"  Rate: {successful/subscription_time:.1f} subs/sec")
        
        # Should handle at least 80% successfully
        success_rate = successful / len(symbols)
        assert success_rate >= 0.8, f"Success rate {success_rate:.2f} too low"
        
        # Wait for data flow
        await asyncio.sleep(5)
        
        # Check data is flowing for subscribed symbols
        data_count = 0
        for symbol in symbols[:10]:  # Check first 10
            price = cache.get_price(symbol)
            if price:
                data_count += 1
        
        print(f"📊 Data flowing for {data_count}/10 sampled symbols")
        assert data_count >= 5, "Should have data for at least half of sampled symbols"
        
        print("✅ High-volume subscription test passed")
    
    @pytest.mark.asyncio
    async def test_concurrent_health_operations(self, stress_manager):
        """Test concurrent health check operations under load"""
        manager, providers, cache = stress_manager
        
        print("🏁 Testing concurrent health operations under load...")
        
        await manager.start()
        
        # Subscribe to symbols to generate load
        symbols = [f"LOAD{i:03d}USDT" for i in range(20)]
        
        for symbol in symbols:
            await manager.subscribe(symbol)
        
        # Wait for data flow to start
        await asyncio.sleep(2)
        
        # Run concurrent health operations
        async def health_stress_loop(loop_id: int):
            """Stress test health checking"""
            operations = 0
            errors = 0
            
            for _ in range(200):  # 200 operations per loop
                try:
                    # Mix of different health operations
                    for provider_enum, provider_instance in providers.items():
                        manager._is_provider_healthy(provider_instance)
                        
                        # Check random symbol health
                        symbol = symbols[operations % len(symbols)]
                        provider_instance.is_symbol_healthy(symbol, 60)
                        
                        operations += 1
                        
                    await asyncio.sleep(0.001)  # Minimal delay
                    
                except Exception as e:
                    errors += 1
                    
            return operations, errors
        
        # Run multiple concurrent stress loops
        start_time = time.time()
        
        stress_tasks = [
            asyncio.create_task(health_stress_loop(i))
            for i in range(5)  # 5 concurrent loops
        ]
        
        results = await asyncio.gather(*stress_tasks)
        
        stress_time = time.time() - start_time
        
        # Analyze results
        total_operations = sum(r[0] for r in results)
        total_errors = sum(r[1] for r in results)
        
        print(f"🏁 Stress test results:")
        print(f"  Total operations: {total_operations}")
        print(f"  Total errors: {total_errors}")
        print(f"  Time taken: {stress_time:.2f}s")
        print(f"  Operations/sec: {total_operations/stress_time:.1f}")
        print(f"  Error rate: {total_errors/total_operations*100:.2f}%")
        
        # System should handle load with low error rate
        error_rate = total_errors / total_operations
        assert error_rate < 0.01, f"Error rate {error_rate:.3f} too high"
        
        # Should maintain reasonable throughput
        ops_per_second = total_operations / stress_time
        assert ops_per_second > 1000, f"Throughput {ops_per_second:.1f} ops/sec too low"
        
        print("✅ Concurrent health stress test passed")
    
    @pytest.mark.asyncio
    async def test_rapid_provider_state_changes(self, stress_manager):
        """Test rapid provider connection/disconnection cycles"""
        manager, providers, cache = stress_manager
        
        print("⚡ Testing rapid provider state changes...")
        
        await manager.start()
        
        # Subscribe to symbols
        test_symbols = ["RAPID001USDT", "RAPID002USDT", "RAPID003USDT"]
        
        for symbol in test_symbols:
            await manager.subscribe(symbol)
        
        # Wait for initial data
        await asyncio.sleep(2)
        
        binance = providers[Provider.BINANCE]
        bybit = providers[Provider.BYBIT]
        
        # Rapid state change cycles
        cycles = 10
        start_time = time.time()
        
        for cycle in range(cycles):
            # Disconnect primary
            await binance.disconnect()
            await asyncio.sleep(0.1)
            
            # Connect backup
            await bybit.connect()
            await asyncio.sleep(0.1)
            
            # Disconnect backup
            await bybit.disconnect()
            await asyncio.sleep(0.1)
            
            # Reconnect primary
            await binance.connect()
            await asyncio.sleep(0.1)
            
        cycle_time = time.time() - start_time
        
        print(f"⚡ Rapid state changes:")
        print(f"  Cycles completed: {cycles}")
        print(f"  Time taken: {cycle_time:.2f}s")
        print(f"  Average cycle time: {cycle_time/cycles:.3f}s")
        
        # System should remain stable
        final_health = {
            prov_enum: manager._is_provider_healthy(prov_instance)
            for prov_enum, prov_instance in providers.items()
        }
        
        print(f"🩺 Final health status: {final_health}")
        
        # At least one provider should be healthy
        healthy_providers = [k for k, v in final_health.items() if v]
        assert len(healthy_providers) > 0, "At least one provider should remain healthy"
        
        # Data should still be flowing
        await asyncio.sleep(3)
        
        active_data = 0
        for symbol in test_symbols:
            price = cache.get_price(symbol)
            if price and (datetime.now(timezone.utc) - price.timestamp).total_seconds() < 10:
                active_data += 1
                
        print(f"📊 Active data streams: {active_data}/{len(test_symbols)}")
        assert active_data > 0, "Should maintain at least one active data stream"
        
        print("✅ Rapid state change test passed")
    
    @pytest.mark.asyncio
    async def test_memory_usage_under_load(self, stress_manager):
        """Test memory usage doesn't grow excessively under load"""
        manager, providers, cache = stress_manager
        
        print("🧠 Testing memory usage under load...")
        
        await manager.start()
        
        # Helper to measure memory usage
        def measure_objects():
            """Count health-related objects"""
            total_symbols = 0
            total_health_entries = 0
            
            for provider_instance in providers.values():
                total_symbols += len(provider_instance._symbol_health)
                total_health_entries += len(provider_instance._symbol_health)
                
            cache_size = len(cache._prices)
            return total_symbols, total_health_entries, cache_size
        
        # Baseline measurement
        baseline = measure_objects()
        print(f"📊 Baseline: symbols={baseline[0]}, health={baseline[1]}, cache={baseline[2]}")
        
        # Generate load with many symbols
        symbols = [f"MEM{i:03d}USDT" for i in range(50)]
        
        for symbol in symbols:
            await manager.subscribe(symbol)
        
        # Let system run under load
        await asyncio.sleep(10)
        
        # Measure during load
        load_measurement = measure_objects()
        print(f"📊 Under load: symbols={load_measurement[0]}, health={load_measurement[1]}, cache={load_measurement[2]}")
        
        # Unsubscribe from symbols
        for symbol in symbols:
            await manager.unsubscribe(symbol)
        
        # Wait for cleanup
        await asyncio.sleep(5)
        
        # Measure after cleanup
        cleanup_measurement = measure_objects()
        print(f"📊 After cleanup: symbols={cleanup_measurement[0]}, health={cleanup_measurement[1]}, cache={cleanup_measurement[2]}")
        
        # Memory should not grow excessively
        # Cache might remain higher due to price history
        symbol_growth = cleanup_measurement[0] - baseline[0]
        health_growth = cleanup_measurement[1] - baseline[1]
        
        print(f"🧠 Memory growth: symbols={symbol_growth}, health={health_growth}")
        
        # Health data should be cleaned up reasonably well
        # Allow some growth but not excessive
        assert symbol_growth < len(symbols) * 0.5, f"Too much symbol data retained: {symbol_growth}"
        assert health_growth < len(symbols) * 0.5, f"Too much health data retained: {health_growth}"
        
        print("✅ Memory usage test passed")
    
    @pytest.mark.asyncio
    async def test_error_resilience_under_stress(self, stress_manager):
        """Test system resilience to errors under stress"""
        manager, providers, cache = stress_manager
        
        print("💥 Testing error resilience under stress...")
        
        await manager.start()
        
        # Subscribe to test symbols
        symbols = ["ERROR001USDT", "ERROR002USDT", "ERROR003USDT"]
        
        for symbol in symbols:
            await manager.subscribe(symbol)
        
        await asyncio.sleep(2)
        
        # Simulate various error conditions
        binance = providers[Provider.BINANCE]
        bybit = providers[Provider.BYBIT]
        
        error_conditions = []
        
        async def simulate_errors():
            """Simulate various error conditions"""
            for i in range(20):  # 20 error cycles
                try:
                    if i % 4 == 0:
                        # Simulate connection failure
                        await binance.disconnect()
                        error_conditions.append("binance_disconnect")
                        
                    elif i % 4 == 1:
                        # Simulate reconnection
                        await binance.connect()
                        error_conditions.append("binance_connect")
                        
                    elif i % 4 == 2:
                        # Simulate backup provider issues
                        await bybit.disconnect()
                        error_conditions.append("bybit_disconnect")
                        
                    else:
                        # Simulate backup recovery
                        await bybit.connect()
                        error_conditions.append("bybit_connect")
                        
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    error_conditions.append(f"error_{type(e).__name__}")
        
        # Run error simulation concurrently with health checks
        async def continuous_health_monitoring():
            """Continuously monitor health during errors"""
            health_samples = []
            
            for _ in range(100):  # 100 health checks
                try:
                    sample = {
                        'timestamp': time.time(),
                        'binance_healthy': manager._is_provider_healthy(binance),
                        'bybit_healthy': manager._is_provider_healthy(bybit),
                        'data_flowing': bool(cache.get_price(symbols[0]))
                    }
                    health_samples.append(sample)
                    
                except Exception as e:
                    health_samples.append({'error': str(e)})
                    
                await asyncio.sleep(0.1)
                
            return health_samples
        
        # Run both tasks concurrently
        start_time = time.time()
        
        error_task = asyncio.create_task(simulate_errors())
        health_task = asyncio.create_task(continuous_health_monitoring())
        
        await asyncio.gather(error_task, health_task)
        
        test_time = time.time() - start_time
        health_samples = health_task.result()
        
        print(f"💥 Error resilience results:")
        print(f"  Test duration: {test_time:.2f}s")
        print(f"  Error conditions: {len(error_conditions)}")
        print(f"  Health samples: {len(health_samples)}")
        
        # Analyze health stability
        error_samples = [s for s in health_samples if 'error' in s]
        healthy_samples = [s for s in health_samples if 'error' not in s and (s.get('binance_healthy') or s.get('bybit_healthy'))]
        data_samples = [s for s in health_samples if 'error' not in s and s.get('data_flowing')]
        
        print(f"  Health check errors: {len(error_samples)}")
        print(f"  Samples with healthy provider: {len(healthy_samples)}")
        print(f"  Samples with data flowing: {len(data_samples)}")
        
        # System should remain resilient
        error_rate = len(error_samples) / len(health_samples)
        health_rate = len(healthy_samples) / len(health_samples)
        data_rate = len(data_samples) / len(health_samples)
        
        print(f"📊 Rates: error={error_rate:.2f}, healthy={health_rate:.2f}, data={data_rate:.2f}")
        
        assert error_rate < 0.1, f"Health check error rate {error_rate:.3f} too high"
        assert health_rate > 0.7, f"Healthy provider rate {health_rate:.3f} too low"
        assert data_rate > 0.5, f"Data flow rate {data_rate:.3f} too low"
        
        print("✅ Error resilience test passed")


if __name__ == "__main__":
    print("🔥 Running market stress tests...")
    print("   - These tests simulate high load conditions")
    print("   - Tests may take several minutes to complete")
    print("   - Monitor system resources during execution")
    
    pytest.main(["-v", "-s", __file__, "--tb=short"])