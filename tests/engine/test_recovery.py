"""
Recovery tests for tracking engine.

Tests validate that the engine is deterministically recoverable after restart.
Simulates real restarts by:
1. Creating signal and processing ticks
2. Committing to database
3. Destroying engine components
4. Creating new TrackingManager/UnitOfWork
5. Loading tracking from database
6. Continuing with remaining ticks

Final state after restart must be identical to running continuously.

Uses real engine components - no mocking of business logic.
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
# Test Helpers
# ===========================================================================

async def create_test_source(unique_id: int) -> SignalSource:
    """Create a test signal source with unique identifier."""
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    channel_id = timestamp_ms + unique_id
    
    async with UnitOfWork() as uow:
        source = await uow.signal_sources.create(
            name=f"Recovery Test Source {channel_id}",
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
        
        for i, price in enumerate(entries, start=1):
            await uow.signal_entries.create(
                signal_id=signal.id,
                position=i,
                price=price,
            )
        
        for i, price in enumerate(targets, start=1):
            await uow.signal_targets.create(
                signal_id=signal.id,
                position=i,
                price=price,
            )
        
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
    processor = ActionProcessor()
    manager = TrackingManager(
        uow_factory=lambda: uow,
        tracker=tracker,
        processor=processor,
        cache=cache,
        interval=2.0,
    )
    
    await manager._tick()


async def cleanup_test_data(signal_id: int, source_id: int):
    """Clean up test data."""
    async with UnitOfWork() as uow:
        signal = await uow.signals.get(signal_id)
        if signal:
            await uow.session.delete(signal)
            await uow.flush()
        
        source = await uow.signal_sources.get(source_id)
        if source:
            await uow.session.delete(source)
        
        await uow.commit()


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
async def async_engine():
    """Provide database engine with proper cleanup."""
    yield db_engine
    await db_engine.dispose()


@pytest.fixture
def current_time():
    """Current timestamp for tests."""
    return datetime.now(timezone.utc)


# ===========================================================================
# Test 1: Recovery After Entry1
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_after_entry1(async_engine, current_time):
    """
    Scenario: Entry1 → Restart → TP1 → TP2 → SL
    
    Verify:
    - entry1 remains touched
    - entry_method is ENTRY_1
    - actual_entry_price is correct
    - TP progression continues correctly
    - stop loss works correctly
    - audit events are correct
    """
    source = await create_test_source(1001)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entries=[Decimal("50000"), Decimal("49000")],
            targets=[Decimal("52000"), Decimal("54000"), Decimal("56000")],
            stop_loss=Decimal("48000"),
            started_at=current_time,
        )
        
        # Phase 1: Before restart - Hit Entry1
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        feed_price(cache1, "BTCUSDT", Decimal("50000"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        # Verify Entry1 state
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("50000")
            assert tracking.status == TrackingStatus.TRACKING
            
            logs_before = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs_before if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
        
        # SIMULATE RESTART: Destroy old components, create new ones
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Phase 2: After restart - Continue with TP1
        feed_price(cache2, "BTCUSDT", Decimal("52000"), current_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            assert tracking.entry_method == EntryMethod.ENTRY_1  # Persisted
            assert tracking.actual_entry_price == Decimal("50000")  # Persisted
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].position == 1
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1  # No duplicate
        
        # TP2
        feed_price(cache2, "BTCUSDT", Decimal("54000"), current_time + timedelta(minutes=2))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
        
        # Stop Loss
        feed_price(cache2, "BTCUSDT", Decimal("48000"), current_time + timedelta(minutes=3))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "stop_loss"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 2: Recovery After Emergency Entry
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_after_emergency_entry(async_engine, current_time):
    """
    Scenario: 5 minutes → Emergency Entry → Restart → Entry2 → TP1 → TP2 → SL
    
    Verify:
    - entry_method == EMERGENCY_ENTRY
    - actual_entry_price preserved
    - engine never attempts Entry1 again
    - engine never attempts another Emergency Entry
    - Entry2 CAN fire after emergency (per updated business rules)
    - TP1 recalculated when Entry2 fires
    - TP progression continues normally
    """
    source = await create_test_source(1002)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entries=[Decimal("3000"), Decimal("2950")],
            targets=[Decimal("3100"), Decimal("3200"), Decimal("3300")],
            stop_loss=Decimal("2900"),
            started_at=current_time,
        )
        
        # Phase 1: Before restart - Wait for emergency entry
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Feed prices above EntryHigh to avoid hitting Entry1
        feed_price(cache1, "ETHUSDT", Decimal("3050"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        # Wait 5+ minutes, calculate emergency price
        # emergency = 3100 + (3100 - 3000) / 4 = 3100 + 25 = 3125
        emergency_time = current_time + timedelta(minutes=5, seconds=10)
        expected_emergency_price = Decimal("3125")
        
        feed_price(cache1, "ETHUSDT", expected_emergency_price, emergency_time)
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.has_entered is True
            assert tracking.entry_method == EntryMethod.EMERGENCY_ENTRY
            assert tracking.actual_entry_price == expected_emergency_price
            assert tracking.emergency_entry_triggered_at is not None
            
            logs_before = await uow.audit_logs.by_tracking(tracking_id)
            emergency_logs = [log for log in logs_before if log.event == AuditEventType.EMERGENCY_ENTRY_HIT]
            assert len(emergency_logs) == 1
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Phase 2: After restart - Feed Entry1 price (should be ignored for actual entry)
        feed_price(cache2, "ETHUSDT", Decimal("3000"), emergency_time + timedelta(seconds=30))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            # Note: entry1_touched is True because emergency entry sets it
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.EMERGENCY_ENTRY  # Unchanged
            assert tracking.actual_entry_price == expected_emergency_price  # Unchanged, not updated to 3000
            
            # No new emergency entry events
            logs = await uow.audit_logs.by_tracking(tracking_id)
            emergency_logs = [log for log in logs if log.event == AuditEventType.EMERGENCY_ENTRY_HIT]
            assert len(emergency_logs) == 1  # Still only 1
        
        # Feed Entry2 price (SHOULD trigger after emergency per new business rules)
        feed_price(cache2, "ETHUSDT", Decimal("2950"), emergency_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            # Business Rule: EntryLow CAN fire after Emergency Entry (as long as no TP hit yet)
            assert tracking.entry2_touched is True
            
            # Check Entry2 audit log
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1
            
            # TP1 should be recalculated
            # EntryHigh = 3000, original TP1 = 3100
            # new_tp1 = 3000 + (3100 - 3000) / 2 = 3000 + 50 = 3050
            expected_new_tp1 = Decimal("3050")
            assert tracking.current_tp1_price == expected_new_tp1
        
        # TP1 (recalculated value)
        feed_price(cache2, "ETHUSDT", expected_new_tp1, emergency_time + timedelta(minutes=2))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
        
        # TP2
        feed_price(cache2, "ETHUSDT", Decimal("3200"), emergency_time + timedelta(minutes=3))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
        
        # Stop Loss
        feed_price(cache2, "ETHUSDT", Decimal("2900"), emergency_time + timedelta(minutes=4))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CLOSED
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 3: Recovery After Entry2
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_after_entry2(async_engine, current_time):
    """
    Scenario: Entry1 → Entry2 → Restart → TP1 → TP2
    
    Verify:
    - entry2_touched remains true
    - current_tp1_price remains the recalculated value
    - TP1 uses the recalculated price
    - original TP1 is never used again
    """
    source = await create_test_source(1003)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BNBUSDT",
            direction=Direction.LONG,
            entries=[Decimal("400"), Decimal("395")],
            targets=[Decimal("410"), Decimal("420"), Decimal("430")],
            stop_loss=Decimal("390"),
            started_at=current_time,
        )
        
        # Phase 1: Before restart - Hit Entry1 and Entry2
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Entry1
        feed_price(cache1, "BNBUSDT", Decimal("400"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.current_tp1_price == Decimal("410")  # Original
        
        # Entry2
        feed_price(cache1, "BNBUSDT", Decimal("395"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
            # New TP1: 400 + (410 - 400) / 2 = 400 + 5 = 405
            expected_new_tp1 = Decimal("405")
            assert tracking.current_tp1_price == expected_new_tp1
            
            logs_before = await uow.audit_logs.by_tracking(tracking_id)
            entry2_logs = [log for log in logs_before if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1
            tp1_recalc_logs = [log for log in logs_before if log.event == AuditEventType.TP1_RECALCULATED]
            assert len(tp1_recalc_logs) == 1
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Phase 2: After restart - Hit NEW TP1 (405, not original 410)
        feed_price(cache2, "BNBUSDT", Decimal("405"), current_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            assert tracking.entry2_touched is True  # Persisted
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
            assert tp_hits[0].price == Decimal("405")  # New TP1
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1  # No duplicate
        
        # Original TP1 (410) should NOT trigger another event
        feed_price(cache2, "BNBUSDT", Decimal("410"), current_time + timedelta(minutes=2))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1  # Still 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1  # Still only 1
        
        # TP2 (original target 2)
        feed_price(cache2, "BNBUSDT", Decimal("420"), current_time + timedelta(minutes=3))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 4: Recovery with Corrupted Peak Cache
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_with_corrupted_peak_cache(async_engine, current_time):
    """
    Scenario: Entry1 → price halfway to TP1 → Restart with corrupted cache → SL
    
    Verify:
    - recovery still reaches correct final state
    - engine does not rely on peak_price_after_entry for correctness
    - no duplicate audit events
    - no duplicate TP events
    - no duplicate risk-free events
    """
    source = await create_test_source(1004)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="SOLUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100")],
            targets=[Decimal("110"), Decimal("120"), Decimal("130")],
            stop_loss=Decimal("95"),
            started_at=current_time,
        )
        
        # Phase 1: Before restart - Enter and reach halfway to TP1
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Entry
        feed_price(cache1, "SOLUSDT", Decimal("100"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        # Halfway to TP1: entry=100, tp1=110, halfway=105
        feed_price(cache1, "SOLUSDT", Decimal("105"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.halfway_to_tp1_reached is True
            assert tracking.peak_price_after_entry == Decimal("105")
            
            # Note: halfway_to_tp1_reached has no specific audit event, it's just a tracked field
        
        # SIMULATE RESTART + CORRUPT CACHE
        # Force peak_price_after_entry to NULL
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get(tracking_id)
            tracking.peak_price_after_entry = None
            await uow.commit()
        
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Phase 2: After restart with corrupted cache - Continue and hit SL
        # Engine should recalculate peak from current price
        feed_price(cache2, "SOLUSDT", Decimal("103"), current_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        # Hit stop loss - should be marked RISK_FREE due to persisted flag
        feed_price(cache2, "SOLUSDT", Decimal("95"), current_time + timedelta(minutes=2))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.RISK_FREE
            assert tracking.is_active is False
            
            # Verify no duplicate events
            logs = await uow.audit_logs.by_tracking(tracking_id)
            
            # halfway_to_tp1_reached has no audit event, just verify closed event
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "risk_free"
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 0  # No TPs hit
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 5: Restart After TP1
# ===========================================================================

@pytest.mark.asyncio
async def test_restart_after_tp1(async_engine, current_time):
    """
    Scenario: Entry1 → TP1 → Restart → TP2 → TP3 → Completed
    
    Verify:
    - TP1 is not emitted again
    - highest_target_hit resumes correctly
    - completion occurs exactly once
    """
    source = await create_test_source(1005)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entries=[Decimal("1.00")],
            targets=[Decimal("1.10"), Decimal("1.20"), Decimal("1.30")],
            stop_loss=Decimal("0.95"),
            started_at=current_time,
        )
        
        # Phase 1: Before restart - Entry and TP1
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Entry
        feed_price(cache1, "ADAUSDT", Decimal("1.00"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        # TP1
        feed_price(cache1, "ADAUSDT", Decimal("1.10"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits_before = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits_before) == 1
            
            logs_before = await uow.audit_logs.by_tracking(tracking_id)
            tp_logs = [log for log in logs_before if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 1
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Phase 2: After restart - Feed TP1 price again (idempotency test)
        feed_price(cache2, "ADAUSDT", Decimal("1.10"), current_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1  # Still 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1  # Still only 1
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            tp_logs = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 1  # Still only 1
        
        # TP2
        feed_price(cache2, "ADAUSDT", Decimal("1.20"), current_time + timedelta(minutes=2))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
        
        # TP3 (final)
        feed_price(cache2, "ADAUSDT", Decimal("1.30"), current_time + timedelta(minutes=3))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 3
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 3
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1  # Completion event only once
            assert closed_logs[0].payload["reason"] == "all_targets_hit"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 6: Multiple Restarts
# ===========================================================================

@pytest.mark.asyncio
async def test_multiple_restarts(async_engine, current_time):
    """
    Scenario: Signal → Entry1 → Restart → Restart → Restart → Entry2 → 
              Restart → TP1 → Restart → TP2 → Restart → SL
    
    Verify:
    - Every event is emitted exactly once
    - Final database state is identical to running without restarts
    """
    source = await create_test_source(1006)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="DOTUSDT",
            direction=Direction.LONG,
            entries=[Decimal("20"), Decimal("19")],
            targets=[Decimal("21"), Decimal("22"), Decimal("23")],
            stop_loss=Decimal("18"),
            started_at=current_time,
        )
        
        # Helper to create new cache/tracker
        def new_engine():
            return PriceCache(), Tracker()
        
        # Signal created
        cache, tracker = new_engine()
        
        # Entry1
        feed_price(cache, "DOTUSDT", Decimal("20"), current_time)
        await run_tracking_tick(cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
        
        # Restart 1
        del cache, tracker
        cache, tracker = new_engine()
        
        feed_price(cache, "DOTUSDT", Decimal("20"), current_time + timedelta(seconds=5))
        await run_tracking_tick(cache, tracker)
        
        # Restart 2
        del cache, tracker
        cache, tracker = new_engine()
        
        feed_price(cache, "DOTUSDT", Decimal("20"), current_time + timedelta(seconds=10))
        await run_tracking_tick(cache, tracker)
        
        # Restart 3
        del cache, tracker
        cache, tracker = new_engine()
        
        # Entry2
        feed_price(cache, "DOTUSDT", Decimal("19"), current_time + timedelta(seconds=15))
        await run_tracking_tick(cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
            # New TP1: 20 + (21 - 20) / 2 = 20.5
            assert tracking.current_tp1_price == Decimal("20.5")
        
        # Restart 4
        del cache, tracker
        cache, tracker = new_engine()
        
        # TP1 (new value 20.5)
        feed_price(cache, "DOTUSDT", Decimal("20.5"), current_time + timedelta(seconds=20))
        await run_tracking_tick(cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
        
        # Restart 5
        del cache, tracker
        cache, tracker = new_engine()
        
        # TP2
        feed_price(cache, "DOTUSDT", Decimal("22"), current_time + timedelta(seconds=25))
        await run_tracking_tick(cache, tracker)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
        
        # Restart 6
        del cache, tracker
        cache, tracker = new_engine()
        
        # Stop Loss
        feed_price(cache, "DOTUSDT", Decimal("18"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache, tracker)
        
        # Final verification - NO DUPLICATES
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            
            # Verify event counts
            logs = await uow.audit_logs.by_tracking(tracking_id)
            
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
            
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1
            
            tp1_recalc_logs = [log for log in logs if log.event == AuditEventType.TP1_RECALCULATED]
            assert len(tp1_recalc_logs) == 1
            
            tp_logs = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 2  # TP1 and TP2
            
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            
            # Verify TP hit records
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
            assert tp_hits[0].position == 1
            assert tp_hits[1].position == 2
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 7: Recovery Preserves All State
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_preserves_all_state(async_engine, current_time):
    """
    Comprehensive state preservation test.
    
    Verify all persisted fields survive restart:
    - entry1_touched, entry2_touched
    - has_entered, actual_entry_price
    - entry_method, emergency_entry_triggered_at
    - highest_target_hit
    - halfway_to_tp1_reached
    - current_tp1_price, current_stop_loss
    - status, is_active
    """
    source = await create_test_source(1007)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="LINKUSDT",
            direction=Direction.LONG,
            entries=[Decimal("15"), Decimal("14.5")],
            targets=[Decimal("16"), Decimal("17"), Decimal("18")],
            stop_loss=Decimal("14"),
            started_at=current_time,
        )
        
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Build up complex state
        # Entry1
        feed_price(cache1, "LINKUSDT", Decimal("15"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        # Entry2
        feed_price(cache1, "LINKUSDT", Decimal("14.5"), current_time + timedelta(seconds=10))
        await run_tracking_tick(cache1, tracker1)
        
        # Halfway to TP1
        # entry=15, new_tp1=15.5, halfway=15.25
        feed_price(cache1, "LINKUSDT", Decimal("15.25"), current_time + timedelta(seconds=20))
        await run_tracking_tick(cache1, tracker1)
        
        # TP1
        feed_price(cache1, "LINKUSDT", Decimal("15.5"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        # Capture state before restart
        async with UnitOfWork() as uow:
            tracking_before = await uow.trackings.get_full(tracking_id)
            
            state_before = {
                "entry1_touched": tracking_before.entry1_touched,
                "entry2_touched": tracking_before.entry2_touched,
                "has_entered": tracking_before.has_entered,
                "actual_entry_price": tracking_before.actual_entry_price,
                "entry_method": tracking_before.entry_method,
                "highest_target_hit": tracking_before.highest_target_hit,
                "halfway_to_tp1_reached": tracking_before.halfway_to_tp1_reached,
                "current_tp1_price": tracking_before.current_tp1_price,
                "current_stop_loss": tracking_before.current_stop_loss,
                "status": tracking_before.status,
                "is_active": tracking_before.is_active,
            }
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Feed a price and process
        feed_price(cache2, "LINKUSDT", Decimal("15.5"), current_time + timedelta(seconds=40))
        await run_tracking_tick(cache2, tracker2)
        
        # Verify all state preserved
        async with UnitOfWork() as uow:
            tracking_after = await uow.trackings.get_full(tracking_id)
            
            assert tracking_after.entry1_touched == state_before["entry1_touched"]
            assert tracking_after.entry2_touched == state_before["entry2_touched"]
            assert tracking_after.has_entered == state_before["has_entered"]
            assert tracking_after.actual_entry_price == state_before["actual_entry_price"]
            assert tracking_after.entry_method == state_before["entry_method"]
            assert tracking_after.highest_target_hit == state_before["highest_target_hit"]
            assert tracking_after.halfway_to_tp1_reached == state_before["halfway_to_tp1_reached"]
            assert tracking_after.current_tp1_price == state_before["current_tp1_price"]
            assert tracking_after.current_stop_loss == state_before["current_stop_loss"]
            assert tracking_after.status == state_before["status"]
            assert tracking_after.is_active == state_before["is_active"]
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 8: Recovery After Timeout Expiry
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_after_timeout_expiry(async_engine, current_time):
    """
    Scenario: Signal created → near timeout → Restart → past timeout → Expired
    
    Verify:
    - Tracking correctly expires after restart
    - No duplicate expiry events
    """
    source = await create_test_source(1008)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="UNIUSDT",
            direction=Direction.LONG,
            entries=[Decimal("10")],
            targets=[Decimal("11"), Decimal("12")],
            stop_loss=Decimal("9"),
            started_at=current_time,
        )
        
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Feed price just before timeout (1h 59m)
        feed_price(cache1, "UNIUSDT", Decimal("10.5"), current_time + timedelta(hours=1, minutes=59))
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.is_active is True
            assert tracking.status == TrackingStatus.WAITING_ENTRY
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Feed price after timeout (2h 1m)
        feed_price(cache2, "UNIUSDT", Decimal("10.5"), current_time + timedelta(hours=2, minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CANCELLED
            assert tracking.is_active is False
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            expired_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_EXPIRED]
            assert len(expired_logs) == 1  # Only one expiry event
            assert expired_logs[0].payload["reason"] == "timeout"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 9: Recovery After TP1 Crossed Before Entry
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_after_tp1_crossed_before_entry(async_engine, current_time):
    """
    Scenario: Signal created → Restart → TP1 crossed without entry → Cancelled
    
    Verify:
    - Cancellation happens correctly after restart
    - No duplicate cancellation events
    """
    source = await create_test_source(1009)
    
    try:
        signal, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="AVAXUSDT",
            direction=Direction.LONG,
            entries=[Decimal("30")],
            targets=[Decimal("32"), Decimal("34")],
            stop_loss=Decimal("28"),
            started_at=current_time,
        )
        
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Feed price near entry but not crossing
        feed_price(cache1, "AVAXUSDT", Decimal("30.5"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.is_active is True
            assert tracking.has_entered is False
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Feed price that crosses TP1
        feed_price(cache2, "AVAXUSDT", Decimal("32"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache2, tracker2)
        
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
# Test 10: Recovery with Risk-Free State
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_with_risk_free_state(async_engine, current_time):
    """
    Scenario: Entry → halfway to TP1 → Restart → SL → Risk-Free
    
    Verify:
    - halfway_to_tp1_reached flag persists
    - Stop loss correctly triggers RISK_FREE status
    - No duplicate risk-free events
    """
    source = await create_test_source(1010)
    
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
        
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Entry
        feed_price(cache1, "MATICUSDT", Decimal("1.00"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        # Halfway to TP1: entry=1.00, tp1=1.20, halfway=1.10
        feed_price(cache1, "MATICUSDT", Decimal("1.10"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.halfway_to_tp1_reached is True
            
            # Note: halfway_to_tp1_reached has no specific audit event
        
        # SIMULATE RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Hit stop loss
        feed_price(cache2, "MATICUSDT", Decimal("0.90"), current_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.RISK_FREE
            assert tracking.is_active is False
            assert tracking.halfway_to_tp1_reached is True  # Persisted
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            
            # halfway_to_tp1_reached has no audit event, just verify closed event
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
            assert closed_logs[0].payload["reason"] == "risk_free"
    
    finally:
        await cleanup_test_data(signal.id, source.id)


# ===========================================================================
# Test 11: Recovery Determinism - Compare With and Without Restart
# ===========================================================================

@pytest.mark.asyncio
async def test_recovery_determinism(async_engine, current_time):
    """
    Ultimate determinism test.
    
    Run same scenario twice:
    1. Continuously without restart
    2. With restart in the middle
    
    Verify final database state is IDENTICAL.
    """
    source = await create_test_source(1011)
    
    try:
        # Scenario 1: Continuous run
        signal1, tracking1_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ATOMUSDT",
            direction=Direction.LONG,
            entries=[Decimal("10"), Decimal("9.5")],
            targets=[Decimal("11"), Decimal("12"), Decimal("13")],
            stop_loss=Decimal("9"),
            started_at=current_time,
        )
        
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Continuous execution
        feed_price(cache1, "ATOMUSDT", Decimal("10"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        feed_price(cache1, "ATOMUSDT", Decimal("9.5"), current_time + timedelta(seconds=10))
        await run_tracking_tick(cache1, tracker1)
        
        feed_price(cache1, "ATOMUSDT", Decimal("10.25"), current_time + timedelta(seconds=20))
        await run_tracking_tick(cache1, tracker1)
        
        feed_price(cache1, "ATOMUSDT", Decimal("11"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache1, tracker1)
        
        feed_price(cache1, "ATOMUSDT", Decimal("12"), current_time + timedelta(seconds=40))
        await run_tracking_tick(cache1, tracker1)
        
        feed_price(cache1, "ATOMUSDT", Decimal("9"), current_time + timedelta(seconds=50))
        await run_tracking_tick(cache1, tracker1)
        
        # Capture final state
        async with UnitOfWork() as uow:
            tracking1 = await uow.trackings.get_full(tracking1_id)
            
            state1 = {
                "entry1_touched": tracking1.entry1_touched,
                "entry2_touched": tracking1.entry2_touched,
                "highest_target_hit": tracking1.highest_target_hit,
                "halfway_to_tp1_reached": tracking1.halfway_to_tp1_reached,
                "status": tracking1.status,
                "is_active": tracking1.is_active,
            }
            
            tp_hits1 = await uow.tp_hits.by_tracking(tracking1_id)
            logs1 = await uow.audit_logs.by_tracking(tracking1_id)
        
        # Cleanup first signal
        await cleanup_test_data(signal1.id, source.id)
        
        # Create new source for second run
        source2 = await create_test_source(10110)
        
        # Scenario 2: With restart
        signal2, tracking2_id = await create_signal_with_tracking(
            source_id=source2.id,
            symbol="ATOMUSDT",
            direction=Direction.LONG,
            entries=[Decimal("10"), Decimal("9.5")],
            targets=[Decimal("11"), Decimal("12"), Decimal("13")],
            stop_loss=Decimal("9"),
            started_at=current_time,
        )
        
        cache2a = PriceCache()
        tracker2a = Tracker()
        
        # Run until after Entry2
        feed_price(cache2a, "ATOMUSDT", Decimal("10"), current_time)
        await run_tracking_tick(cache2a, tracker2a)
        
        feed_price(cache2a, "ATOMUSDT", Decimal("9.5"), current_time + timedelta(seconds=10))
        await run_tracking_tick(cache2a, tracker2a)
        
        # RESTART HERE
        del cache2a, tracker2a
        cache2b = PriceCache()
        tracker2b = Tracker()
        
        # Continue with remaining ticks
        feed_price(cache2b, "ATOMUSDT", Decimal("10.25"), current_time + timedelta(seconds=20))
        await run_tracking_tick(cache2b, tracker2b)
        
        feed_price(cache2b, "ATOMUSDT", Decimal("11"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache2b, tracker2b)
        
        feed_price(cache2b, "ATOMUSDT", Decimal("12"), current_time + timedelta(seconds=40))
        await run_tracking_tick(cache2b, tracker2b)
        
        feed_price(cache2b, "ATOMUSDT", Decimal("9"), current_time + timedelta(seconds=50))
        await run_tracking_tick(cache2b, tracker2b)
        
        # Capture final state
        async with UnitOfWork() as uow:
            tracking2 = await uow.trackings.get_full(tracking2_id)
            
            state2 = {
                "entry1_touched": tracking2.entry1_touched,
                "entry2_touched": tracking2.entry2_touched,
                "highest_target_hit": tracking2.highest_target_hit,
                "halfway_to_tp1_reached": tracking2.halfway_to_tp1_reached,
                "status": tracking2.status,
                "is_active": tracking2.is_active,
            }
            
            tp_hits2 = await uow.tp_hits.by_tracking(tracking2_id)
            logs2 = await uow.audit_logs.by_tracking(tracking2_id)
        
        # VERIFY IDENTICAL FINAL STATE
        assert state1 == state2, "Final tracking state must be identical"
        assert len(tp_hits1) == len(tp_hits2), "TP hits count must match"
        assert len(logs1) == len(logs2), "Audit logs count must match"
        
        # Verify TP hit positions match
        for i, (tp1, tp2) in enumerate(zip(tp_hits1, tp_hits2)):
            assert tp1.position == tp2.position, f"TP position {i} must match"
            assert tp1.price == tp2.price, f"TP price {i} must match"
        
        # Verify audit log events match
        events1 = [log.event for log in logs1]
        events2 = [log.event for log in logs2]
        assert events1 == events2, "Audit log events must match exactly"
        
        # Cleanup second signal
        await cleanup_test_data(signal2.id, source2.id)
    
    finally:
        # Ensure cleanup
        pass


# ===========================================================================
# Test 12: SHORT Recovery Scenario
# ===========================================================================

@pytest.mark.asyncio
async def test_short_recovery_scenario(async_engine, current_time):
    """
    Test SHORT signal recovery with inverted logic.
    
    Scenario: SHORT Entry1 → Restart → Entry2 → TP1 → Restart → TP2 → SL
    
    Verify SHORT direction state persists correctly through restarts.
    """
    source = await create_test_source(1012)
    
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
        
        cache1 = PriceCache()
        tracker1 = Tracker()
        
        # Entry1 (50000 - lower for SHORT)
        feed_price(cache1, "BTCUSDT", Decimal("50000"), current_time)
        await run_tracking_tick(cache1, tracker1)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.actual_entry_price == Decimal("50000")
        
        # RESTART
        del cache1, tracker1
        cache2 = PriceCache()
        tracker2 = Tracker()
        
        # Entry2 (51000 - higher for SHORT, DCA)
        feed_price(cache2, "BTCUSDT", Decimal("51000"), current_time + timedelta(seconds=30))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry2_touched is True
            assert tracking.entry1_touched is True  # Persisted
        
        # TP1 (49000 - price goes down for SHORT)
        feed_price(cache2, "BTCUSDT", Decimal("49000"), current_time + timedelta(minutes=1))
        await run_tracking_tick(cache2, tracker2)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 1
        
        # RESTART AGAIN
        del cache2, tracker2
        cache3 = PriceCache()
        tracker3 = Tracker()
        
        # TP2 (48000)
        feed_price(cache3, "BTCUSDT", Decimal("48000"), current_time + timedelta(minutes=2))
        await run_tracking_tick(cache3, tracker3)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 2
            
            tp_hits = await uow.tp_hits.by_tracking(tracking_id)
            assert len(tp_hits) == 2
        
        # Stop Loss (52000 - price goes up for SHORT)
        feed_price(cache3, "BTCUSDT", Decimal("52000"), current_time + timedelta(minutes=3))
        await run_tracking_tick(cache3, tracker3)
        
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.status == TrackingStatus.CLOSED
            assert tracking.is_active is False
            
            logs = await uow.audit_logs.by_tracking(tracking_id)
            
            # Verify no duplicate events
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
            
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry2_logs) == 1
            
            tp_logs = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
            assert len(tp_logs) == 2
            
            closed_logs = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
            assert len(closed_logs) == 1
    
    finally:
        await cleanup_test_data(signal.id, source.id)
