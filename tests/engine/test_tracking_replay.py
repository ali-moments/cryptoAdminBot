"""
Deterministic tracking engine replay tests.

Tests complete signal lifecycle by feeding controlled market ticks into
real engine components (Tracker + Rules + ActionProcessor + DB).

No mocking of business logic.
Only engine validation.
"""
import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.database.enums import Direction, SignalStatus, TrackingStatus, Provider, AuditEventType, EntryMethod
from app.database.models import Signal, SignalEntry, SignalTarget, SignalSource
from app.database.uow import UnitOfWork
from app.database.db import engine as db_engine
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.engine.tracker import Tracker
from app.engine.action_processor import ActionProcessor
from app.engine.tracking_manager import TrackingManager


# ===========================================================================
# Pytest Event Loop Configuration
# ===========================================================================

# @pytest.fixture(scope="session")
# def event_loop_policy():
#     """Use default event loop policy."""
#     return asyncio.get_event_loop_policy()


@pytest.fixture(scope="function")
async def async_engine():
    """Provide database engine with proper cleanup."""
    yield db_engine
    # Clean up any lingering connections
    await db_engine.dispose()


# ===========================================================================
# Test Helpers
# ===========================================================================

async def create_test_source(unique_id: int) -> SignalSource:
    """Create a test signal source with unique identifier."""
    # Use timestamp + unique_id for true uniqueness across test runs
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    channel_id = timestamp_ms + unique_id
    
    async with UnitOfWork() as uow:
        source = await uow.signal_sources.create(
            name=f"Test Source {channel_id}",
            parser_name="test_parser",
            telegram_channel_id=channel_id,
            is_active=True,
        )
        await uow.commit()
        return source


async def create_signal_with_tracking(
    source_id: int,
    symbol: str,
    direction: Direction,
    entries: list[Decimal],
    targets: list[Decimal],
    stop_loss: Decimal,
    leverage: int = 10,
    provider: Provider = Provider.BINANCE,
    started_at: datetime = None,
) -> tuple[Signal, int]:
    """Create signal and tracking, return signal and tracking_id."""
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    
    async with UnitOfWork() as uow:
        # Create signal
        signal = await uow.signals.create(
            source_id=source_id,
            symbol=symbol,
            direction=direction,
            leverage=leverage,
            stop_loss=stop_loss,
            expires_at=started_at + timedelta(hours=2),
            status=SignalStatus.WAITING_ENTRY,
        )
        await uow.flush()
        
        # Create entries
        for i, price in enumerate(entries, start=1):
            await uow.signal_entries.create(
                signal_id=signal.id,
                position=i,
                price=price,
            )
        
        # Create targets
        for i, price in enumerate(targets, start=1):
            await uow.signal_targets.create(
                signal_id=signal.id,
                position=i,
                price=price,
            )
        
        # Create tracking with initial TP1
        initial_tp1 = targets[0] if targets else None
        
        tracking = await uow.trackings.create(
            signal_id=signal.id,
            status=TrackingStatus.WAITING_ENTRY,
            provider=provider,
            is_active=True,
            started_at=started_at,
            current_stop_loss=stop_loss,
            current_tp1_price=initial_tp1,
        )
        
        await uow.commit()
        
        # Reload with relationships
        signal = await uow.signals.get_full(signal.id)
        
    return signal, tracking.id


