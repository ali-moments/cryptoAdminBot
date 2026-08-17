"""
Regression test for missed TP recovery - the CORRECT business requirement.

Tests that TP targets are NEVER LOST and can be recovered after:
- Engine restart
- Provider change 
- Temporary outages
- Stuck processing

The system MUST recover missed targets based on current price vs persisted state.
"""
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.database.enums import Direction, TrackingStatus, Provider, EntryMethod
from app.database.models import Tracking, Signal, SignalEntry, SignalTarget
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.market.events import ProviderChangedEvent
from app.engine.tracker import Tracker
from app.engine.action_processor import ActionProcessor
from app.engine.tracking_manager import TrackingManager
from app.engine.actions import TakeProfitHit


def create_test_tracking_scenario(current_time: datetime) -> Tracking:
    """
    Create test scenario:
    Entry = 100, TP1 = 110, TP2 = 120, TP3 = 130
    Tracking is active and entered, no targets processed yet.
    """
    # Create realistic signal
    signal = MagicMock(spec=Signal)
    signal.id = 8001
    signal.symbol = "RECOVERYUSDT"
    signal.direction = Direction.LONG
    signal.leverage = 10
    signal.expires_at = current_time + timedelta(hours=2)  # Not expired
    
    # Create entries: Entry = 100
    entry1 = MagicMock(spec=SignalEntry)
    entry1.price = Decimal("100")
    entry1.position = 1
    signal.entries = [entry1]
    
    # Create targets: TP1=110, TP2=120, TP3=130
    target1 = MagicMock(spec=SignalTarget)
    target1.price = Decimal("110")
    target1.position = 1
    
    target2 = MagicMock(spec=SignalTarget)
    target2.price = Decimal("120")
    target2.position = 2
    
    target3 = MagicMock(spec=SignalTarget)
    target3.price = Decimal("130")
    target3.position = 3
    
    signal.targets = [target1, target2, target3]
    
    # Create tracking - already entered but no TPs processed
    tracking = MagicMock(spec=Tracking)
    tracking.id = 8001
    tracking.signal = signal
    tracking.signal_id = signal.id
    tracking.status = TrackingStatus.TRACKING  # Already tracking
    tracking.is_active = True
    tracking.started_at = current_time - timedelta(minutes=30)
    
    # Entry state: entered at 100
    tracking.entry1_touched = True
    tracking.entry2_touched = False
    tracking.entry_method = EntryMethod.ENTRY_1
    tracking.actual_entry_price = Decimal("100")
    tracking.has_entered = True
    
    # TP state: NO targets processed yet
    tracking.highest_target_hit = 0  # KEY: No TPs processed
    tracking.current_tp1_price = Decimal("110")
    tracking.current_stop_loss = Decimal("90")
    
    # Peak tracking
    tracking.peak_price_after_entry = Decimal("105")  # Started at 105
    tracking.halfway_to_tp1_reached = False
    
    return tracking


class MockUoW:
    """Proper async context manager mock."""
    def __init__(self, trackings):
        self.trackings = AsyncMock()
        self.trackings.get_active.return_value = trackings
        self.commit = AsyncMock()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.mark.asyncio
