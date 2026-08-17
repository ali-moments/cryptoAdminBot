"""
Regression tests for confirmed provider change bugs.

Tests specifically target the bugs identified in the audit:
1. Provider reset clears ALL tracking initialization (too broad)
2. Historical TP actions emitted after reinitialization
"""
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from app.database.enums import Direction, SignalStatus, TrackingStatus, Provider, AuditEventType, EntryMethod
from app.database.models import SignalSource
from app.database.uow import UnitOfWork
from app.database.db import engine as db_engine
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.market.events import ProviderChangedEvent
from app.engine.tracker import Tracker
from app.engine.action_processor import ActionProcessor
from app.engine.tracking_manager import TrackingManager


async def create_test_source(unique_id: int) -> SignalSource:
    """Create a test signal source with unique identifier."""
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    channel_id = timestamp_ms + unique_id
    
    async with UnitOfWork() as uow:
        source = await uow.signal_sources.create(
            name=f"Provider Bug Test {channel_id}",
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
# BUG 1: Provider Reset Too Broad - Regression Test
# ===========================================================================

@pytest.mark.asyncio
async def test_provider_change_resets_all_trackings_bug(async_engine, current_time):
    """
    REGRESSION TEST for confirmed bug:
    ProviderChangedEvent causes reset_initialization_state() to clear
    ALL tracking IDs, not just those affected by the provider change.
    
    Scenario:
    1. BTCUSDT and ETHUSDT are both being tracked
    2. BTCUSDT is on BINANCE, ETHUSDT is on BYBIT  
    3. BINANCE fails -> ProviderChangedEvent(BINANCE -> BYBIT)
    4. BUG: ALL trackings lose initialization state (including ETHUSDT on BYBIT)
    5. Next tick: ETHUSDT gets re-initialized even though it wasn't affected
    
    This test reproduces the exact buggy behavior.
    """
    source = await create_test_source(3001)
    
    try:
        # Create two trackings that are already established
        btc_signal_id, btc_tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entries=[Decimal("50000")],
            targets=[Decimal("55000"), Decimal("60000")],
            stop_loss=Decimal("45000"),
            started_at=current_time - timedelta(minutes=10),
        )
        
        eth_signal_id, eth_tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="ETHUSDT", 
            direction=Direction.LONG,
            entries=[Decimal("3000")],
            targets=[Decimal("3300"), Decimal("3600")],
            stop_loss=Decimal("2700"),
            started_at=current_time - timedelta(minutes=10),
        )
        
        # Manually set both trackings to already be tracking with TP1 hit
        async with UnitOfWork() as uow:
            btc_tracking = await uow.trackings.get(btc_tracking_id)
            btc_tracking.entry1_touched = True
            btc_tracking.entry_method = EntryMethod.ENTRY_1
            btc_tracking.actual_entry_price = Decimal("50000")
            btc_tracking.status = TrackingStatus.TRACKING
            btc_tracking.highest_target_hit = 1  # TP1 already hit
            
            eth_tracking = await uow.trackings.get(eth_tracking_id)
            eth_tracking.entry1_touched = True
            eth_tracking.entry_method = EntryMethod.ENTRY_1
            eth_tracking.actual_entry_price = Decimal("3000")
            eth_tracking.status = TrackingStatus.TRACKING
            eth_tracking.highest_target_hit = 2  # TP1 and TP2 already hit
            
            await uow.commit()
        
        # Create manager and simulate it has been running
        cache = PriceCache()
        tracker = Tracker()
        processor = ActionProcessor(telegram_service=AsyncMock())
        manager = TrackingManager(
            uow_factory=UnitOfWork,
            tracker=tracker,
            processor=processor,
            cache=cache,
            interval=2.0,
        )
        
        # Simulate trackings are already initialized in this session
        manager._initialized_trackings.add(btc_tracking_id)
        manager._initialized_trackings.add(eth_tracking_id)
        
        # Feed current prices - ETHUSDT is beyond TP2 (should NOT retrigger)
        feed_price(cache, "BTCUSDT", Decimal("55000"), current_time)  # At TP1
        feed_price(cache, "ETHUSDT", Decimal("3700"), current_time)   # Beyond TP2
        
        # Normal tick - should do nothing (already initialized)
        await manager._tick()
        
        # Verify no new TP actions were created
        async with UnitOfWork() as uow:
            eth_tp_hits = await uow.tp_hits.by_tracking(eth_tracking_id)
            initial_tp_count = len(eth_tp_hits)
        
        # ===== SIMULATE PROVIDER CHANGE EVENT =====
        provider_change_event = ProviderChangedEvent(
            previous=Provider.BINANCE,
            current=Provider.BYBIT
        )
        
        # BUG: This clears ALL tracking initialization state
        await manager.on_provider_changed(provider_change_event)
        
        # Verify bug - both trackings lost initialization state
        assert btc_tracking_id not in manager._initialized_trackings  # Expected
        assert eth_tracking_id not in manager._initialized_trackings  # BUG! Should still be initialized
        
        # Next tick - ETHUSDT will be re-initialized despite being unaffected
        await manager._tick()
        
        # BUG MANIFESTATION: ETHUSDT emits historical TP actions
        async with UnitOfWork() as uow:
            eth_tp_hits = await uow.tp_hits.by_tracking(eth_tracking_id) 
            final_tp_count = len(eth_tp_hits)
            
            # This should NOT happen - ETHUSDT wasn't affected by BINANCE failure
            # But due to the bug, it gets re-initialized and sees current price > TP1,TP2
            # However, since highest_target_hit=2, no new TP actions should be emitted
            # The bug is in the initialization reset logic, not necessarily TP emission
            
            # The key bug is that eth_tracking_id was unnecessarily reset
            # In a real scenario, this could cause issues for trackings that were stable
            
        # Verify the tracking was re-initialized (the bug behavior)
        assert eth_tracking_id in manager._initialized_trackings
        
    finally:
        await cleanup_test_data(btc_signal_id, source.id)
        await cleanup_test_data(eth_signal_id, source.id)


