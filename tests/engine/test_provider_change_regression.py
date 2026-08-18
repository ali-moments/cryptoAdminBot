"""
Regression test for provider change bug.

Tests the exact scenario that broke in production:
- Tracking in TRACKING status  
- Provider change event
- Price tick that should trigger TP
- Verify track()/process() is called without restart
"""

import pytest
import asyncio
from decimal import Decimal
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock

from app.market.dto import PriceTick
from app.database.enums import TrackingStatus, SignalStatus, Direction, Provider
from app.database.models import Signal, Tracking
from app.engine.tracking_manager import TrackingManager
from app.market.events import ProviderChangedEvent


class TestProviderChangeRegression:
    """Regression test for the provider change initialization bug."""

    def create_tracking_scenario(self):
        """Create a tracking in TRACKING status ready for TP."""
        signal = Signal()
        signal.id = 1
        signal.symbol = "BTCUSDT"
        signal.direction = Direction.LONG
        signal.status = SignalStatus.TRACKING
        
        tracking = Tracking()
        tracking.id = 1
        tracking.signal = signal
        tracking.status = TrackingStatus.TRACKING
        tracking.entry1_touched = True
        tracking.peak_price = Decimal("50500")
        
        return tracking
    
    def create_tp_price_tick(self):
        """Create a price tick that should trigger TP."""
        return PriceTick(
            provider=Provider.BINANCE,
            symbol="BTCUSDT",
            price=Decimal("51000"),  # TP price
            timestamp=datetime.now(UTC)
        )
    
    def setup_mocks(self, tracking):
        """Setup mock dependencies."""
        # Create shared mock that persists across UOW instances
        shared_trackings_mock = AsyncMock()
        shared_trackings_mock.get_active.return_value = [tracking]
        
        class MockUOW:
            def __init__(self):
                self.trackings = shared_trackings_mock
                
            async def __aenter__(self):
                return self
                
            async def __aexit__(self, *args):
                pass
                
            async def commit(self):
                pass
        
        mock_uow_factory = lambda: MockUOW()
        
        mock_cache = MagicMock()
        mock_cache.get.return_value = self.create_tp_price_tick()
        
        mock_tracker = AsyncMock()
        mock_tp_action = MagicMock()
        mock_tp_action.__class__.__name__ = "TakeProfitHit"
        mock_tracker.track.return_value = [mock_tp_action]
        
        mock_processor = AsyncMock()
        
        return mock_uow_factory, mock_cache, mock_tracker, mock_processor

    async def test_provider_change_continues_normal_processing(self):
        """
        Regression test: After provider change, TRACKING-status trackings
        should continue normal processing without getting stuck in re-initialization.
        """
        tracking = self.create_tracking_scenario()
        mock_uow_factory, mock_cache, mock_tracker, mock_processor = self.setup_mocks(tracking)
        
        # Create TrackingManager
        manager = TrackingManager(
            uow_factory=mock_uow_factory,
            cache=mock_cache,
            tracker=mock_tracker,
            processor=mock_processor,
            interval=0.1
        )
        
        # Step 1: Normal startup - establish baseline
        await manager._tick()
        
        assert tracking.id in manager._initialized_trackings
        assert mock_tracker.track.call_count == 1
        assert mock_processor.process.call_count == 1
        
        # Reset counters
        mock_tracker.track.reset_mock()
        mock_processor.process.reset_mock()
        
        # Step 2: Provider change event (this was causing the bug)
        event = ProviderChangedEvent(previous=Provider.BINANCE, current=Provider.BYBIT)
        await manager.on_provider_changed(event)
        
        # CRITICAL: Initialization state should NOT be cleared
        assert tracking.id in manager._initialized_trackings, \
            "Provider change should not clear initialization state"
        
        # Step 3: Next tick - should process normally without re-initialization
        await manager._tick()
        
        # Verify normal processing occurred (not re-initialization)
        assert mock_tracker.track.call_count == 1, \
            "track() should be called after provider change"
        assert mock_processor.process.call_count == 1, \
            "Actions should be processed after provider change"
        
        # Verify tracking remains initialized (no reset loop)
        assert tracking.id in manager._initialized_trackings, \
            "Tracking should remain initialized after provider change"

    async def test_multiple_provider_changes_stability(self):
        """
        Test that multiple rapid provider changes don't corrupt state.
        """
        tracking = self.create_tracking_scenario()
        mock_uow_factory, mock_cache, mock_tracker, mock_processor = self.setup_mocks(tracking)
        
        manager = TrackingManager(
            uow_factory=mock_uow_factory,
            cache=mock_cache,
            tracker=mock_tracker,
            processor=mock_processor,
            interval=0.1
        )
        
        # Initialize normally
        await manager._tick()
        assert mock_tracker.track.call_count == 1
        
        # Multiple provider changes
        events = [
            ProviderChangedEvent(previous=Provider.BINANCE, current=Provider.BYBIT),
            ProviderChangedEvent(previous=Provider.BYBIT, current=Provider.OKX), 
            ProviderChangedEvent(previous=Provider.OKX, current=Provider.BINANCE),
        ]
        
        for event in events:
            mock_tracker.track.reset_mock()
            mock_processor.process.reset_mock()
            
            # Provider change
            await manager.on_provider_changed(event)
            
            # Should still be initialized
            assert tracking.id in manager._initialized_trackings
            
            # Next tick should work normally
            await manager._tick()
            
            # Should process actions normally
            assert mock_tracker.track.call_count == 1
            assert mock_processor.process.call_count == 1

    async def test_waiting_entry_vs_tracking_behavior(self):
        """
        Verify that WAITING_ENTRY and TRACKING trackings behave correctly after provider changes.
        """
        # Create both types of trackings
        waiting_signal = Signal()
        waiting_signal.id = 2
        waiting_signal.symbol = "ETHUSDT"
        waiting_signal.direction = Direction.LONG
        waiting_signal.status = SignalStatus.WAITING_ENTRY
        
        waiting_tracking = Tracking()
        waiting_tracking.id = 2
        waiting_tracking.signal = waiting_signal
        waiting_tracking.status = TrackingStatus.WAITING_ENTRY
        waiting_tracking.entry1_touched = False
        
        tracking_tracking = self.create_tracking_scenario()
        
        # Setup mocks for both trackings
        shared_trackings_mock = AsyncMock()
        shared_trackings_mock.get_active.return_value = [waiting_tracking, tracking_tracking]
        
        class MockUOW:
            def __init__(self):
                self.trackings = shared_trackings_mock
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def commit(self):
                pass
        
        mock_cache = MagicMock()
        mock_cache.get.return_value = self.create_tp_price_tick()
        
        mock_tracker = AsyncMock()
        # Fix: _get_ordered_entries should be a regular method, not async
        mock_tracker._get_ordered_entries = MagicMock(return_value=(None, None))
        mock_tracker.track.return_value = []  # No actions for simplicity
        
        mock_processor = AsyncMock()
        
        manager = TrackingManager(
            uow_factory=lambda: MockUOW(),
            cache=mock_cache,
            tracker=mock_tracker,
            processor=mock_processor,
            interval=0.1
        )
        
        # Initialize both trackings
        await manager._tick()
        
        assert waiting_tracking.id in manager._initialized_trackings
        assert tracking_tracking.id in manager._initialized_trackings
        
        # Provider change - should not affect either tracking's state
        event = ProviderChangedEvent(previous=Provider.BINANCE, current=Provider.BYBIT)
        await manager.on_provider_changed(event)
        
        # Both should remain initialized
        assert waiting_tracking.id in manager._initialized_trackings
        assert tracking_tracking.id in manager._initialized_trackings
        
        # Next tick should continue normally for both
        mock_tracker.track.reset_mock()
        await manager._tick()
        
        # Both should have been processed normally (2 track() calls)
        assert mock_tracker.track.call_count == 2


if __name__ == "__main__":
    import asyncio
    
    async def run_tests():
        test = TestProviderChangeRegression()
        
        print("Running regression test 1: Provider change continues normal processing")
        await test.test_provider_change_continues_normal_processing()
        print("✅ Test 1 passed")
        
        print("Running regression test 2: Multiple provider changes stability")
        await test.test_multiple_provider_changes_stability()
        print("✅ Test 2 passed")
        
        print("Running regression test 3: WAITING_ENTRY vs TRACKING behavior")
        await test.test_waiting_entry_vs_tracking_behavior()
        print("✅ Test 3 passed")
        
        print("\n🎉 All regression tests passed!")
    
    asyncio.run(run_tests())