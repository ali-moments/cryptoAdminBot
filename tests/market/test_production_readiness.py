#!/usr/bin/env python3
"""
Production readiness tests for the market system.

These tests validate that the market system is ready for production deployment
by testing all critical scenarios, recovery mechanisms, and performance requirements.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
import logging

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider


class TestProductionReadiness:
    """Production readiness validation tests"""
    
    @pytest.fixture
    async def production_manager(self):
        """Create production-ready manager configuration"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        # Real providers for production testing
        providers = {
            Provider.BINANCE: BinanceProvider(dispatcher),
            Provider.BYBIT: BybitProvider(dispatcher),
            Provider.OKX: OKXProvider(dispatcher),
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
    async def test_cold_start_recovery(self, production_manager):
        """Test system can start from cold state and establish all connections"""
        manager, providers, cache = production_manager
        
        print("🥶 Testing cold start recovery...")
        
        # Verify initial state
        assert not manager._running
        for provider in providers.values():
            assert not provider.is_connected
        
        # Start system
        start_time = datetime.now()
        await manager.start()
        startup_time = (datetime.now() - start_time).total_seconds()
        
        print(f"⏱️  Startup time: {startup_time:.2f}s")
        
        # Verify successful startup
        assert manager._running
        assert manager._active in providers
        
        primary_provider = providers[manager._active]
        assert primary_provider.is_connected
        assert manager._is_provider_healthy(primary_provider)
        
        # Test critical symbol subscription
        critical_symbols = ["BTCUSDT", "ETHUSDT"]
        
        for symbol in critical_symbols:
            await manager.subscribe(symbol)
            provider_enum = manager._symbol_providers.get(symbol)
            assert provider_enum is not None
            print(f"📍 {symbol} routed to {provider_enum.value}")
        
        # Wait for data flow
        await asyncio.sleep(15)
        
        # Verify data is flowing for critical symbols
        for symbol in critical_symbols:
            price = cache.get_price(symbol)
            assert price is not None, f"No price data for critical symbol {symbol}"
            
            age_seconds = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
            assert age_seconds < 60, f"Stale data for {symbol}: {age_seconds}s old"
            
            print(f"✅ {symbol}: ${price.price} ({age_seconds:.1f}s old)")
        
        print("✅ Cold start recovery successful")
    
    @pytest.mark.asyncio
    async def test_provider_cascade_failure_recovery(self, production_manager):
        """Test recovery from cascade provider failures"""
        manager, providers, cache = production_manager
        
        print("💥 Testing provider cascade failure recovery...")
        
        await manager.start()
        
        # Subscribe to test symbols
        test_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        
        for symbol in test_symbols:
            await manager.subscribe(symbol)
        
        # Wait for initial data
        await asyncio.sleep(10)
        
        # Verify initial state
        initial_data = {}
        for symbol in test_symbols:
            price = cache.get_price(symbol)
            initial_data[symbol] = price
            print(f"📊 Initial {symbol}: ${price.price if price else 'None'}")
        
        # Simulate cascade failure - primary fails first
        print("🔥 Simulating primary provider failure...")
        primary = providers[manager._active]
        await primary.disconnect()
        
        await asyncio.sleep(5)
        
        # Simulate secondary failure
        print("🔥 Simulating secondary provider failure...")
        secondary_providers = [p for p in providers.values() if p != primary]
        if secondary_providers:
            await secondary_providers[0].disconnect()
            
        await asyncio.sleep(5)
        
        # At this point, system should be trying to use remaining providers
        # or attempting reconnection
        
        # Simulate recovery - restore one provider
        print("🔧 Simulating provider recovery...")
        await primary.connect()
        
        # Wait for system to detect recovery and re-establish subscriptions
        await asyncio.sleep(15)
        
        # Verify recovery
        print("🩺 Verifying recovery...")
        recovered_count = 0
        
        for symbol in test_symbols:
            price = cache.get_price(symbol)
            if price:
                age_seconds = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
                if age_seconds < 60:  # Fresh data
                    recovered_count += 1
                    print(f"✅ {symbol} recovered: ${price.price} ({age_seconds:.1f}s old)")
                else:
                    print(f"⚠️  {symbol} stale: ${price.price} ({age_seconds:.1f}s old)")
            else:
                print(f"❌ {symbol}: No data")
        
        # Should recover at least 80% of symbols
        recovery_rate = recovered_count / len(test_symbols)
        print(f"📊 Recovery rate: {recovery_rate:.2f}")
        assert recovery_rate >= 0.8, f"Recovery rate {recovery_rate:.2f} insufficient"
        
        print("✅ Cascade failure recovery successful")
    
    @pytest.mark.asyncio
    async def test_data_staleness_detection_and_failover(self, production_manager):
        """Test detection of stale data and automatic failover"""
        manager, providers, cache = production_manager
        
        print("📡 Testing data staleness detection and failover...")
        
        await manager.start()
        
        # Subscribe to test symbol
        test_symbol = "BTCUSDT"
        await manager.subscribe(test_symbol)
        
        # Wait for initial data
        await asyncio.sleep(10)
        
        initial_price = cache.get_price(test_symbol)
        assert initial_price is not None
        initial_provider = manager._symbol_providers[test_symbol]
        
        print(f"📊 Initial: {test_symbol} = ${initial_price.price} from {initial_provider.value}")
        
        # Connect backup provider
        backup_providers = [p for p_enum, p in providers.items() if p_enum != initial_provider]
        if backup_providers:
            backup = backup_providers[0]
            if not backup.is_connected:
                await backup.connect()
                await asyncio.sleep(5)
        
        # Simulate primary provider data stopping (connection stays up but no data)
        print("🔌 Simulating data stall on primary provider...")
        
        primary_provider_instance = providers[initial_provider]
        
        # Force stale data by manipulating health timestamp
        primary_provider_instance._last_global_ticker = datetime.now(timezone.utc) - timedelta(seconds=200)
        if test_symbol in primary_provider_instance._symbol_health:
            primary_provider_instance._symbol_health[test_symbol] = datetime.now(timezone.utc) - timedelta(seconds=200)
        
        # Wait for health system to detect staleness
        await asyncio.sleep(10)
        
        # Verify staleness detection
        is_healthy = manager._is_provider_healthy(primary_provider_instance)
        symbol_healthy = primary_provider_instance.is_symbol_healthy(test_symbol, 60)
        
        print(f"🩺 Health check: provider={is_healthy}, symbol={symbol_healthy}")
        
        # The provider should be detected as unhealthy due to stale data
        assert not is_healthy or not symbol_healthy, "Should detect stale data"
        
        # For new subscriptions, should avoid unhealthy provider
        test_symbol2 = "ETHUSDT"
        await manager.subscribe(test_symbol2)
        
        new_provider = manager._symbol_providers.get(test_symbol2)
        if new_provider:
            print(f"📍 New symbol {test_symbol2} routed to: {new_provider.value}")
            
            # Should route to different provider if primary is unhealthy
            if not is_healthy:
                assert new_provider != initial_provider, "Should avoid unhealthy provider for new symbols"
        
        print("✅ Staleness detection working correctly")
    
    @pytest.mark.asyncio
    async def test_high_frequency_health_monitoring(self, production_manager):
        """Test health monitoring under high-frequency operations"""
        manager, providers, cache = production_manager
        
        print("⚡ Testing high-frequency health monitoring...")
        
        await manager.start()
        
        # Subscribe to multiple symbols
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "SOLUSDT"]
        
        for symbol in symbols:
            await manager.subscribe(symbol)
        
        await asyncio.sleep(10)
        
        # Run high-frequency health monitoring
        async def monitor_health():
            """High-frequency health monitoring loop"""
            health_samples = []
            
            for i in range(1000):  # 1000 health checks
                sample_time = datetime.now()
                
                try:
                    # Check global provider health
                    provider_health = {}
                    for prov_enum, prov_instance in providers.items():
                        provider_health[prov_enum] = manager._is_provider_healthy(prov_instance)
                    
                    # Check symbol-specific health
                    symbol_health = {}
                    for symbol in symbols:
                        prov_enum = manager._symbol_providers.get(symbol)
                        if prov_enum:
                            prov_instance = providers[prov_enum]
                            symbol_health[symbol] = prov_instance.is_symbol_healthy(symbol, 60)
                    
                    sample = {
                        'time': sample_time,
                        'providers': provider_health,
                        'symbols': symbol_health,
                        'cache_size': len(cache._prices)
                    }
                    
                    health_samples.append(sample)
                    
                except Exception as e:
                    health_samples.append({'error': str(e), 'time': sample_time})
                
                await asyncio.sleep(0.01)  # 100Hz monitoring
            
            return health_samples
        
        # Run monitoring
        samples = await monitor_health()
        
        # Analyze results
        error_samples = [s for s in samples if 'error' in s]
        valid_samples = [s for s in samples if 'error' not in s]
        
        print(f"📊 High-frequency monitoring results:")
        print(f"  Total samples: {len(samples)}")
        print(f"  Valid samples: {len(valid_samples)}")
        print(f"  Error samples: {len(error_samples)}")
        
        if error_samples:
            print(f"  Sample errors: {[s['error'] for s in error_samples[:5]]}")
        
        error_rate = len(error_samples) / len(samples)
        print(f"  Error rate: {error_rate:.3f}")
        
        # Should have very low error rate
        assert error_rate < 0.01, f"Health monitoring error rate {error_rate:.3f} too high"
        
        # Check consistency
        if valid_samples:
            # At least some providers should be consistently healthy
            provider_health_rates = {}
            for prov_enum in providers.keys():
                healthy_count = sum(1 for s in valid_samples if s['providers'].get(prov_enum, False))
                provider_health_rates[prov_enum] = healthy_count / len(valid_samples)
            
            print(f"  Provider health rates: {provider_health_rates}")
            
            # At least one provider should be healthy most of the time
            max_health_rate = max(provider_health_rates.values()) if provider_health_rates else 0
            assert max_health_rate > 0.8, f"No provider consistently healthy: {provider_health_rates}"
        
        print("✅ High-frequency health monitoring successful")
    
    @pytest.mark.asyncio
    async def test_production_performance_requirements(self, production_manager):
        """Test system meets production performance requirements"""
        manager, providers, cache = production_manager
        
        print("🚀 Testing production performance requirements...")
        
        # Performance requirements
        MAX_STARTUP_TIME = 30.0  # seconds
        MAX_SUBSCRIPTION_TIME = 5.0  # seconds per symbol
        MIN_DATA_FRESHNESS = 60.0  # seconds
        MIN_HEALTH_CHECK_RATE = 50.0  # checks per second
        
        # Test startup performance
        print("⏱️  Testing startup performance...")
        start_time = datetime.now()
        await manager.start()
        startup_time = (datetime.now() - start_time).total_seconds()
        
        print(f"  Startup time: {startup_time:.2f}s (requirement: <{MAX_STARTUP_TIME}s)")
        assert startup_time < MAX_STARTUP_TIME, f"Startup too slow: {startup_time:.2f}s"
        
        # Test subscription performance
        print("📝 Testing subscription performance...")
        test_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        
        subscription_times = []
        for symbol in test_symbols:
            sub_start = datetime.now()
            await manager.subscribe(symbol)
            sub_time = (datetime.now() - sub_start).total_seconds()
            subscription_times.append(sub_time)
            print(f"  {symbol}: {sub_time:.2f}s")
        
        avg_sub_time = sum(subscription_times) / len(subscription_times)
        max_sub_time = max(subscription_times)
        
        print(f"  Average subscription time: {avg_sub_time:.2f}s")
        print(f"  Maximum subscription time: {max_sub_time:.2f}s (requirement: <{MAX_SUBSCRIPTION_TIME}s)")
        
        assert max_sub_time < MAX_SUBSCRIPTION_TIME, f"Subscription too slow: {max_sub_time:.2f}s"
        
        # Test data freshness
        print("📡 Testing data freshness...")
        await asyncio.sleep(15)  # Wait for data
        
        freshness_results = {}
        for symbol in test_symbols:
            price = cache.get_price(symbol)
            if price:
                age = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
                freshness_results[symbol] = age
                print(f"  {symbol}: {age:.1f}s old")
        
        if freshness_results:
            avg_age = sum(freshness_results.values()) / len(freshness_results)
            max_age = max(freshness_results.values())
            
            print(f"  Average data age: {avg_age:.1f}s")
            print(f"  Maximum data age: {max_age:.1f}s (requirement: <{MIN_DATA_FRESHNESS}s)")
            
            assert max_age < MIN_DATA_FRESHNESS, f"Data too stale: {max_age:.1f}s"
        
        # Test health check performance
        print("🩺 Testing health check performance...")
        health_start = datetime.now()
        health_checks = 0
        
        for _ in range(1000):  # 1000 health checks
            for provider_instance in providers.values():
                manager._is_provider_healthy(provider_instance)
                health_checks += 1
        
        health_time = (datetime.now() - health_start).total_seconds()
        health_rate = health_checks / health_time
        
        print(f"  Health checks: {health_checks} in {health_time:.2f}s")
        print(f"  Health check rate: {health_rate:.1f} checks/s (requirement: >{MIN_HEALTH_CHECK_RATE}/s)")
        
        assert health_rate > MIN_HEALTH_CHECK_RATE, f"Health checks too slow: {health_rate:.1f}/s"
        
        print("✅ All production performance requirements met")
    
    @pytest.mark.asyncio
    async def test_logging_and_observability(self, production_manager):
        """Test logging and observability features"""
        manager, providers, cache = production_manager
        
        print("📝 Testing logging and observability...")
        
        # Capture log messages
        log_messages = []
        
        class TestLogHandler(logging.Handler):
            def emit(self, record):
                log_messages.append(record.getMessage())
        
        # Add test handler to relevant loggers
        test_handler = TestLogHandler()
        test_handler.setLevel(logging.DEBUG)
        
        manager_logger = logging.getLogger('app.market.manager')
        manager_logger.addHandler(test_handler)
        manager_logger.setLevel(logging.DEBUG)
        
        try:
            # Perform operations that should generate logs
            await manager.start()
            await manager.subscribe("BTCUSDT")
            await asyncio.sleep(5)
            
            # Check for key log messages
            connection_logs = [msg for msg in log_messages if 'connect' in msg.lower()]
            subscription_logs = [msg for msg in log_messages if 'subscrib' in msg.lower()]
            health_logs = [msg for msg in log_messages if 'health' in msg.lower()]
            
            print(f"📊 Log message analysis:")
            print(f"  Total messages: {len(log_messages)}")
            print(f"  Connection logs: {len(connection_logs)}")
            print(f"  Subscription logs: {len(subscription_logs)}")
            print(f"  Health logs: {len(health_logs)}")
            
            # Should have comprehensive logging
            assert len(connection_logs) > 0, "Missing connection log messages"
            assert len(subscription_logs) > 0, "Missing subscription log messages"
            
            # Sample log messages
            if log_messages:
                print("  Sample log messages:")
                for msg in log_messages[-5:]:
                    print(f"    {msg}")
            
        finally:
            manager_logger.removeHandler(test_handler)
        
        print("✅ Logging and observability validated")