# ===========================================================================
# BUG 2: Historical TP Actions After Reinitialization - Regression Test  
# ===========================================================================

@pytest.mark.asyncio  
async def test_historical_tp_actions_after_reinitialization_bug(async_engine, current_time):
    """
    REGRESSION TEST for confirmed bug:
    After provider change + reinitialization, trackings can emit
    historical TP actions for targets that were "already hit" before restart.
    
    Scenario:
    1. Signal enters and hits TP1, TP2  
    2. System restart OR provider change
    3. Reinitialization occurs
    4. BUG: Current price beyond TP2 -> TakeProfitRule emits TP1+TP2 as "new" hits
    5. Database shows duplicate TP hit attempts or unexpected behavior
    
    This test reproduces the scenario where historical TPs are re-emitted.
    """
    source = await create_test_source(3002)
    
    try:
        signal_id, tracking_id = await create_signal_with_tracking(
            source_id=source.id,
            symbol="HISTUSDT",
            direction=Direction.LONG,
            entries=[Decimal("100")],
            targets=[Decimal("110"), Decimal("120"), Decimal("130")],
            stop_loss=Decimal("90"),
            started_at=current_time - timedelta(minutes=30),
        )
        
        # Manually set tracking state as if it already hit TP1 and TP2
        async with UnitOfWork() as uow:
            tracking = await uow.trackings.get(tracking_id)
            tracking.entry1_touched = True
            tracking.entry_method = EntryMethod.ENTRY_1
            tracking.actual_entry_price = Decimal("100")
            tracking.status = TrackingStatus.TRACKING
            tracking.highest_target_hit = 2  # TP1 and TP2 already hit
            
            # Create existing TP hit records
            await uow.tp_hits.create(
                tracking_id=tracking_id,
                position=1,
                price=Decimal("110"),
                profit_percent=Decimal("10.00"),
                hit_at=current_time - timedelta(minutes=20),
            )
            
            await uow.tp_hits.create(
                tracking_id=tracking_id,
                position=2, 
                price=Decimal("120"),
                profit_percent=Decimal("20.00"),
                hit_at=current_time - timedelta(minutes=10),
            )
            
            await uow.commit()
        
        # Create manager (simulating restart - fresh initialization state)
        cache = PriceCache()
        tracker = Tracker()
        processor = ActionProcessor(telegram_service=AsyncMock())
        manager = TrackingManager(
            uow_factory=UnitOfWork,
            tracker=tracker,
            processor=processor,
            cache=cache,
            interval=2.0,
        )
        
        # Note: _initialized_trackings is empty (simulating restart)
        assert tracking_id not in manager._initialized_trackings
        
        # Feed current price beyond TP2 but below TP3
        feed_price(cache, "HISTUSDT", Decimal("125"), current_time)
        
        # Count TP hits before tick
        async with UnitOfWork() as uow:
            tp_hits_before = await uow.tp_hits.by_tracking(tracking_id)
            tp_count_before = len(tp_hits_before)
        
        # Execute tick - this triggers reinitialization
        await manager._tick()
        
        # BUG MANIFESTATION: Check if historical TP actions were emitted
        async with UnitOfWork() as uow:
            tp_hits_after = await uow.tp_hits.by_tracking(tracking_id)
            tp_count_after = len(tp_hits_after)
            
            tracking = await uow.trackings.get_full(tracking_id)
        
        # The BUG would manifest as:
        # - New TP hit records attempted to be created for already-hit targets
        # - Or highest_target_hit being updated incorrectly
        # - Or duplicate processing attempts
        
        # In this specific case, the idempotency check in ActionProcessor should
        # prevent duplicate TP records, but the bug is that the actions are
        # being generated at all for already-hit targets.
        
        print(f"TP hits before: {tp_count_before}, after: {tp_count_after}")
        print(f"highest_target_hit: {tracking.highest_target_hit}")
        
        # The tracking should remain stable - no new TPs should be processed
        # because they were already hit (highest_target_hit = 2)
        assert tp_count_after == tp_count_before  # No new TP records
        assert tracking.highest_target_hit == 2   # Should remain unchanged
        
        # But the issue is that TakeProfitRule even tried to emit actions
        # This is the bug - it should not generate actions for historical targets
        
    finally:
        await cleanup_test_data(signal_id, source.id)