def feed_price(cache: PriceCache, symbol: str, price: Decimal, timestamp: datetime = None):
    """Feed a price tick to the cache."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    tick = PriceTick(
        symbol=symbol,
        price=price,
        provider=Provider.BINANCE,
        timestamp=timestamp,
    )
    
    cache._prices[symbol] = tick


async def run_tracking_tick(cache: PriceCache, tracker: Tracker):
    """Run a single tracking manager tick with fresh UoW."""
    uow = UnitOfWork()
    processor = ActionProcessor(uow)
    manager = TrackingManager(
        uow=uow,
        tracker=tracker,
        processor=processor,
        cache=cache,
        interval=2.0,
    )
    
    await manager._tick()


async def cleanup_test_data(signal_id: int, source_id: int):
    """Clean up test data."""
    async with UnitOfWork() as uow:
        # First, delete signal (cascade will handle trackings, entries, targets, etc.)
        signal = await uow.signals.get(signal_id)
        if signal:
            await uow.session.delete(signal)
            await uow.flush()  # Force signal deletion before source deletion
        
        # Then delete source
        source = await uow.signal_sources.get(source_id)
        if source:
            await uow.session.delete(source)
        
        await uow.commit()


# ===========================================================================
# Test Fixtures
# ===========================================================================

@pytest.fixture
def price_cache():
    """Fresh price cache."""
    return PriceCache()


@pytest.fixture
def tracker():
    """Fresh tracker instance."""
    return Tracker()


@pytest.fixture
def current_time():
    """Current timestamp for tests."""
    return datetime.now(timezone.utc)


# ===========================================================================
# Test 1: Emergency Entry Lifecycle
# ===========================================================================

@pytest.mark.asyncio
async def test_emergency_entry_lifecycle(async_engine, price_cache, tracker, current_time):
    """
    Test emergency entry scenario with Entry2 allowed:
    
    1. Signal created → WAITING_ENTRY
    2. Entry1 missed, 5 minutes pass → emergency entry calculated
    3. Emergency entry hit → PositionEntered (EMERGENCY_ENTRY)
    4. Entry2 CAN trigger after emergency entry (updated business rule)
    5. TP1 recalculated when Entry2 fires
    6. TP progression (TP1 recalculated, TP2)
    7. Stop loss hit → CLOSED
    """
    source = await create_test_source(1)
    
    try:
        # Step 1: Create LONG signal
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entries=[Decimal("50000"), Decimal("49000")],
            targets=[Decimal("52000"), Decimal("54000"), Decimal("56000")],
            stop_loss=Decimal("48000"),
            started_at=current_time,
        )
        
        # Verify initial state
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.WAITING_ENTRY
            assert tracking.has_entered is False
            assert tracking.entry1_touched is False
            assert tracking.entry2_touched is False
            assert tracking.actual_entry_price is None
        
        # Step 2: Feed prices ABOVE EntryHigh to avoid hitting it
        # EntryHigh = 50000, so feed prices above to miss it
        feed_price(price_cache, "BTCUSDT", Decimal("50100"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        feed_price(price_cache, "BTCUSDT", Decimal("50200"), current_time + timedelta(seconds=1))
        await run_tracking_tick(price_cache, tracker)
        
        # Step 3: Advance time by 5 minutes → emergency entry becomes available
        # Calculate expected emergency entry price (deterministic formula):
        # LONG: emergency = tp1 + (tp1 - EntryHigh) / 4
        # EntryHigh = 50000, tp1 = 52000
        # emergency = 52000 + (52000 - 50000) / 4 = 52000 + 500 = 52500
        expected_emergency_price = Decimal("52500")
        
        emergency_time = current_time + timedelta(minutes=5, seconds=10)
        
        # Feed a price that's NOT at emergency level yet - should not enter
        feed_price(price_cache, "BTCUSDT", Decimal("52400"), emergency_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.has_entered is False, "Should not enter yet - emergency price not hit"
        
        # Step 4: Hit emergency entry price - should enter
        feed_price(price_cache, "BTCUSDT", expected_emergency_price, emergency_time + timedelta(seconds=5))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify emergency entry triggered
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.EMERGENCY_ENTRY
            assert tracking.actual_entry_price == expected_emergency_price
            assert tracking.emergency_entry_triggered_at is not None
            assert tracking.entry2_touched is False
            assert tracking.status == TrackingStatus.TRACKING
            
            # Check audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            emergency_logs = [log for log in logs if log.event == AuditEventType.EMERGENCY_ENTRY_HIT]
            assert len(emergency_logs) == 1
        
        # Step 5: Feed entry2 price - SHOULD trigger after emergency (new business rule)
        feed_price(price_cache, "BTCUSDT", Decimal("49000"), emergency_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            # Business Rule: Entry2 CAN trigger after emergency entry (as long as no TP hit yet)
            assert tracking.entry2_touched is True, "Entry2 should trigger after emergency entry"
            
            # TP1 should be recalculated
            # EntryHigh = 50000, original TP1 = 52000
            # new_tp1 = 50000 + (52000 - 50000) / 2 = 50000 + 1000 = 51000
            expected_new_tp1 = Decimal("51000")
            assert tracking.current_tp1_price == expected_new_tp1
        
        # Step 6: TP progression - Hit TP1 (recalculated)
        feed_price(price_cache, "BTCUSDT", Decimal("51000"), emergency_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            assert tracking.is_active is True
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].position == 1
            assert tp_hits[0].price == Decimal("51000")  # Recalculated TP1
        
        # Hit TP2
        feed_price(price_cache, "BTCUSDT", Decimal("54000"), emergency_time + timedelta(minutes=2))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
            assert tp_hits[1].position == 2
        
        # Step 7: Stop loss hit
        feed_price(price_cache, "BTCUSDT", Decimal("48000"), emergency_time + timedelta(minutes=3))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            assert tracking.closed_at is not None
            
            # Check audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "stop_loss"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 2: Normal DCA Entry Lifecycle
# ===========================================================================

@pytest.mark.asyncio
async def test_normal_dca_entry_lifecycle(async_engine, price_cache, tracker, current_time):
    """
    Test normal DCA (Dollar Cost Averaging) scenario:
    
    1. Hit Entry1 (50000) → PositionEntered (ENTRY_1)
    2. Hit Entry2 (49000) → entry2_touched = True
    3. Verify TP1 is recalculated: new_tp1 = entry1 + (original_tp1 - entry1) / 2
    4. Verify TP uses new TP1 value
    """
    source = await create_test_source(2)
    
    try:
        # Create LONG signal
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entries=[Decimal("50000"), Decimal("49000")],
            targets=[Decimal("52000"), Decimal("54000"), Decimal("56000")],
            stop_loss=Decimal("48000"),
            started_at=current_time,
        )
        
        # Step 1: Hit Entry1
        feed_price(price_cache, "ETHUSDT", Decimal("50000"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("50000")
            assert tracking.entry2_touched is False
            assert tracking.status == TrackingStatus.TRACKING
            
            # Original TP1 should be set
            original_tp1 = tracking.current_tp1_price
            assert original_tp1 == Decimal("52000")
            
            # Check audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
        
        # Step 2: Hit Entry2 (price drops - DCA scenario)
        feed_price(price_cache, "ETHUSDT", Decimal("49000"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
            assert tracking.actual_entry_price == Decimal("50000"), "Entry price should remain at first entry"
            
            # Verify TP1 recalculation
            # Formula uses EntryHigh (higher price) for all directions
            # EntryHigh = entries[0] = 50000 (first entry, higher price for LONG)
            # new_tp1 = 50000 + (52000 - 50000) / 2 = 50000 + 1000 = 51000
            expected_new_tp1 = Decimal("51000")
            assert tracking.current_tp1_price == expected_new_tp1, f"TP1 should be recalculated to {expected_new_tp1}"
            
            # Check audit logs
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1
            
            tp1_recalc_logs = [log for log in logs if log.event == AuditEventType.TP1_RECALCULATED]
            assert len(tp1_recalc_logs) == 1, "Should have TP1 recalculation audit log"
        
        # Step 3: Hit NEW TP1 (51000, not original 52000)
        feed_price(price_cache, "ETHUSDT", Decimal("51000"), current_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].position == 1
            assert tp_hits[0].price == Decimal("51000"), "Should hit new TP1, not original"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 3: Complete Lifecycle Replay
# ===========================================================================

@pytest.mark.asyncio
async def test_complete_lifecycle_replay(async_engine, price_cache, tracker, current_time):
    """
    Test complete lifecycle:
    
    Signal → Entry1 → Entry2 → TP1 → TP2 → TP3 → Completed
    """
    source = await create_test_source(3)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="SOLUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100"), Decimal("95")],
            targets=[Decimal("105"), Decimal("110"), Decimal("115")],
            stop_loss=Decimal("90"),
            started_at=current_time,
        )
        
        # Entry1
        feed_price(price_cache, "SOLUSDT", Decimal("100"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.status == TrackingStatus.TRACKING
        
        # Entry2
        feed_price(price_cache, "SOLUSDT", Decimal("95"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
            # New TP1 calculation: EntryHigh = 100 (first entry, higher for LONG)
            # new_tp1 = 100 + (105 - 100) / 2 = 100 + 2.5 = 102.5
            assert tracking.current_tp1_price == Decimal("102.5")
        
        # TP1 (new TP1 = 102.5)
        feed_price(price_cache, "SOLUSDT", Decimal("102.5"), current_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            assert tracking.is_active is True
        
        # TP2
        feed_price(price_cache, "SOLUSDT", Decimal("110"), current_time + timedelta(minutes=2))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            assert tracking.is_active is True
        
        # TP3 (final)
        feed_price(price_cache, "SOLUSDT", Decimal("115"), current_time + timedelta(minutes=3))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 3
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            assert tracking.closed_at is not None
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 3
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "all_targets_hit"
    
    finally:
        await cleanup_test_data(signal.id, source.id)



# ===========================================================================
# Test 4: Idempotency Replay
# ===========================================================================

@pytest.mark.asyncio
async def test_idempotency_replay(async_engine, price_cache, tracker, current_time):
    """
    Test idempotency: duplicate ticks should not create duplicate actions.
    
    Send same tick multiple times, verify:
    - Only one TpHit record
    - Only one audit event
    - No duplicated state changes
    """
    source = await create_test_source(4)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BNBUSDT",
            direction=Direction.LONG,
            entries=[Decimal("300")],
            targets=[Decimal("310"), Decimal("320"), Decimal("330")],
            stop_loss=Decimal("290"),
            started_at=current_time,
        )
        
        # Enter position
        feed_price(price_cache, "BNBUSDT", Decimal("300"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        # Hit TP1
        feed_price(price_cache, "BNBUSDT", Decimal("310"), current_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            tp_logs = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 1
        
        # Send DUPLICATE tick for TP1 (10 times)
        for i in range(10):
            feed_price(price_cache, "BNBUSDT", Decimal("310"), current_time + timedelta(seconds=20 + i))
            await run_tracking_tick(price_cache, tracker)
        
        # Verify NO duplicates created
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1, f"Should have exactly 1 TP hit, got {len(tp_hits)}"
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            tp_logs = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 1, f"Should have exactly 1 TP audit log, got {len(tp_logs)}"
        
        # Now hit TP2 (should still work normally)
        feed_price(price_cache, "BNBUSDT", Decimal("320"), current_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 5: TP1 Crossed Before Entry
# ===========================================================================

@pytest.mark.asyncio
async def test_tp1_crossed_before_entry(async_engine, price_cache, tracker, current_time):
    """
    Test TP1 crossing protection:
    
    If TP1 is crossed before any entry, signal should be cancelled.
    """
    source = await create_test_source(5)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entries=[Decimal("0.50")],
            targets=[Decimal("0.55"), Decimal("0.60"), Decimal("0.65")],
            stop_loss=Decimal("0.45"),
            started_at=current_time,
        )
        
        # Feed price that crosses TP1 without hitting entry
        feed_price(price_cache, "ADAUSDT", Decimal("0.55"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        # Verify signal is cancelled
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CANCELLED
            assert tracking.is_active is False
            assert tracking.has_entered is False
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            expired_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_EXPIRED]
            assert len(expired_logs) == 1
            assert expired_logs[0].payload["reason"] == "tp1_crossed"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 6: Timeout Expiry
# ===========================================================================

@pytest.mark.asyncio
async def test_timeout_expiry(async_engine, price_cache, tracker, current_time):
    """
    Test timeout expiry:
    
    If signal expires after 2 hours without entry, should be cancelled.
    """
    source = await create_test_source(6)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="DOGEUSDT",
            direction=Direction.LONG,
            entries=[Decimal("0.10")],
            targets=[Decimal("0.11"), Decimal("0.12"), Decimal("0.13")],
            stop_loss=Decimal("0.09"),
            started_at=current_time,
        )
        
        # Feed price that doesn't hit entry, just before timeout
        feed_price(price_cache, "DOGEUSDT", Decimal("0.105"), current_time + timedelta(hours=1, minutes=59))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.is_active is True
        
        # Advance past timeout (2 hours + 1 minute)
        feed_price(price_cache, "DOGEUSDT", Decimal("0.105"), current_time + timedelta(hours=2, minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify expired
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CANCELLED
            assert tracking.is_active is False
            assert tracking.has_entered is False
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            expired_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_EXPIRED]
            assert len(expired_logs) == 1
            assert expired_logs[0].payload["reason"] == "timeout"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 7: SHORT Signal Flow
# ===========================================================================

@pytest.mark.asyncio
async def test_short_signal_flow(async_engine, price_cache, tracker, current_time):
    """
    Test SHORT signal with inverted logic:
    
    - Entry: price goes UP to entry
    - TP: price goes DOWN to TP
    - SL: price goes UP to SL
    """
    source = await create_test_source(7)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BTCUSDT",
            direction=Direction.SHORT,
            entries=[Decimal("50000"), Decimal("51000")],
            targets=[Decimal("49000"), Decimal("48000"), Decimal("47000")],
            stop_loss=Decimal("52000"),
            started_at=current_time,
        )
        
        # Entry1 (50000 - lower entry for SHORT)
        feed_price(price_cache, "BTCUSDT", Decimal("50000"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.actual_entry_price == Decimal("50000")
        
        # Entry2 (51000 - higher entry for SHORT, DCA)
        feed_price(price_cache, "BTCUSDT", Decimal("51000"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
        
        # TP1 (49000 - price goes down)
        feed_price(price_cache, "BTCUSDT", Decimal("49000"), current_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
        
        # Stop loss (52000 - price goes up)
        feed_price(price_cache, "BTCUSDT", Decimal("52000"), current_time + timedelta(minutes=2))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 8: LONG Signal Flow
# ===========================================================================

@pytest.mark.asyncio
async def test_long_signal_flow(async_engine, price_cache, tracker, current_time):
    """
    Test LONG signal with normal logic:
    
    - Entry: price goes DOWN to entry
    - TP: price goes UP to TP
    - SL: price goes DOWN to SL
    
    Full lifecycle: Entry1 → Entry2 (DCA) → TP1 (recalculated) → TP2 → TP3 → Completed
    """
    source = await create_test_source(8)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entries=[Decimal("2000"), Decimal("1950")],
            targets=[Decimal("2100"), Decimal("2200"), Decimal("2300")],
            stop_loss=Decimal("1900"),
            started_at=current_time,
        )
        
        # Verify initial state
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.WAITING_ENTRY
            assert tracking.has_entered is False
            assert tracking.current_tp1_price == Decimal("2100")
        
        # Entry1 (2000 - higher entry for LONG, price goes down to hit it)
        feed_price(price_cache, "ETHUSDT", Decimal("2000"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("2000")
            assert tracking.status == TrackingStatus.TRACKING
            assert tracking.entry2_touched is False
            
            # Original TP1 should still be set
            assert tracking.current_tp1_price == Decimal("2100")
            
            # Check audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
        
        # Entry2 (1950 - lower entry for LONG, price continues down - DCA)
        feed_price(price_cache, "ETHUSDT", Decimal("1950"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
            assert tracking.actual_entry_price == Decimal("2000"), "Entry price should remain at first entry"
            
            # Verify TP1 recalculation
            # For LONG: EntryHigh = entries[0] = 2000 (first entry, higher price)
            # new_tp1 = 2000 + (2100 - 2000) / 2 = 2000 + 50 = 2050
            expected_new_tp1 = Decimal("2050")
            assert tracking.current_tp1_price == expected_new_tp1, f"TP1 should be recalculated to {expected_new_tp1}"
            
            # Check audit logs
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1
            
            tp1_recalc_logs = [log for log in logs if log.event == AuditEventType.TP1_RECALCULATED]
            assert len(tp1_recalc_logs) == 1, "Should have TP1 recalculation audit log"
        
        # TP1 (2050 - new recalculated TP1, price goes up)
        feed_price(price_cache, "ETHUSDT", Decimal("2050"), current_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            assert tracking.is_active is True
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].position == 1
            assert tp_hits[0].price == Decimal("2050"), "Should hit new TP1, not original"
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            tp_logs = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 1
        
        # TP2 (2200 - original TP2, price continues up)
        feed_price(price_cache, "ETHUSDT", Decimal("2200"), current_time + timedelta(minutes=2))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            assert tracking.is_active is True
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
            assert tp_hits[1].position == 2
            assert tp_hits[1].price == Decimal("2200")
        
        # TP3 (2300 - final target, price continues up)
        feed_price(price_cache, "ETHUSDT", Decimal("2300"), current_time + timedelta(minutes=3))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 3
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            assert tracking.closed_at is not None
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 3
            assert tp_hits[2].position == 3
            assert tp_hits[2].price == Decimal("2300")
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "all_targets_hit"
        
        # Verify: if price drops to stop loss after completion, nothing should happen
        feed_price(price_cache, "ETHUSDT", Decimal("1900"), current_time + timedelta(minutes=4))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            # Status should remain CLOSED, not change to anything else
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 9: Multiple TPs in Single Tick (Price Gap)
# ===========================================================================

@pytest.mark.asyncio
async def test_multiple_tps_single_tick(async_engine, price_cache, tracker, current_time):
    """
    Test multiple TPs hit in single tick (price gap):
    
    If price jumps from below TP1 to above TP3, all should be hit.
    """
    source = await create_test_source(9)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ATOMUSDT",
            direction=Direction.LONG,
            entries=[Decimal("10.00")],
            targets=[Decimal("10.10"), Decimal("10.20"), Decimal("10.30")],
            stop_loss=Decimal("9.50"),
            started_at=current_time,
        )
        
        # Enter
        feed_price(price_cache, "ATOMUSDT", Decimal("10.00"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        # Price gap - jumps to above all TPs
        feed_price(price_cache, "ATOMUSDT", Decimal("10.35"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify all TPs hit
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 3
            assert tracking.status == TrackingStatus.CLOSED
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 3
            assert tp_hits[0].position == 1
            assert tp_hits[1].position == 2
            assert tp_hits[2].position == 3
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 10: Entry Method State Tracking
# ===========================================================================

@pytest.mark.asyncio
async def test_entry_method_state_tracking(async_engine, price_cache, tracker, current_time):
    """
    Test entry method is correctly tracked:
    
    - ENTRY_1: normal first entry
    - EMERGENCY_ENTRY: timeout-based emergency entry
    - Entry method persists throughout lifecycle
    """
    source = await create_test_source(10)
    
    try:
        # Test ENTRY_1 method
        signal1, tracking1_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="LINKUSDT",
            direction=Direction.LONG,
            entries=[Decimal("20.00")],
            targets=[Decimal("21.00"), Decimal("22.00")],
            stop_loss=Decimal("19.00"),
            started_at=current_time,
        )
        
        feed_price(price_cache, "LINKUSDT", Decimal("20.00"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking1_id)
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("20.00")
        
        # Close this tracking
        feed_price(price_cache, "LINKUSDT", Decimal("19.00"), current_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        # Cleanup first signal
        await cleanup_test_data(signal1.id, source.id)
        
        # Create new source for second test
        source2 = await create_test_source(1001)
        
        # Test EMERGENCY_ENTRY method
        signal2, tracking2_id = await create_signal_with_tracking(
            source_id=source2.id,
            symbol="UNIUSDT",
            direction=Direction.LONG,
            entries=[Decimal("15.00")],
            targets=[Decimal("16.00"), Decimal("17.00")],
            stop_loss=Decimal("14.00"),
            started_at=current_time,
        )
        
        # Wait 5 minutes for emergency entry to become available
        # But first, feed prices ABOVE EntryHigh (15.00) to avoid hitting it
        feed_price(price_cache, "UNIUSDT", Decimal("15.50"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        # Calculate expected emergency price: 16 + (16 - 15) / 4 = 16 + 0.25 = 16.25
        expected_emergency_price = Decimal("16.25")
        
        emergency_time = current_time + timedelta(minutes=5, seconds=10)
        
        # Feed price below emergency level - should not enter yet
        feed_price(price_cache, "UNIUSDT", Decimal("16.00"), emergency_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking2_id)
            assert tracking.has_entered is False, "Should not enter yet"
        
        # Hit emergency entry price - should enter
        feed_price(price_cache, "UNIUSDT", expected_emergency_price, emergency_time + timedelta(seconds=5))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking2_id)
            assert tracking.entry_method == EntryMethod.EMERGENCY_ENTRY
            assert tracking.actual_entry_price == expected_emergency_price
            assert tracking.emergency_entry_triggered_at is not None
        
        # Cleanup second signal
        await cleanup_test_data(signal2.id, source2.id)
    
    finally:
        # Ensure cleanup if test fails
        pass


# ===========================================================================
# Test 11: Recovery After Restart Simulation
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_after_restart(async_engine, price_cache, tracker, current_time):
    """
    Test recovery after restart:
    
    1. Enter position and hit TP1
    2. Simulate restart by creating new tracker/cache
    3. Continue from where we left off
    4. Verify no duplicate actions
    """
    source = await create_test_source(11)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="AVAXUSDT",
            direction=Direction.LONG,
            entries=[Decimal("30.00")],
            targets=[Decimal("31.00"), Decimal("32.00"), Decimal("33.00")],
            stop_loss=Decimal("29.00"),
            started_at=current_time,
        )
        
        # Phase 1: Before "restart"
        feed_price(price_cache, "AVAXUSDT", Decimal("30.00"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        feed_price(price_cache, "AVAXUSDT", Decimal("31.00"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits_before = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits_before) == 1
        
        # Phase 2: Simulate restart - new tracker and cache
        new_tracker = Tracker()
        new_cache = PriceCache()
        
        # Feed same TP1 price again (should be idempotent)
        feed_price(new_cache, "AVAXUSDT", Decimal("31.00"), current_time + timedelta(seconds=40))
        await run_tracking_tick(new_cache, new_tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1  # Still 1
            
            tp_hits_after = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits_after) == 1  # Still only 1
        
        # Phase 3: Continue with TP2 (should work normally)
        feed_price(new_cache, "AVAXUSDT", Decimal("32.00"), current_time + timedelta(minutes=1))
        await run_tracking_tick(new_cache, new_tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits_final = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits_final) == 2
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 12: Risk-Free Stop Loss
# ===========================================================================

@pytest.mark.asyncio
async def test_risk_free_stop_loss(async_engine, price_cache, tracker, current_time):
    """
    Test risk-free stop loss:
    
    If price reaches halfway to TP1 then hits SL, should be marked RISK_FREE.
    """
    source = await create_test_source(12)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="MATICUSDT",
            direction=Direction.LONG,
            entries=[Decimal("1.00")],
            targets=[Decimal("1.20"), Decimal("1.40")],
            stop_loss=Decimal("0.90"),
            started_at=current_time,
        )
        
        # Enter
        feed_price(price_cache, "MATICUSDT", Decimal("1.00"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        # Reach halfway to TP1: entry=1.00, tp1=1.20, halfway=1.10
        feed_price(price_cache, "MATICUSDT", Decimal("1.10"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.halfway_to_tp1_reached is True
        
        # Hit stop loss
        feed_price(price_cache, "MATICUSDT", Decimal("0.90"), current_time + timedelta(minutes=1))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify marked as risk-free
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.RISK_FREE
            assert tracking.is_active is False
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "risk_free"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 13: Emergency Entry Price Calculation
# ===========================================================================

@pytest.mark.asyncio
async def test_emergency_entry_price_calculation(async_engine, price_cache, tracker, current_time):
    """
    Test emergency entry price calculation formula by verifying behavior:
    
    LONG: emergency = tp1 + (tp1 - entry1) / 4
    SHORT: emergency = tp1 - (entry1 - tp1) / 4
    
    Tests that entry actually happens at the calculated emergency price.
    """
    source = await create_test_source(13)
    
    try:
        # Test LONG
        # Formula: 1.20 + (1.20 - 1.00) / 4 = 1.20 + 0.05 = 1.25
        expected_long_emergency = Decimal("1.25")
        
        signal_long, tracking_long_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="XRPUSDT",
            direction=Direction.LONG,
            entries=[Decimal("1.00")],
            targets=[Decimal("1.20"), Decimal("1.40")],
            stop_loss=Decimal("0.90"),
            started_at=current_time,
        )
        
        # Feed prices ABOVE EntryHigh (1.00) to avoid hitting it before emergency
        feed_price(price_cache, "XRPUSDT", Decimal("1.10"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        # Wait 5 minutes for emergency to become available
        emergency_time = current_time + timedelta(minutes=5, seconds=10)
        
        # Feed price below emergency level - should NOT enter
        feed_price(price_cache, "XRPUSDT", Decimal("1.24"), emergency_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_long_id)
            assert tracking.has_entered is False, "Should not enter below emergency price"
        
        # Feed emergency price - should enter
        feed_price(price_cache, "XRPUSDT", expected_long_emergency, emergency_time + timedelta(seconds=5))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_long_id)
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.EMERGENCY_ENTRY
            assert tracking.actual_entry_price == expected_long_emergency
        
        # Close this tracking
        feed_price(price_cache, "XRPUSDT", Decimal("0.90"), emergency_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        # Cleanup first signal
        await cleanup_test_data(signal_long.id, source.id)
        
        # Create new source for SHORT test
        source_short = await create_test_source(1302)
        
        # Test SHORT
        # Formula: 0.08 - (0.10 - 0.08) / 4 = 0.08 - 0.005 = 0.075
        expected_short_emergency = Decimal("0.075")
        
        signal_short, tracking_short_id = await create_signal_with_tracking(
            source_id=source_short.id,
            symbol="TRXUSDT",
            direction=Direction.SHORT,
            entries=[Decimal("0.10")],
            targets=[Decimal("0.08"), Decimal("0.06")],
            stop_loss=Decimal("0.11"),
            started_at=current_time,
        )
        
        # Feed prices BELOW EntryLow (0.10) to avoid hitting it before emergency
        # For SHORT, market approaches from below, so EntryLow is first entry
        feed_price(price_cache, "TRXUSDT", Decimal("0.09"), current_time + timedelta(seconds=30))
        await run_tracking_tick(price_cache, tracker)
        
        # Wait 5 minutes for emergency to become available
        emergency_time2 = current_time + timedelta(minutes=5, seconds=10)
        
        # Feed price above emergency level - should NOT enter
        feed_price(price_cache, "TRXUSDT", Decimal("0.076"), emergency_time2)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_short_id)
            assert tracking.has_entered is False, "Should not enter above emergency price"
        
        # Feed emergency price - should enter
        feed_price(price_cache, "TRXUSDT", expected_short_emergency, emergency_time2 + timedelta(seconds=5))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_short_id)
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.EMERGENCY_ENTRY
            assert tracking.actual_entry_price == expected_short_emergency
        
        # Cleanup second signal
        await cleanup_test_data(signal_short.id, source_short.id)
    
    finally:
        # Ensure cleanup if test fails
        pass



# ===========================================================================
# Test 14: Gap Behavior LONG
# ===========================================================================

@pytest.mark.asyncio
async def test_gap_behavior_long(async_engine, price_cache, tracker, current_time):
    """
    Test gap behavior for LONG signal.
    
    Business Rule: If price crosses both EntryHigh and EntryLow in single tick,
    both entries must be detected and both actions returned.
    
    Scenario:
    - EntryHigh = 100
    - EntryLow = 90
    - Price: 110 → 80 (single tick)
    
    Expected:
    - ENTRY_1 action generated
    - ENTRY_2 action generated
    - Both processed in same cycle
    - entry1_touched = True
    - entry2_touched = True
    - TP1 recalculated
    """
    source = await create_test_source(14)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="GAPUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100"), Decimal("90")],
            targets=[Decimal("110"), Decimal("120"), Decimal("130")],
            stop_loss=Decimal("80"),  # Below the gap price
            started_at=current_time,
        )
        
        # Start with price between the two entries (not triggering startup)
        # Price at 105 is above EntryHigh (100) but above EntryLow (90)
        feed_price(price_cache, "GAPUSDT", Decimal("105"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.has_entered is False, "Should not have entered yet"
        
        # Price gaps down crossing both entries in single tick
        feed_price(price_cache, "GAPUSDT", Decimal("88"), current_time + timedelta(seconds=5))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify both entries touched
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True, "Entry1 should be touched"
            assert tracking.entry2_touched is True, "Entry2 should be touched (gap scenario)"
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("100")
            
            # TP1 should be recalculated
            # EntryHigh = 100, original TP1 = 110
            # new_tp1 = 100 + (110 - 100) / 2 = 100 + 5 = 105
            expected_new_tp1 = Decimal("105")
            assert tracking.current_tp1_price == expected_new_tp1
            
            # Check audit logs - should have both Entry1 and Entry2 events
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry1_logs) == 1, "Should have exactly one Entry1 event"
            assert len(entry2_logs) == 1, "Should have exactly one Entry2 event"
            
            tp1_recalc_logs = [log for log in logs if log.event == AuditEventType.TP1_RECALCULATED]
            assert len(tp1_recalc_logs) == 1, "Should have TP1 recalculation event"
        
        # Verify TP1 works with recalculated price
        feed_price(price_cache, "GAPUSDT", Decimal("105"), current_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            # Debug: Check what's happening
            print(f"highest_target_hit: {tracking.highest_target_hit}")
            print(f"current_tp1_price: {tracking.current_tp1_price}")
            print(f"status: {tracking.status}")
            
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].price == Decimal("105")
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 15: Gap Behavior SHORT
# ===========================================================================

@pytest.mark.asyncio
async def test_gap_behavior_short(async_engine, price_cache, tracker, current_time):
    """
    Test gap behavior for SHORT signal.
    
    Business Rule: If price crosses both EntryHigh and EntryLow in single tick,
    both entries must be detected and both actions returned.
    
    For SHORT signals:
    - EntryHigh = 100 (higher for SHORT - this is first entry)
    - EntryLow = 110 (lower for SHORT - this is second/averaging entry)
    
    Scenario:
    - Price: 95 → 115 (single tick, crosses both)
    
    Expected:
    - ENTRY_1 action generated (at EntryHigh = 100)
    - ENTRY_2 action generated (at EntryLow = 110)
    - Both processed in same cycle
    - entry1_touched = True
    - entry2_touched = True
    - TP1 recalculated
    """
    source = await create_test_source(15)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="GAPSHORT",
            direction=Direction.SHORT,
            entries=[Decimal("100"), Decimal("110")],  # EntryHigh=100, EntryLow=110 for SHORT
            targets=[Decimal("90"), Decimal("80"), Decimal("70")],
            stop_loss=Decimal("115"),
            started_at=current_time,
        )
        
        # Start below both entries
        feed_price(price_cache, "GAPSHORT", Decimal("95"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.has_entered is False, "Should not have entered yet"
        
        # Price gaps up crossing both entries in single tick
        feed_price(price_cache, "GAPSHORT", Decimal("112"), current_time + timedelta(seconds=5))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify both entries touched
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True, "Entry1 should be touched"
            assert tracking.entry2_touched is True, "Entry2 should be touched (gap scenario)"
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("100")  # EntryHigh for SHORT
            
            # TP1 should be recalculated
            # For SHORT: EntryHigh = 100, original TP1 = 90
            # new_tp1 = 100 + (90 - 100) / 2 = 100 - 5 = 95
            expected_new_tp1 = Decimal("95")
            assert tracking.current_tp1_price == expected_new_tp1
            
            # Check audit logs
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry1_logs) == 1
            assert len(entry2_logs) == 1
            
            tp1_recalc_logs = [log for log in logs if log.event == AuditEventType.TP1_RECALCULATED]
            assert len(tp1_recalc_logs) == 1
        
        # Verify TP1 works with recalculated price
        feed_price(price_cache, "GAPSHORT", Decimal("95"), current_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].price == Decimal("95")
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 16: Startup Detection LONG
# ===========================================================================

@pytest.mark.asyncio
async def test_startup_detection_long(async_engine, price_cache, tracker, current_time):
    """
    Test startup detection for LONG signal.
    
    Business Rule: If tracking starts while price is already past EntryHigh,
    immediately trigger Entry1.
    
    Scenario:
    - EntryHigh = 100
    - Starting price = 95 (already below EntryHigh)
    
    Expected:
    - Entry1 immediately triggered on first tick
    """
    source = await create_test_source(16)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="STARTUPLONG",
            direction=Direction.LONG,
            entries=[Decimal("100"), Decimal("90")],
            targets=[Decimal("110"), Decimal("120")],
            stop_loss=Decimal("85"),
            started_at=current_time,
        )
        
        # First tick with price already below EntryHigh
        feed_price(price_cache, "STARTUPLONG", Decimal("95"), current_time + timedelta(seconds=1))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify entry triggered immediately
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True, "Entry1 should be triggered at startup"
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("100")
            assert tracking.status == TrackingStatus.TRACKING
            
            # Check audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 17: Startup Detection SHORT
# ===========================================================================

@pytest.mark.asyncio
async def test_startup_detection_short(async_engine, price_cache, tracker, current_time):
    """
    Test startup detection for SHORT signal.
    
    Business Rule: If tracking starts while price is already past EntryHigh,
    immediately trigger Entry1.
    
    Scenario:
    - EntryHigh = 90 (lower for SHORT)
    - Starting price = 95 (already above EntryHigh)
    
    Expected:
    - Entry1 immediately triggered on first tick
    """
    source = await create_test_source(17)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="STARTUPSHORT",
            direction=Direction.SHORT,
            entries=[Decimal("90"), Decimal("100")],
            targets=[Decimal("80"), Decimal("70")],
            stop_loss=Decimal("105"),
            started_at=current_time,
        )
        
        # First tick with price already above EntryHigh (90)
        feed_price(price_cache, "STARTUPSHORT", Decimal("95"), current_time + timedelta(seconds=1))
        await run_tracking_tick(price_cache, tracker)
        
        # Verify entry triggered immediately
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True, "Entry1 should be triggered at startup"
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("90")
            assert tracking.status == TrackingStatus.TRACKING
            
            # Check audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 18: Entry2 Blocked After TP1
# ===========================================================================

@pytest.mark.asyncio
async def test_entry2_blocked_after_tp1(async_engine, price_cache, tracker, current_time):
    """
    Test that Entry2 is permanently blocked after any TP hit.
    
    Business Rule: "Once any TP has been reached, EntryLow becomes permanently disabled."
    
    Scenario:
    - Entry1 → TP1 → Price drops to EntryLow → Entry2 should NOT trigger
    """
    source = await create_test_source(18)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BLOCKENTRY2",
            direction=Direction.LONG,
            entries=[Decimal("100"), Decimal("90")],
            targets=[Decimal("110"), Decimal("120")],
            stop_loss=Decimal("85"),
            started_at=current_time,
        )
        
        # Entry1
        feed_price(price_cache, "BLOCKENTRY2", Decimal("100"), current_time)
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry2_touched is False
        
        # TP1
        feed_price(price_cache, "BLOCKENTRY2", Decimal("110"), current_time + timedelta(seconds=10))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
        
        # Price drops to EntryLow - should be IGNORED
        feed_price(price_cache, "BLOCKENTRY2", Decimal("90"), current_time + timedelta(seconds=20))
        await run_tracking_tick(price_cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is False, "Entry2 should be blocked after TP1 hit"
            
            # Check audit logs - should NOT have Entry2 event
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 0, "Should have no Entry2 events after TP hit"
    
    finally:
        await cleanup_test_data(signal.id, source.id)
