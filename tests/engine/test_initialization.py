"""
Initialization phase behavior tests.

Tests verify that the initialization phase:
- Runs exactly once per tracking per engine session
- Runs again after engine restart
- Delegates to existing entry rules (single source of truth)
- Handles gap scenarios correctly during initialization
- Skips normal rules when initialization emits actions
"""
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

from app.database.enums import Direction, SignalStatus, TrackingStatus, Provider, AuditEventType, EntryMethod
from app.database.models import SignalSource
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
            name=f"Init Test Source {channel_id}",
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
    started_at: datetime = None,
) -> tuple[int, int]:
    """Create signal and tracking, return signal_id and tracking_id."""
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    
    async with UnitOfWork() as uow:
        signal = await uow.signals.create(
            source_id=source_id,
            symbol=symbol,
            direction=direction,
            leverage=10,
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
            provider=Provider.BINANCE,
            is_active=True,
            started_at=started_at,
            current_stop_loss=stop_loss,
            current_tp1_price=initial_tp1,
        )
        
        await uow.commit()
        
    return signal.id, tracking.id


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
# Test 1: Initialization Runs Once Per Session
# ===========================================================================

@pytest.mark.asyncio
async def test_initialization_runs_once_per_session(async_engine, current_time):
    """
    Verify initialization runs exactly once per engine session.
    
    1. Create tracking
    2. First tick: initialization runs, emits ENTRY_1
    3. Second tick: initialization does NOT run again
    4. Third tick: initialization does NOT run again
    """
    source = await create_test_source(2001)
    
    try:
        signal_id, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="INITUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100")],
            targets=[Decimal("110")],
            stop_loss=Decimal("90"),
            started_at=current_time,
        )
        
        # Create manager with fresh cache and tracker
        cache = PriceCache()
        tracker = Tracker()
        uow = UnitOfWork()
        mock_telegram = Mock()
        processor = ActionProcessor(telegram_service=mock_telegram)
        manager = TrackingManager(
            uow_factory=lambda: uow,
            tracker=tracker,
            processor=processor,
            cache=cache,
            interval=2.0,
        )
        
        # Feed price below EntryHigh (should trigger initialization)
        feed_price(cache, "INITUSDT", Decimal("95"), current_time)
        
        # First tick: initialization should run
        assert tracking_id not in manager._initialized_trackings
        await manager._tick()
        assert tracking_id in manager._initialized_trackings
        
        # Verify entry occurred
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
        
        # Second tick: initialization should NOT run again
        feed_price(cache, "INITUSDT", Decimal("95"), current_time + timedelta(seconds=2))
        await manager._tick()
        
        # Third tick: initialization should NOT run again
        feed_price(cache, "INITUSDT", Decimal("95"), current_time + timedelta(seconds=4))
        await manager._tick()
        
        # Verify tracking_id is still in the set (only added once)
        assert tracking_id in manager._initialized_trackings
        
    finally:
        await cleanup_test_data(signal_id, source.id)


# ===========================================================================
# Test 2: Initialization Runs Again After Restart
# ===========================================================================

@pytest.mark.asyncio
async def test_initialization_runs_again_after_restart(async_engine, current_time):
    """
    Verify initialization runs again after engine restart.
    
    1. Create tracking
    2. First session: initialization runs, no entry (price above EntryHigh)
    3. Simulate restart: new manager instance
    4. Second session: initialization runs AGAIN, entry occurs (price below EntryHigh)
    """
    source = await create_test_source(2002)
    
    try:
        signal_id, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="RESTARTUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100")],
            targets=[Decimal("110")],
            stop_loss=Decimal("90"),
            started_at=current_time,
        )
        
        # ==========================================
        # Session 1: Price above entry (no action)
        # ==========================================
        cache1 = PriceCache()
        tracker1 = Tracker()
        uow1 = UnitOfWork()
        mock_telegram1 = Mock()
        processor1 = ActionProcessor(telegram_service=mock_telegram1)
        manager1 = TrackingManager(
            uow_factory=lambda: uow1,
            tracker=tracker1,
            processor=processor1,
            cache=cache1,
            interval=2.0,
        )
        
        # Feed price ABOVE EntryHigh (should not trigger entry)
        feed_price(cache1, "RESTARTUSDT", Decimal("105"), current_time)
        
        # Initialization runs but emits nothing
        await manager1._tick()
        assert tracking_id in manager1._initialized_trackings
        
        # Verify no entry occurred
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is False
            assert tracking.entry_method is None
        
        # ==========================================
        # Simulate restart: new manager
        # ==========================================
        del cache1, tracker1, manager1, uow1, processor1, mock_telegram1
        
        cache2 = PriceCache()
        tracker2 = Tracker()
        uow2 = UnitOfWork()
        mock_telegram2 = Mock()
        processor2 = ActionProcessor(telegram_service=mock_telegram2)
        manager2 = TrackingManager(
            uow_factory=lambda: uow2,
            tracker=tracker2,
            processor=processor2,
            cache=cache2,
            interval=2.0,
        )
        
        # New manager has empty initialization set
        assert tracking_id not in manager2._initialized_trackings
        
        # ==========================================
        # Session 2: Price below entry (entry occurs)
        # ==========================================
        # Feed price BELOW EntryHigh (should trigger entry)
        feed_price(cache2, "RESTARTUSDT", Decimal("95"), current_time + timedelta(minutes=1))
        
        # Initialization runs again and emits ENTRY_1
        await manager2._tick()
        assert tracking_id in manager2._initialized_trackings
        
        # Verify entry occurred
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("100")
        
    finally:
        await cleanup_test_data(signal_id, source.id)