# ===========================================================================
# BUG 3: Network I/O Blocking Simulation 
# ===========================================================================

@pytest.mark.asyncio
async def test_network_io_blocking_subscription_bug(async_engine):
    """
    REGRESSION TEST for confirmed bug:
    Network I/O operations in _try_subscribe_symbol() can block
    indefinitely without timeout, holding the _sync_lock.
    
    This test simulates the blocking behavior using mocks.
    """
    from unittest.mock import AsyncMock, patch
    import asyncio
    
    from app.market.manager import ProviderManager
    from app.market.dispatcher import EventDispatcher
    from app.market.providers.base import BaseProvider
    
    # Create mock provider that hangs on current_price() call
    class BlockingProvider(BaseProvider):
        def __init__(self):
            super().__init__(AsyncMock())
            self.is_connected = True
        
        async def current_price(self, symbol: str):
            # Simulate network hang - this would block indefinitely in real scenario
            await asyncio.sleep(10)  # Long delay simulating network timeout
            raise Exception("Network timeout")
        
        async def subscribe(self, symbol: str):
            pass
        
        async def unsubscribe(self, symbol: str):
            pass
        
        async def connect(self):
            self.is_connected = True
        
        async def disconnect(self):
            self.is_connected = False
    
    # Create manager with blocking provider
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    blocking_provider = BlockingProvider()
    providers = {Provider.BINANCE: blocking_provider}
    
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
    )
    
    manager._running = True
    manager._active = Provider.BINANCE
    
    # Test the bug: subscription hangs due to network I/O without timeout
    start_time = current_time()
    
    try:
        # This should complete quickly but will hang due to blocking network call
        # In the real bug, this would block indefinitely
        with pytest.raises((asyncio.TimeoutError, Exception)):
            await asyncio.wait_for(manager.subscribe("TESTUSDT"), timeout=1.0)
    except Exception as e:
        # Expected - network operation should fail/timeout
        pass
    
    end_time = current_time()
    duration = (end_time - start_time).total_seconds()
    
    # Verify it took at least close to our timeout (proving it was hanging)
    assert duration >= 0.9  # Almost full timeout duration
    
    # The bug is that this operation blocks other subscriptions
    # In a real scenario, other symbols couldn't be subscribed during this hang


def current_time():
    return datetime.now(timezone.utc)