@pytest.mark.asyncio
async def test_full_production_simulation():
    """Full production simulation test"""
    print("🎭 Running full production simulation...")
    
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    providers = {
        Provider.BINANCE: BinanceProvider(dispatcher),
        Provider.BYBIT: BybitProvider(dispatcher),
        Provider.OKX: OKXProvider(dispatcher),
    }
    
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers=providers
    )
    
    try:
        # Phase 1: System Startup
        print("Phase 1: System startup and initialization")
        await manager.start()
        
        # Phase 2: Load Critical Symbols
        print("Phase 2: Loading critical trading symbols")
        critical_symbols = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "SOLUSDT",
            "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT"
        ]
        
        for symbol in critical_symbols:
            await manager.subscribe(symbol)
            await asyncio.sleep(0.5)  # Realistic subscription pace
        
        # Phase 3: Normal Operation
        print("Phase 3: Normal operation monitoring")
        await asyncio.sleep(30)  # 30 seconds of normal operation
        
        # Verify all symbols have data
        active_symbols = 0
        for symbol in critical_symbols:
            price = cache.get_price(symbol)
            if price:
                age = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
                if age < 60:
                    active_symbols += 1
                print(f"  {symbol}: ${price.price} ({age:.1f}s old)")
        
        print(f"Active symbols: {active_symbols}/{len(critical_symbols)}")
        
        # Phase 4: Simulate Production Incident
        print("Phase 4: Simulating production incident")
        
        # Primary provider failure
        primary = providers[manager._active]
        await primary.disconnect()
        
        # Wait for system to adapt
        await asyncio.sleep(10)
        
        # Verify system continues operating
        post_incident_active = 0
        for symbol in critical_symbols:
            price = cache.get_price(symbol)
            if price:
                age = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
                if age < 120:  # More lenient during incident
                    post_incident_active += 1
        
        print(f"Post-incident active symbols: {post_incident_active}/{len(critical_symbols)}")
        
        # Phase 5: Recovery
        print("Phase 5: System recovery")
        await primary.connect()
        await asyncio.sleep(15)
        
        # Final validation
        recovered_symbols = 0
        for symbol in critical_symbols:
            price = cache.get_price(symbol)
            if price:
                age = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
                if age < 60:
                    recovered_symbols += 1
        
        print(f"Recovered symbols: {recovered_symbols}/{len(critical_symbols)}")
        
        # Production readiness criteria
        initial_success = active_symbols / len(critical_symbols) >= 0.9
        incident_resilience = post_incident_active / len(critical_symbols) >= 0.5
        recovery_success = recovered_symbols / len(critical_symbols) >= 0.8
        
        print(f"📊 Production readiness assessment:")
        print(f"  Initial operation: {'PASS' if initial_success else 'FAIL'} ({active_symbols}/{len(critical_symbols)})")
        print(f"  Incident resilience: {'PASS' if incident_resilience else 'FAIL'} ({post_incident_active}/{len(critical_symbols)})")
        print(f"  Recovery capability: {'PASS' if recovery_success else 'FAIL'} ({recovered_symbols}/{len(critical_symbols)})")
        
        overall_ready = initial_success and incident_resilience and recovery_success
        print(f"  Overall readiness: {'READY FOR PRODUCTION' if overall_ready else 'NOT READY'}")
        
        assert overall_ready, "System not ready for production deployment"
        
    finally:
        await manager.stop()
    
    print("✅ Full production simulation completed successfully")


if __name__ == "__main__":
    print("🏭 Running production readiness tests...")
    print("   - These tests validate production deployment readiness")
    print("   - Tests use real exchange connections")
    print("   - Tests may take several minutes to complete")
    
    pytest.main(["-v", "-s", __file__, "--tb=short"])