# ===========================================================================
# Test 3: Initialization Gap Detection
# ===========================================================================

@pytest.mark.asyncio
async def test_initialization_gap_detection(async_engine, current_time):
    """
    Verify initialization handles gap scenarios correctly.
    
    If engine starts with price already beyond both entries,
    initialization should emit both ENTRY_1 and ENTRY_2.
    """
    source = await create_test_source(2003)
    
    try:
        signal_id, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="GAPINIT",
            direction=Direction.LONG,
            entries=[Decimal("100"), Decimal("90")],
            targets=[Decimal("110"), Decimal("120")],
            stop_loss=Decimal("80"),
            started_at=current_time,
        )
        
        cache = PriceCache()
        tracker = Tracker()
        uow = UnitOfWork()
        mock_telegram = Mock()
        processor = ActionProcessor(telegram_service=mock_telegram)
        manager = TrackingManager(
            uow_factory=lambda: uow,
            tracker=tracker,
            processor=processor,
            cache=cache,
            interval=2.0,
        )
        
        # Feed price below BOTH entries (gap scenario)
        feed_price(cache, "GAPINIT", Decimal("85"), current_time)
        
        # Initialization should detect gap and emit both actions
        await manager._tick()
        
        # Verify both entries occurred
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry2_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            assert tracking.actual_entry_price == Decimal("100")
            
            # TP1 should be recalculated
            # EntryHigh = 100, original TP1 = 110
            # new_tp1 = 100 + (110 - 100) / 2 = 105
            assert tracking.current_tp1_price == Decimal("105")
            
            # Verify audit logs
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            entry2_logs = [log for log in logs if log.event == AuditEventType.ENTRY2_HIT]
            assert len(entry1_logs) == 1
            assert len(entry2_logs) == 1
        
    finally:
        await cleanup_test_data(signal_id, source.id)


# ===========================================================================
# Test 4: Initialization Skips Normal Rules When Actions Emitted
# ===========================================================================

@pytest.mark.asyncio
async def test_initialization_skips_normal_rules(async_engine, current_time):
    """
    Verify that when initialization emits actions, normal rules don't run
    during the same cycle.
    
    This is important because it ensures one state transition per cycle.
    """
    source = await create_test_source(2004)
    
    try:
        signal_id, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="SKIPUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100")],
            targets=[Decimal("110")],
            stop_loss=Decimal("90"),
            started_at=current_time,
        )
        
        cache = PriceCache()
        tracker = Tracker()
        uow = UnitOfWork()
        mock_telegram = Mock()
        processor = ActionProcessor(telegram_service=mock_telegram)
        manager = TrackingManager(
            uow_factory=lambda: uow,
            tracker=tracker,
            processor=processor,
            cache=cache,
            interval=2.0,
        )
        
        # Feed price that triggers initialization entry
        feed_price(cache, "SKIPUSDT", Decimal("95"), current_time)
        
        # First tick: initialization emits ENTRY_1, normal rules skipped
        await manager._tick()
        
        # Verify entry occurred
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.status == TrackingStatus.TRACKING
            
            # Only ONE entry event should exist
            # (If normal rules ran, we might have duplicate events or unexpected behavior)
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 1
        
        # Second tick: initialization already done, normal rules run
        feed_price(cache, "SKIPUSDT", Decimal("110"), current_time + timedelta(seconds=2))
        await manager._tick()
        
        # Verify TP1 was hit by normal rules
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.highest_target_hit == 1
        
    finally:
        await cleanup_test_data(signal_id, source.id)


# ===========================================================================
# Test 5: Initialization Respects Database State
# ===========================================================================

@pytest.mark.asyncio
async def test_initialization_respects_database_state(async_engine, current_time):
    """
    Verify initialization respects existing database state.
    
    If tracking has already entered (entry1_touched = True),
    initialization should not emit any actions.
    """
    source = await create_test_source(2005)
    
    try:
        # Create tracking and manually set it to already entered
        signal_id, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="STATEUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100")],
            targets=[Decimal("110")],
            stop_loss=Decimal("90"),
            started_at=current_time,
        )
        
        # Manually mark as entered
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get(tracking_id)
            tracking.entry1_touched = True
            tracking.entry_method = EntryMethod.ENTRY_1
            tracking.actual_entry_price = Decimal("100")
            tracking.status = TrackingStatus.TRACKING
            await uow.commit()
        
        # Create manager
        cache = PriceCache()
        tracker = Tracker()
        uow = UnitOfWork()
        mock_telegram = Mock()
        processor = ActionProcessor(telegram_service=mock_telegram)
        manager = TrackingManager(
            uow_factory=lambda: uow,
            tracker=tracker,
            processor=processor,
            cache=cache,
            interval=2.0,
        )
        
        # Feed price below entry (normally would trigger initialization)
        feed_price(cache, "STATEUSDT", Decimal("95"), current_time)
        
        # Initialization should run but emit nothing (already entered)
        await manager._tick()
        
        # Verify no duplicate entry
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            assert tracking.entry1_touched is True
            assert tracking.entry_method == EntryMethod.ENTRY_1
            
            # Should have no entry audit logs (we manually set state)
            logs = await uow.audit_logs.by_tracking(tracking_id)
            entry1_logs = [log for log in logs if log.event == AuditEventType.ENTRY1_HIT]
            assert len(entry1_logs) == 0
        
    finally:
        await cleanup_test_data(signal_id, source.id)
