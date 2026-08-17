"""
Regression tests for TP Recovery Business Requirements.

These tests validate the core business requirement:
"Every crossed TP must eventually be emitted exactly once."

Tests cover specific scenarios from the business requirements:
1. Engine restart while price crosses multiple TPs
2. Engine downtime followed by price crossing multiple TPs  
3. Provider switch followed by crossed TPs
4. Multiple missed TPs recovered in order
5. Already-processed TPs are never emitted again

The invariant: Every crossed TP must eventually be emitted exactly once.
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
from app.engine.actions import TakeProfitHit, TrackingCompleted


def create_test_tracking_long(current_time: datetime, highest_target_hit: int = 0) -> Tracking:
    """
    Create LONG test scenario:
    Entry = 100, TP1 = 110, TP2 = 120, TP3 = 130
    """
    signal = MagicMock(spec=Signal)
    signal.id = 9001
    signal.symbol = "LONGUSDT"
    signal.direction = Direction.LONG
    signal.leverage = 10
    signal.expires_at = current_time + timedelta(hours=2)
    
    # Entry at 100
    entry1 = MagicMock(spec=SignalEntry)
    entry1.price = Decimal("100")
    entry1.position = 1
    signal.entries = [entry1]
    
    # Targets: TP1=110, TP2=120, TP3=130
    targets = []
    for i, price in enumerate([110, 120, 130], 1):
        target = MagicMock(spec=SignalTarget)
        target.price = Decimal(str(price))
        target.position = i
        targets.append(target)
    signal.targets = targets
    
    # Tracking already entered
    tracking = MagicMock(spec=Tracking)
    tracking.id = 9001
    tracking.signal = signal
    tracking.signal_id = signal.id
    tracking.status = TrackingStatus.TRACKING
    tracking.is_active = True
    tracking.started_at = current_time - timedelta(minutes=30)
    
    # Entry state
    tracking.entry1_touched = True
    tracking.entry2_touched = False
    tracking.entry_method = EntryMethod.ENTRY_1
    tracking.actual_entry_price = Decimal("100")
    tracking.has_entered = True
    
    # TP state
    tracking.highest_target_hit = highest_target_hit
    tracking.current_tp1_price = Decimal("110")
    tracking.current_stop_loss = Decimal("90")
    
    # Peak tracking
    tracking.peak_price_after_entry = Decimal("105")
    tracking.halfway_to_tp1_reached = False
    
    return tracking


def create_test_tracking_short(current_time: datetime, highest_target_hit: int = 0) -> Tracking:
    """
    Create SHORT test scenario:
    Entry = 100, TP1 = 90, TP2 = 80, TP3 = 70
    """
    signal = MagicMock(spec=Signal)
    signal.id = 9002
    signal.symbol = "SHORTUSDT"
    signal.direction = Direction.SHORT
    signal.leverage = 10
    signal.expires_at = current_time + timedelta(hours=2)
    
    # Entry at 100
    entry1 = MagicMock(spec=SignalEntry)
    entry1.price = Decimal("100")
    entry1.position = 1
    signal.entries = [entry1]
    
    # Targets: TP1=90, TP2=80, TP3=70
    targets = []
    for i, price in enumerate([90, 80, 70], 1):
        target = MagicMock(spec=SignalTarget)
        target.price = Decimal(str(price))
        target.position = i
        targets.append(target)
    signal.targets = targets
    
    # Tracking already entered
    tracking = MagicMock(spec=Tracking)
    tracking.id = 9002
    tracking.signal = signal
    tracking.signal_id = signal.id
    tracking.status = TrackingStatus.TRACKING
    tracking.is_active = True
    tracking.started_at = current_time - timedelta(minutes=30)
    
    # Entry state
    tracking.entry1_touched = True
    tracking.entry2_touched = False
    tracking.entry_method = EntryMethod.ENTRY_1
    tracking.actual_entry_price = Decimal("100")
    tracking.has_entered = True
    
    # TP state
    tracking.highest_target_hit = highest_target_hit
    tracking.current_tp1_price = Decimal("90")
    tracking.current_stop_loss = Decimal("110")
    
    # Peak tracking
    tracking.peak_price_after_entry = Decimal("95")
    tracking.halfway_to_tp1_reached = False
    
    return tracking


class MockUoW:
    def __init__(self, trackings):
        self.trackings = AsyncMock()
        self.trackings.get_active.return_value = trackings
        self.commit = AsyncMock()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        pass


async def setup_manager_and_process_tick(tracking, tick):
    """Helper to setup manager and process a tick."""
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
    
    # Simulate tracking already initialized
    manager._initialized_trackings.add(tracking.id)
    
    # Set price in cache
    cache._prices[tracking.signal.symbol] = tick
    
    # Process tick
    await manager._tick()
    
    return processor


@pytest.mark.asyncio
async def test_engine_restart_multiple_tp_crossings():
    """
    Business Requirement Test: Engine restart while price crosses multiple TPs
    
    Scenario:
    - TP1=110, TP2=120, TP3=130
    - Engine last processed price=105
    - Engine restarts and receives price=135
    - MUST emit TP1, TP2, TP3 (all crossed)
    """
    current_time = datetime.now(timezone.utc)
    
    # Create tracking with no TPs processed
    tracking = create_test_tracking_long(current_time, highest_target_hit=0)
    
    # Engine restarts at price 135 (crosses all targets)
    tick = PriceTick(
        symbol="LONGUSDT",
        price=Decimal("135"),
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    
    processor = await setup_manager_and_process_tick(tracking, tick)
    
    # Verify all TPs recovered
    assert processor.process.call_count == 1
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    tp_positions = [a.target_number for a in tp_actions]
    completion_actions = [a for a in actions_arg if isinstance(a, TrackingCompleted)]
    
    # All targets must be recovered in order
    assert tp_positions == [1, 2, 3], f"Must recover all TPs [1,2,3], got {tp_positions}"
    assert len(completion_actions) == 1, "Must emit TrackingCompleted after all TPs"
    
    print("✅ ENGINE RESTART RECOVERY - All TPs recovered after restart at price 135")


@pytest.mark.asyncio 
async def test_engine_downtime_partial_tp_crossings():
    """
    Business Requirement Test: Engine downtime followed by partial TP crossings
    
    Scenario:
    - TP1=110, TP2=120, TP3=130  
    - Engine was offline during 105 → 125 move
    - Engine resumes at 125 (crosses TP1, TP2 but not TP3)
    - MUST emit TP1, TP2 but NOT TP3
    """
    current_time = datetime.now(timezone.utc)
    
    tracking = create_test_tracking_long(current_time, highest_target_hit=0)
    
    # Engine resumes at 125 (partial crossing)
    tick = PriceTick(
        symbol="LONGUSDT",
        price=Decimal("125"),
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    
    processor = await setup_manager_and_process_tick(tracking, tick)
    
    # Verify partial recovery
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    tp_positions = [a.target_number for a in tp_actions]
    completion_actions = [a for a in actions_arg if isinstance(a, TrackingCompleted)]
    
    # Only TP1, TP2 should be recovered
    assert tp_positions == [1, 2], f"Must recover only TP1,TP2, got {tp_positions}"
    assert len(completion_actions) == 0, "Must NOT emit TrackingCompleted (TP3 not hit)"
    
    print("✅ ENGINE DOWNTIME RECOVERY - Partial TP recovery at price 125")


@pytest.mark.asyncio
async def test_provider_switch_with_crossed_tps():
    """
    Business Requirement Test: Provider switch followed by crossed TPs
    
    Scenario:
    - Engine running on BINANCE, last price 105
    - Switch to BYBIT provider
    - First BYBIT tick at 125 (crosses TP1, TP2)
    - MUST recover TP1, TP2 despite provider change
    """
    current_time = datetime.now(timezone.utc)
    
    tracking = create_test_tracking_long(current_time, highest_target_hit=0)
    
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
    
    # Tracking initially processed on BINANCE
    manager._initialized_trackings.add(tracking.id)
    
    # Simulate provider change
    provider_event = ProviderChangedEvent(
        previous=Provider.BINANCE,
        current=Provider.BYBIT
    )
    await manager.on_provider_changed(provider_event)
    
    # First BYBIT tick at 125
    tick = PriceTick(
        symbol="LONGUSDT",
        price=Decimal("125"),
        provider=Provider.BYBIT,
        timestamp=current_time,
    )
    cache._prices["LONGUSDT"] = tick
    
    await manager._tick()
    
    # Verify recovery works despite provider change
    assert processor.process.call_count >= 1
    
    # Find TP actions across all calls
    tp_actions = []
    for call in processor.process.call_args_list:
        _, actions_arg, _ = call[0]
        tp_actions.extend([a for a in actions_arg if isinstance(a, TakeProfitHit)])
    
    tp_positions = [a.target_number for a in tp_actions]
    
    assert 1 in tp_positions, "Must recover TP1 despite provider change"
    assert 2 in tp_positions, "Must recover TP2 despite provider change"
    assert 3 not in tp_positions, "Must NOT emit TP3 (not crossed)"
    
    print("✅ PROVIDER SWITCH RECOVERY - TPs recovered after BINANCE → BYBIT switch")


@pytest.mark.asyncio
async def test_multiple_missed_tps_correct_order():
    """
    Business Requirement Test: Multiple missed TPs recovered in correct order
    
    Scenario:
    - Engine offline during gap: 105 → 135
    - Must recover TP1, TP2, TP3 in exact order [1, 2, 3]
    """
    current_time = datetime.now(timezone.utc)
    
    tracking = create_test_tracking_long(current_time, highest_target_hit=0)
    
    # Large price gap covers all targets
    tick = PriceTick(
        symbol="LONGUSDT",
        price=Decimal("135"),
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    
    processor = await setup_manager_and_process_tick(tracking, tick)
    
    # Verify correct ordering
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    
    # Must be in exact order
    assert len(tp_actions) == 3, f"Must recover 3 TPs, got {len(tp_actions)}"
    assert tp_actions[0].target_number == 1, "First action must be TP1"
    assert tp_actions[1].target_number == 2, "Second action must be TP2"
    assert tp_actions[2].target_number == 3, "Third action must be TP3"
    
    print("✅ MULTIPLE TP ORDERING - TPs recovered in correct order [1, 2, 3]")


@pytest.mark.asyncio
async def test_processed_tps_never_reemitted():
    """
    Business Requirement Test: Already-processed TPs are never emitted again
    
    Scenario:
    - TP1, TP2 already processed (highest_target_hit=2)
    - Price moves to 135 (crosses TP3)
    - Must emit ONLY TP3, never re-emit TP1, TP2
    """
    current_time = datetime.now(timezone.utc)
    
    # Tracking with TP1, TP2 already processed
    tracking = create_test_tracking_long(current_time, highest_target_hit=2)
    
    # Price crosses TP3
    tick = PriceTick(
        symbol="LONGUSDT",
        price=Decimal("135"),
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    
    processor = await setup_manager_and_process_tick(tracking, tick)
    
    # Verify only TP3 emitted
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    tp_positions = [a.target_number for a in tp_actions]
    
    # Only TP3 should be emitted
    assert tp_positions == [3], f"Must emit only TP3, got {tp_positions}"
    
    print("✅ IDEMPOTENCY VERIFIED - Only new TP3 emitted, no duplicates")


@pytest.mark.asyncio
async def test_short_position_tp_recovery():
    """
    Business Requirement Test: TP recovery works for SHORT positions
    
    Scenario:
    - SHORT position: Entry=100, TP1=90, TP2=80, TP3=70
    - Engine offline during 95 → 65 move
    - Must recover all TPs when price drops below targets
    """
    current_time = datetime.now(timezone.utc)
    
    tracking = create_test_tracking_short(current_time, highest_target_hit=0)
    
    # Price drops to 65 (crosses all SHORT targets)
    tick = PriceTick(
        symbol="SHORTUSDT",
        price=Decimal("65"),
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    
    processor = await setup_manager_and_process_tick(tracking, tick)
    
    # Verify SHORT recovery
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    tp_positions = [a.target_number for a in tp_actions]
    completion_actions = [a for a in actions_arg if isinstance(a, TrackingCompleted)]
    
    # All SHORT targets recovered
    assert tp_positions == [1, 2, 3], f"Must recover all SHORT TPs [1,2,3], got {tp_positions}"
    assert len(completion_actions) == 1, "Must emit TrackingCompleted"
    
    print("✅ SHORT POSITION RECOVERY - All SHORT TPs recovered at price 65")


@pytest.mark.asyncio
async def test_exact_business_requirement_scenario():
    """
    Business Requirement Test: Exact scenario from requirements
    
    TP1 = 110, TP2 = 120, TP3 = 130
    Engine last processed price = 105
    Engine goes offline
    Market moves: 105 → 115 → 125 → 135  
    Engine restarts and receives 135
    MUST emit: TP1, TP2, TP3
    """
    current_time = datetime.now(timezone.utc)
    
    # Exact scenario from requirements
    tracking = create_test_tracking_long(current_time, highest_target_hit=0)
    
    # Engine receives 135 after offline period
    tick = PriceTick(
        symbol="LONGUSDT",
        price=Decimal("135"),
        provider=Provider.BINANCE,
        timestamp=current_time,
    )
    
    processor = await setup_manager_and_process_tick(tracking, tick)
    
    # Exact verification from business requirements
    call_args = processor.process.call_args_list[0]
    _, actions_arg, _ = call_args[0]
    
    tp_actions = [a for a in actions_arg if isinstance(a, TakeProfitHit)]
    
    # Must emit TP1, TP2, TP3 as required
    assert len(tp_actions) == 3, "Must emit exactly 3 TP actions"
    assert tp_actions[0].target_number == 1, "Must emit TP1"
    assert tp_actions[1].target_number == 2, "Must emit TP2" 
    assert tp_actions[2].target_number == 3, "Must emit TP3"
    
    # Verify prices match targets
    assert tp_actions[0].price == Decimal("110"), "TP1 price must be 110"
    assert tp_actions[1].price == Decimal("120"), "TP2 price must be 120"
    assert tp_actions[2].price == Decimal("130"), "TP3 price must be 130"
    
    print("✅ BUSINESS REQUIREMENT SCENARIO VERIFIED")
    print("   Engine offline: 105 → 135")
    print("   Recovered: TP1(110), TP2(120), TP3(130)")
    print("   All crossed TPs emitted exactly once")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])