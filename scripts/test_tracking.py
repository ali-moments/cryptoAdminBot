"""
Integration test framework for tracking engine.

This script validates the complete tracking flow:
Market Tick → Tracker → ActionProcessor → Database

Tests verify:
- Entry detection (first/second)
- Take profit progression (TP1/TP2/TP3)
- Stop loss hits
- Risk-free logic
- Expiry timeouts
- Idempotency (duplicate ticks)
- Recovery (restart scenarios)
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncContextManager

from sqlalchemy import select

from app.config.settings import settings
from app.database.db import engine, SessionLocal
from app.database.models import Signal, SignalEntry, SignalTarget, SignalSource, Tracking, TpHit, AuditLog
from app.database.enums import Direction, SignalStatus, TrackingStatus, Provider, AuditEventType
from app.database.uow import UnitOfWork
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.engine.tracker import Tracker
from app.engine.action_processor import ActionProcessor
from app.engine.tracking_manager import TrackingManager


# ============================================================================
# Test Helpers
# ============================================================================

class TestContext:
    """Holds test state and provides helper methods."""
    
    def __init__(self, test_id: int = 0):
        self.cache = PriceCache()
        self.tracker = Tracker()
        self.processor = None  # Created per-test with fresh UoW
        self.manager = None
        self.source: SignalSource | None = None
        self.signal: Signal | None = None
        self.tracking: Tracking | None = None
        self.current_time = datetime.now(timezone.utc)
        # Use timestamp + test_id for uniqueness across runs
        self.test_id = int(self.current_time.timestamp() * 1000) + test_id
    
    async def setup_source(self):
        """Create a test signal source."""
        async with UnitOfWork() as uow:
            self.source = await uow.signal_sources.create(
                name=f"Test Source {self.test_id}",
                parser_name="test_parser",
                telegram_channel_id=123456789 + self.test_id,  # Unique per test
                is_active=True,
            )
            await uow.commit()
    
    async def create_signal(
        self,
        symbol: str,
        direction: Direction,
        entries: list[Decimal],
        targets: list[Decimal],
        stop_loss: Decimal,
        leverage: int = 10,
        expires_in_hours: int = 2,
    ) -> Signal:
        """Create a signal with entries and targets."""
        async with UnitOfWork() as uow:
            # Create signal
            signal = await uow.signals.create(
                source_id=self.source.id,
                symbol=symbol,
                direction=direction,
                leverage=leverage,
                stop_loss=stop_loss,
                expires_at=self.current_time + timedelta(hours=expires_in_hours),
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
            
            await uow.commit()
            
            # Reload with relationships
            signal = await uow.signals.get_full(signal.id)
            
        self.signal = signal
        return signal
    
    async def create_tracking(
        self,
        signal: Signal | None = None,
        provider: Provider = Provider.BINANCE,
    ) -> Tracking:
        """Create tracking for signal."""
        if signal is None:
            signal = self.signal
        
        async with UnitOfWork() as uow:
            # Initialize current_tp1_price to original TP1
            initial_tp1 = signal.targets[0].price if signal.targets else None
            
            tracking = await uow.trackings.create(
                signal_id=signal.id,
                status=TrackingStatus.WAITING_ENTRY,
                provider=provider,
                is_active=True,
                started_at=self.current_time,
                current_stop_loss=signal.stop_loss,
                current_tp1_price=initial_tp1,  # Initialize with original TP1
            )
            await uow.commit()
            
            # Reload with relationships
            tracking = await uow.trackings.get_full(tracking.id)
        
        self.tracking = tracking
        return tracking
    
    def feed_price(
        self,
        symbol: str,
        price: Decimal,
        timestamp: datetime | None = None,
        provider: Provider = Provider.BINANCE,
    ):
        """Feed a price tick to the cache."""
        if timestamp is None:
            timestamp = self.current_time
        
        tick = PriceTick(
            symbol=symbol,
            price=price,
            provider=provider,
            timestamp=timestamp,
        )
        
        # Update cache directly
        self.cache._prices[symbol] = tick
    
    async def run_tick(self):
        """Run a single tracking manager tick."""
        async with UnitOfWork() as uow:
            processor = ActionProcessor(uow)
            manager = TrackingManager(
                uow=uow,
                tracker=self.tracker,
                processor=processor,
                cache=self.cache,
                interval=2.0,
            )
            
            # Run single tick
            await manager._tick()
    
    async def get_tracking(self, tracking_id: int) -> Tracking:
        """Reload tracking from database."""
        async with UnitOfWork() as uow:
            return await uow.trackings.get_full(tracking_id)
    
    async def get_tp_hits(self, tracking_id: int) -> list[TpHit]:
        """Get all TP hits for tracking."""
        async with UnitOfWork() as uow:
            return await uow.tp_hits.by_tracking(tracking_id)
    
    async def get_audit_logs(self, tracking_id: int) -> list[AuditLog]:
        """Get all audit logs for tracking."""
        async with UnitOfWork() as uow:
            return await uow.audit_logs.by_tracking(tracking_id)
    
    async def cleanup(self):
        """Clean up test data."""
        async with SessionLocal() as session:
            # Delete in reverse dependency order
            await session.execute(select(AuditLog).where(AuditLog.signal_id == self.signal.id))
            await session.execute(select(Tracking).where(Tracking.signal_id == self.signal.id))
            await session.execute(select(SignalTarget).where(SignalTarget.signal_id == self.signal.id))
            await session.execute(select(SignalEntry).where(SignalEntry.signal_id == self.signal.id))
            await session.execute(select(Signal).where(Signal.id == self.signal.id))
            await session.execute(select(SignalSource).where(SignalSource.id == self.source.id))
            await session.commit()


# ============================================================================
# Test Scenarios
# ============================================================================

async def test_entry_hit_first():
    """Test: First entry hit."""
    print("\n=== Test: First Entry Hit ===")
    
    ctx = TestContext(test_id=1)
    await ctx.setup_source()
    
    # Create LONG signal: entries=[50000, 49000], targets=[51000, 52000, 53000], SL=48000
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000"), Decimal("49000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Feed price at first entry
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    
    # Run tick
    await ctx.run_tick()
    
    # Verify
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.entry1_touched == True
    assert tracking.entry2_touched == False
    assert tracking.status == TrackingStatus.TRACKING
    assert tracking.actual_entry_price == Decimal("50000")
    assert tracking.is_active == True
    
    # Check audit log
    logs = await ctx.get_audit_logs(tracking.id)
    assert len(logs) == 1
    assert logs[0].event == AuditEventType.ENTRY1_HIT
    
    print("✓ First entry detected correctly")
    print(f"  Entry price: {tracking.actual_entry_price}")
    print(f"  Status: {tracking.status}")


async def test_entry_hit_both_entries():
    """Test: Both entries can be hit over time (DCA scenario)."""
    print("\n=== Test: Both Entries Hit (DCA) ===")
    
    ctx = TestContext(test_id=2)
    await ctx.setup_source()
    
    # Create LONG signal with two entries
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000"), Decimal("49000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Hit first entry
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.entry1_touched == True, f"entry1 should be touched after hitting 50000"
    assert tracking.entry2_touched == False, f"entry2 should not be touched yet"
    assert tracking.actual_entry_price == Decimal("50000"), f"actual_entry_price should be 50000"
    
    print("✓ First entry hit")
    
    # Hit second entry (price drops further - DCA scenario)
    ctx.feed_price("BTCUSDT", Decimal("49000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.entry1_touched == True, f"entry1 should still be touched"
    assert tracking.entry2_touched == True, f"entry2 should be touched after hitting 49000"
    assert tracking.actual_entry_price == Decimal("50000"), f"actual_entry_price should remain at first entry (50000)"
    
    # Check audit logs - should have both entry events
    logs = await ctx.get_audit_logs(tracking.id)
    entry_logs = [log for log in logs if log.event in [AuditEventType.ENTRY1_HIT, AuditEventType.ENTRY2_HIT]]
    assert len(entry_logs) == 2, f"Should have 2 entry events, got {len(entry_logs)}"
    
    print("✓ Both entries hit (DCA scenario)")
    print(f"  Entry price: {tracking.actual_entry_price}")


async def test_tp_progression():
    """Test: TP1 → TP2 → TP3 progression."""
    print("\n=== Test: TP Progression ===")
    
    ctx = TestContext(test_id=3)
    await ctx.setup_source()
    
    # Create LONG signal
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter position
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    # Hit TP1
    ctx.feed_price("BTCUSDT", Decimal("51000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 1
    assert tracking.is_active == True
    
    tp_hits = await ctx.get_tp_hits(tracking.id)
    assert len(tp_hits) == 1
    assert tp_hits[0].position == 1
    assert tp_hits[0].price == Decimal("51000")
    
    print("✓ TP1 hit")
    
    # Hit TP2
    ctx.feed_price("BTCUSDT", Decimal("52000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 2
    
    tp_hits = await ctx.get_tp_hits(tracking.id)
    assert len(tp_hits) == 2
    assert tp_hits[1].position == 2
    
    print("✓ TP2 hit")
    
    # Hit TP3 (should complete tracking)
    ctx.feed_price("BTCUSDT", Decimal("53000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 3
    assert tracking.status == TrackingStatus.CLOSED
    assert tracking.is_active == False
    
    tp_hits = await ctx.get_tp_hits(tracking.id)
    assert len(tp_hits) == 3
    
    print("✓ TP3 hit and tracking completed")
    
    # Check audit logs
    logs = await ctx.get_audit_logs(tracking.id)
    target_events = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
    assert len(target_events) == 3
    
    closed_events = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
    assert len(closed_events) == 1


async def test_stop_loss_hit():
    """Test: Stop loss hit."""
    print("\n=== Test: Stop Loss Hit ===")
    
    ctx = TestContext(test_id=4)
    await ctx.setup_source()
    
    # Create LONG signal
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter position
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    # Hit stop loss
    ctx.feed_price("BTCUSDT", Decimal("48000"))
    await ctx.run_tick()
    
    # Verify
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.status == TrackingStatus.CLOSED
    assert tracking.is_active == False
    assert tracking.closed_at is not None
    
    # Check audit log
    logs = await ctx.get_audit_logs(tracking.id)
    closed_events = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].payload["reason"] == "stop_loss"
    
    print("✓ Stop loss hit and tracking closed")


async def test_risk_free():
    """Test: Risk-free stop loss hit."""
    print("\n=== Test: Risk Free ===")
    
    ctx = TestContext(test_id=5)
    await ctx.setup_source()
    
    # Create LONG signal
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("52000"), Decimal("54000"), Decimal("56000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter position
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    # Move price halfway to TP1 (51000)
    ctx.feed_price("BTCUSDT", Decimal("51000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.halfway_to_tp1_reached == True
    
    print("✓ Halfway to TP1 reached")
    
    # Hit stop loss (should be risk-free)
    ctx.feed_price("BTCUSDT", Decimal("48000"))
    await ctx.run_tick()
    
    # Verify
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.status == TrackingStatus.RISK_FREE
    assert tracking.is_active == False
    assert tracking.highest_target_hit == 0  # No TP hit
    
    # Check audit log
    logs = await ctx.get_audit_logs(tracking.id)
    closed_events = [log for log in logs if log.event == AuditEventType.SIGNAL_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].payload["reason"] == "risk_free"
    
    print("✓ Risk-free stop loss detected")


async def test_waiting_entry_expired():
    """Test: Entry waiting period expires."""
    print("\n=== Test: Waiting Entry Expired ===")
    
    ctx = TestContext(test_id=6)
    await ctx.setup_source()
    
    # Create signal
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Feed price BELOW TP1 but ABOVE entry (not hit entry, not crossed TP1)
    ctx.feed_price("BTCUSDT", Decimal("50500"))
    await ctx.run_tick()
    
    # Verify still waiting
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.status == TrackingStatus.WAITING_ENTRY, f"Expected WAITING_ENTRY, got {tracking.status}"
    assert tracking.is_active == True, f"Expected is_active=True, got {tracking.is_active}"
    
    # Advance time past 2 hour expiry
    ctx.current_time = signal.created_at + timedelta(hours=2, minutes=1)
    ctx.feed_price("BTCUSDT", Decimal("50500"), timestamp=ctx.current_time)
    await ctx.run_tick()
    
    # Verify expired
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.status == TrackingStatus.CANCELLED, f"Expected CANCELLED, got {tracking.status}"
    assert tracking.is_active == False, f"Expected is_active=False, got {tracking.is_active}"
    
    # Check audit log
    logs = await ctx.get_audit_logs(tracking.id)
    expired_events = [log for log in logs if log.event == AuditEventType.SIGNAL_EXPIRED]
    assert len(expired_events) == 1, f"Expected 1 expired event, got {len(expired_events)}"
    assert expired_events[0].payload["reason"] == "timeout", f"Expected timeout reason, got {expired_events[0].payload['reason']}"
    
    print("✓ Waiting entry expiry detected")


async def test_duplicate_ticks_idempotency():
    """Test: Duplicate ticks are handled idempotently."""
    print("\n=== Test: Duplicate Ticks (Idempotency) ===")
    
    ctx = TestContext(test_id=7)
    await ctx.setup_source()
    
    # Create signal
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter position
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    # Hit TP1
    ctx.feed_price("BTCUSDT", Decimal("51000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 1
    
    tp_hits = await ctx.get_tp_hits(tracking.id)
    assert len(tp_hits) == 1
    
    # Send duplicate tick for TP1 (should be ignored)
    ctx.feed_price("BTCUSDT", Decimal("51000"))
    await ctx.run_tick()
    
    # Verify no duplicate
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 1
    
    tp_hits = await ctx.get_tp_hits(tracking.id)
    assert len(tp_hits) == 1  # Still only one
    
    logs = await ctx.get_audit_logs(tracking.id)
    target_events = [log for log in logs if log.event == AuditEventType.TARGET_HIT]
    assert len(target_events) == 1  # Still only one
    
    print("✓ Duplicate ticks handled idempotently")


async def test_restart_recovery():
    """Test: Recovery after restart (reprocess same state)."""
    print("\n=== Test: Restart Recovery ===")
    
    ctx = TestContext(test_id=8)
    await ctx.setup_source()
    
    # Create signal and enter
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("51000"), Decimal("52000"), Decimal("53000")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter and hit TP1
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    ctx.feed_price("BTCUSDT", Decimal("51000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 1
    
    print("✓ Initial state: entered and TP1 hit")
    
    # Simulate restart: create new context (new Tracker, new ActionProcessor)
    ctx2 = TestContext(test_id=8)  # Same test_id, reusing same source
    ctx2.tracker = Tracker()  # Fresh tracker
    ctx2.source = ctx.source
    ctx2.signal = signal
    ctx2.tracking = tracking
    ctx2.current_time = ctx.current_time
    
    # Feed same price (TP1 level) - should not duplicate
    ctx2.feed_price("BTCUSDT", Decimal("51000"))
    await ctx2.run_tick()
    
    # Verify no duplicate
    tracking = await ctx2.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 1
    
    tp_hits = await ctx2.get_tp_hits(tracking.id)
    assert len(tp_hits) == 1
    
    print("✓ After restart: no duplicate TP1")
    
    # Now hit TP2 - should work normally
    ctx2.feed_price("BTCUSDT", Decimal("52000"))
    await ctx2.run_tick()
    
    tracking = await ctx2.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 2
    
    tp_hits = await ctx2.get_tp_hits(tracking.id)
    assert len(tp_hits) == 2
    
    print("✓ After restart: TP2 processed correctly")


async def test_short_signal():
    """Test: SHORT signal flow (inverted entry/TP logic)."""
    print("\n=== Test: SHORT Signal ===")
    
    ctx = TestContext(test_id=9)
    await ctx.setup_source()
    
    # Create SHORT signal: entries=[50000, 51000], targets=[49000, 48000, 47000], SL=52000
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.SHORT,
        entries=[Decimal("50000"), Decimal("51000")],
        targets=[Decimal("49000"), Decimal("48000"), Decimal("47000")],
        stop_loss=Decimal("52000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter at first entry (50000)
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.entry1_touched == True
    assert tracking.actual_entry_price == Decimal("50000")
    
    print("✓ SHORT entry detected")
    
    # Hit TP1 (49000 - price going down)
    ctx.feed_price("BTCUSDT", Decimal("49000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 1
    
    print("✓ SHORT TP1 hit")
    
    # Hit stop loss (52000 - price going up)
    ctx.feed_price("BTCUSDT", Decimal("52000"))
    await ctx.run_tick()
    
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.status == TrackingStatus.CLOSED
    assert tracking.is_active == False
    
    print("✓ SHORT stop loss hit")


async def test_multiple_tps_single_tick():
    """Test: Multiple TPs hit in single tick (price gap)."""
    print("\n=== Test: Multiple TPs Single Tick ===")
    
    ctx = TestContext(test_id=10)
    await ctx.setup_source()
    
    # Create signal with close TPs
    signal = await ctx.create_signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entries=[Decimal("50000")],
        targets=[Decimal("51000"), Decimal("51100"), Decimal("51200")],
        stop_loss=Decimal("48000"),
    )
    
    tracking = await ctx.create_tracking(signal)
    
    # Enter position
    ctx.feed_price("BTCUSDT", Decimal("50000"))
    await ctx.run_tick()
    
    # Jump price to hit all TPs at once
    ctx.feed_price("BTCUSDT", Decimal("51200"))
    await ctx.run_tick()
    
    # Verify all TPs hit
    tracking = await ctx.get_tracking(tracking.id)
    assert tracking.highest_target_hit == 3
    assert tracking.status == TrackingStatus.CLOSED
    
    tp_hits = await ctx.get_tp_hits(tracking.id)
    assert len(tp_hits) == 3
    assert tp_hits[0].position == 1
    assert tp_hits[1].position == 2
    assert tp_hits[2].position == 3
    
    print("✓ Multiple TPs hit in single tick")


# ============================================================================
# Test Runner
# ============================================================================

async def run_all_tests():
    """Run all test scenarios."""
    print("=" * 60)
    print("TRACKING ENGINE INTEGRATION TESTS")
    print("=" * 60)
    
    tests = [
        test_entry_hit_first,
        test_entry_hit_both_entries,
        test_tp_progression,
        test_stop_loss_hit,
        test_risk_free,
        test_waiting_entry_expired,
        test_duplicate_ticks_idempotency,
        test_restart_recovery,
        test_short_signal,
        test_multiple_tps_single_tick,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            await test()
            passed += 1
        except AssertionError as e:
            print(f"✗ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ ERROR: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


async def main():
    """Main entry point."""
    try:
        success = await run_all_tests()
        exit(0 if success else 1)
    finally:
        # Clean up database connections
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