async def test_missed_tp_recovery_after_engine_gap():
    """
    CORE BUSINESS REQUIREMENT TEST:
    
    Engine processes price at 105, then goes offline.
    Market moves: 105 → 110 → 120 → 125 (crosses TP1, TP2)  
    Engine resumes at 125.
    MUST recover TP1 and TP2, but NOT TP3 (130 not reached).
    """
    current_time = datetime.now(timezone.utc)
    
    # Create scenario: tracking entered at 100, no TPs processed
    tracking = create_test_tracking_scenario(current_time)
    
    # Create manager with proper mocking
    cache = PriceCache()
    tracker = Tracker()
    processor = AsyncMock(spec=ActionProcessor)
    
    def mock_uow_factory():
        return MockUoW([tracking])
    
    manager = TrackingManager(
        uow_factory=mock_uow_factory,
        tracker=tracker,
        processor=processor,
        cache=cache,
        interval=2.0,
    )
    
    # Simulate tracking was initialized (normal state)
    manager._initialized_trackings.add(tracking.id)
    
    # === ENGINE RESUMES AT PRICE 125 ===
    # (Missed 110 TP1 and 120 TP2, but should recover both)
    
    resume_price = Decimal("125")
    tick = PriceTick(
        symbol="RECOVERYUSDT",
        price=resume_price,
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    cache._prices["RECOVERYUSDT"] = tick
    
    # Process tick - should recover missed TPs
    await manager._tick()
    
    # === VERIFY MISSED TP RECOVERY ===
    
    assert processor.process.call_count == 1, "Should process actions for missed TPs"
    
    # Examine the actions that were emitted
    call_args = processor.process.call_args_list[0]
    tracking_arg, actions_arg, uow_arg = call_args[0]
    
    print(f"Actions recovered: {[action.__class__.__name__ for action in actions_arg]}")
    
    # Extract TP actions
    tp_actions = [action for action in actions_arg if isinstance(action, TakeProfitHit)]
    tp_positions = [action.target_number for action in tp_actions]
    
    # BUSINESS REQUIREMENT: Must recover TP1 and TP2 (both crossed by 125)
    assert 1 in tp_positions, "Must recover missed TP1 (110)"
    assert 2 in tp_positions, "Must recover missed TP2 (120)" 
    assert 3 not in tp_positions, "Must NOT generate TP3 (130) - not crossed"
    
    # Should be in correct order
    assert tp_positions == [1, 2], f"TPs should be in order [1,2], got {tp_positions}"
    
    print("✅ MISSED TP RECOVERY VERIFIED")
    print(f"  - Recovered TP1 and TP2 when resuming at price {resume_price}")
    print(f"  - Correctly did not generate TP3 (not crossed)")


@pytest.mark.asyncio  
async def test_missed_tp_recovery_all_targets():
    """
    Test recovery when price gaps over ALL targets.
    
    Engine offline: 105 → 135 (crosses TP1=110, TP2=120, TP3=130)
    MUST recover all three targets.
    """
    current_time = datetime.now(timezone.utc)
    
    # Create scenario
    tracking = create_test_tracking_scenario(current_time)
    
    # Setup manager
    cache = PriceCache()
    tracker = Tracker()
    processor = AsyncMock()
    
    def mock_uow_factory():
        return MockUoW([tracking])
    
    manager = TrackingManager(
        uow_factory=mock_uow_factory,
        tracker=tracker,
        processor=processor,
        cache=cache,
        interval=2.0,
    )
    
    manager._initialized_trackings.add(tracking.id)
    
    # === ENGINE RESUMES AT PRICE 135 (beyond all targets) ===
    
    gap_price = Decimal("135")
    tick = PriceTick(
        symbol="RECOVERYUSDT",
        price=gap_price,
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    cache._prices["RECOVERYUSDT"] = tick
    
    # Process tick
    await manager._tick()
    
    # === VERIFY ALL TARGETS RECOVERED ===
    
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [action for action in actions_arg if isinstance(action, TakeProfitHit)]
    tp_positions = [action.target_number for action in tp_actions]
    
    # All targets should be recovered
    assert tp_positions == [1, 2, 3], f"Must recover all targets [1,2,3], got {tp_positions}"
    
    print(f"✅ ALL TARGETS RECOVERED when jumping to price {gap_price}")


@pytest.mark.asyncio
async def test_tp_idempotency_no_duplicates():
    """
    Test that after successful TP processing, no duplicates are generated.
    
    Scenario:
    1. Recover TP1, TP2 at price 125
    2. Process successfully (highest_target_hit = 2)  
    3. Next tick at 126 should NOT regenerate TP1, TP2
    4. Next tick at 135 should generate only TP3
    """
    current_time = datetime.now(timezone.utc)
    
    # Create scenario where TP1, TP2 already processed
    tracking = create_test_tracking_scenario(current_time)
    tracking.highest_target_hit = 2  # TP1, TP2 already processed
    
    # Setup manager
    cache = PriceCache()
    tracker = Tracker()
    processor = AsyncMock()
    
    def mock_uow_factory():
        return MockUoW([tracking])
    
    manager = TrackingManager(
        uow_factory=mock_uow_factory,
        tracker=tracker,
        processor=processor,
        cache=cache,
        interval=2.0,
    )
    
    manager._initialized_trackings.add(tracking.id)
    
    # === TICK 1: Price 126 (TP1, TP2 already processed) ===
    
    tick1 = PriceTick(
        symbol="RECOVERYUSDT",
        price=Decimal("126"),
        provider=Provider.BINANCE, 
        timestamp=current_time,
    )
    cache._prices["RECOVERYUSDT"] = tick1
    
    await manager._tick()
    
    # Should generate no actions (TP1, TP2 already processed)
    if processor.process.call_count > 0:
        call_args = processor.process.call_args_list[-1]
        _, actions_arg, _ = call_args[0]
        tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
        assert len(tp_actions) == 0, f"Should not regenerate processed TPs, got: {[a.target_number for a in tp_actions]}"
    
    processor.process.reset_mock()
    
    # === TICK 2: Price 135 (crosses TP3) ===
    
    tick2 = PriceTick(
        symbol="RECOVERYUSDT",
        price=Decimal("135"),
        provider=Provider.BINANCE,
        timestamp=current_time + timedelta(seconds=2),
    )
    cache._prices["RECOVERYUSDT"] = tick2
    
    await manager._tick()
    
    # Should generate only TP3
    assert processor.process.call_count == 1, "Should process TP3"
    
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    tp_positions = [a.target_number for a in tp_actions]
    
    assert tp_positions == [3], f"Should generate only TP3, got {tp_positions}"
    
    print("✅ IDEMPOTENCY VERIFIED")
    print("  - No duplicate TP1, TP2 at price 126") 
    print("  - Only TP3 generated at price 135")


@pytest.mark.asyncio
async def test_provider_change_preserves_tp_recovery():
    """
    Test that provider change does NOT prevent TP recovery.
    
    This is the CORRECT test - provider change should not suppress 
    legitimate missed TP recovery.
    """
    current_time = datetime.now(timezone.utc)
    
    # Create scenario
    tracking = create_test_tracking_scenario(current_time)
    
    # Setup manager
    cache = PriceCache() 
    tracker = Tracker()
    processor = AsyncMock()
    
    def mock_uow_factory():
        return MockUoW([tracking])
    
    manager = TrackingManager(
        uow_factory=mock_uow_factory,
        tracker=tracker,
        processor=processor,
        cache=cache,
        interval=2.0,
    )
    
    # Start with tracking initialized
    manager._initialized_trackings.add(tracking.id)
    
    # === SIMULATE PROVIDER CHANGE ===
    
    provider_change_event = ProviderChangedEvent(
        previous=Provider.BINANCE,
        current=Provider.BYBIT
    )
    
    await manager.on_provider_changed(provider_change_event)
    
    # Tracking loses initialization (documented conservative behavior)
    assert tracking.id not in manager._initialized_trackings
    
    # === ENGINE PROCESSES PRICE 125 AFTER PROVIDER CHANGE ===
    
    tick = PriceTick(
        symbol="RECOVERYUSDT",
        price=Decimal("125"),  # Crosses TP1, TP2
        provider=Provider.BYBIT,
        timestamp=current_time,
    )
    cache._prices["RECOVERYUSDT"] = tick
    
    await manager._tick()
    
    # === VERIFY TP RECOVERY STILL WORKS ===
    
    # Should still recover missed TPs despite provider change
    assert processor.process.call_count >= 1, "Should still process TPs after provider change"
    
    # Find the TP actions
    tp_actions = []
    for call in processor.process.call_args_list:
        _, actions_arg, _ = call[0]
        tp_actions.extend([a for a in actions_arg if isinstance(a, TakeProfitHit)])
    
    tp_positions = [a.target_number for a in tp_actions]
    
    # Must still recover missed targets
    assert 1 in tp_positions, "Must still recover TP1 after provider change"
    assert 2 in tp_positions, "Must still recover TP2 after provider change"
    
    print("✅ PROVIDER CHANGE PRESERVES TP RECOVERY")
    print(f"  - Recovered TPs {tp_positions} after BINANCE → BYBIT switch")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])