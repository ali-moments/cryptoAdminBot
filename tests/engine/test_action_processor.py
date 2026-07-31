import pytest
from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from app.database.enums import TrackingStatus, Direction, AuditEventType, EntryMethod
from app.database.models import Tracking, Signal, SignalEntry, SignalTarget
from app.engine.action_processor import ActionProcessor
from app.engine.actions import (
    PositionEntered,
    WaitingEntryExpired,
    StopLossHit,
    TakeProfitHit,
    RiskFreed,
    TrackingCompleted,
    EntryType,
)


@pytest.fixture
def mock_uow():
    """Create a mock UnitOfWork with repositories."""
    uow = Mock()
    uow.tp_hits = AsyncMock()
    uow.audit_logs = AsyncMock()
    return uow


@pytest.fixture
def mock_signal():
    """Create a mock Signal with entries and targets."""
    signal = Mock(spec=Signal)
    signal.id = 1
    signal.symbol = "BTCUSDT"
    signal.direction = Direction.LONG
    
    # Create mock entries
    entry1 = Mock(spec=SignalEntry)
    entry1.price = Decimal("48000.00")
    entry2 = Mock(spec=SignalEntry)
    entry2.price = Decimal("47000.00")
    signal.entries = [entry1, entry2]
    
    # Create mock targets
    target1 = Mock(spec=SignalTarget)
    target1.price = Decimal("52000.00")
    target2 = Mock(spec=SignalTarget)
    target2.price = Decimal("54000.00")
    signal.targets = [target1, target2]
    
    return signal


@pytest.fixture
def mock_tracking(mock_signal):
    """Create a mock Tracking."""
    tracking = Mock(spec=Tracking)
    tracking.id = 100
    tracking.signal_id = 1
    tracking.signal = mock_signal
    tracking.entry1_touched = False
    tracking.entry2_touched = False
    tracking.entry_method = None
    tracking.actual_entry_price = None
    tracking.peak_price_after_entry = None
    tracking.emergency_entry_triggered_at = None
    tracking.current_tp1_price = None
    tracking.status = TrackingStatus.WAITING_ENTRY
    tracking.is_active = True
    tracking.highest_target_hit = 0
    tracking.closed_at = None
    return tracking


@pytest.fixture
def processor(mock_uow):
    """Create ActionProcessor instance."""
    return ActionProcessor(mock_uow)


class TestPositionEnteredEntry1:
    """Tests for PositionEntered ENTRY_1 action."""

    @pytest.mark.asyncio
    async def test_entry1_updates_state(self, processor, mock_tracking, mock_uow):
        """Test that ENTRY_1 updates tracking state correctly."""
        action = PositionEntered(
            entry_type=EntryType.ENTRY_1,
            price=Decimal("48000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.entry1_touched is True
        assert mock_tracking.entry_method == EntryMethod.ENTRY_1
        assert mock_tracking.actual_entry_price == Decimal("48000.00")
        assert mock_tracking.peak_price_after_entry == Decimal("48000.00")
        assert mock_tracking.status == TrackingStatus.TRACKING

    @pytest.mark.asyncio
    async def test_entry1_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that ENTRY_1 creates audit log entry."""
        action = PositionEntered(
            entry_type=EntryType.ENTRY_1,
            price=Decimal("48000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["tracking_id"] == 100
        assert call_kwargs["signal_id"] == 1
        assert call_kwargs["event"] == AuditEventType.ENTRY1_HIT
        assert call_kwargs["payload"]["entry_type"] == "ENTRY_1"
        assert call_kwargs["payload"]["price"] == "48000.00"

    @pytest.mark.asyncio
    async def test_duplicate_entry1_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate ENTRY_1 action is ignored."""
        mock_tracking.entry1_touched = True

        action = PositionEntered(
            entry_type=EntryType.ENTRY_1,
            price=Decimal("48000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not update state again
        mock_uow.audit_logs.create.assert_not_called()


class TestPositionEnteredEntry2:
    """Tests for PositionEntered ENTRY_2 action."""

    @pytest.mark.asyncio
    async def test_entry2_updates_state(self, processor, mock_tracking, mock_uow):
        """Test that ENTRY_2 updates tracking state correctly."""
        action = PositionEntered(
            entry_type=EntryType.ENTRY_2,
            price=Decimal("47000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.entry2_touched is True

    @pytest.mark.asyncio
    async def test_entry2_recalculates_tp1_for_long(self, processor, mock_tracking, mock_uow):
        """Test that ENTRY_2 recalculates TP1 for LONG position."""
        # LONG signal entries structure:
        # - entries[0] = 48000.00 (EntryHigh - higher price, first to be hit)
        # - entries[1] = 47000.00 (EntryLow - lower price, DCA)
        # TP1 calculation: EntryHigh + (original_tp1 - EntryHigh) / 2
        entry_high = Decimal("48000.00")  # Higher price
        original_tp1 = Decimal("52000.00")
        expected_new_tp1 = entry_high + (original_tp1 - entry_high) / Decimal("2")  # 50000.00

        action = PositionEntered(
            entry_type=EntryType.ENTRY_2,
            price=Decimal("47000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.current_tp1_price == expected_new_tp1
        
        # Should create two audit logs: TP1 recalculation and ENTRY2
        assert mock_uow.audit_logs.create.call_count == 2
        
        # First call should be TP1 recalculation
        first_call_kwargs = mock_uow.audit_logs.create.call_args_list[0][1]
        assert first_call_kwargs["event"] == AuditEventType.TP1_RECALCULATED
        assert first_call_kwargs["payload"]["new_tp1"] == str(expected_new_tp1)
        
        # Second call should be ENTRY2
        second_call_kwargs = mock_uow.audit_logs.create.call_args_list[1][1]
        assert second_call_kwargs["event"] == AuditEventType.ENTRY2_HIT

    @pytest.mark.asyncio
    async def test_entry2_recalculates_tp1_for_short(self, processor, mock_tracking, mock_uow):
        """Test that ENTRY_2 recalculates TP1 for SHORT position."""
        # SHORT: EntryHigh is still the higher price
        # entries[0] = 48000.00 (EntryLow - lower price, first to be hit when coming from below)
        # entries[1] = 49000.00 (EntryHigh - higher price, DCA)
        # TP1 calculation: EntryHigh + (original_tp1 - EntryHigh) / 2
        entry_high = Decimal("49000.00")  # Higher price
        original_tp1 = Decimal("44000.00")
        expected_new_tp1 = entry_high + (original_tp1 - entry_high) / Decimal("2")  # 46500.00

        # Reconfigure signal for SHORT
        mock_tracking.signal.direction = Direction.SHORT
        mock_tracking.signal.entries[0].price = Decimal("48000.00")
        mock_tracking.signal.entries[1].price = Decimal("49000.00")
        mock_tracking.signal.targets[0].price = Decimal("44000.00")

        action = PositionEntered(
            entry_type=EntryType.ENTRY_2,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.current_tp1_price == expected_new_tp1

    @pytest.mark.asyncio
    async def test_duplicate_entry2_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate ENTRY_2 action is ignored."""
        mock_tracking.entry2_touched = True

        action = PositionEntered(
            entry_type=EntryType.ENTRY_2,
            price=Decimal("47000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not update state again
        mock_uow.audit_logs.create.assert_not_called()


class TestPositionEnteredEmergencyEntry:
    """Tests for PositionEntered EMERGENCY_ENTRY action."""

    @pytest.mark.asyncio
    async def test_emergency_entry_updates_state(self, processor, mock_tracking, mock_uow):
        """Test that EMERGENCY_ENTRY updates tracking state correctly."""
        action = PositionEntered(
            entry_type=EntryType.EMERGENCY_ENTRY,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.entry_method == EntryMethod.EMERGENCY_ENTRY
        assert mock_tracking.actual_entry_price == Decimal("49000.00")
        assert mock_tracking.peak_price_after_entry == Decimal("49000.00")
        assert mock_tracking.emergency_entry_triggered_at == action.timestamp
        assert mock_tracking.status == TrackingStatus.TRACKING

    @pytest.mark.asyncio
    async def test_emergency_entry_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that EMERGENCY_ENTRY creates audit log entry."""
        action = PositionEntered(
            entry_type=EntryType.EMERGENCY_ENTRY,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.EMERGENCY_ENTRY_HIT
        assert call_kwargs["payload"]["entry_type"] == "EMERGENCY_ENTRY"

    @pytest.mark.asyncio
    async def test_duplicate_emergency_entry_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate EMERGENCY_ENTRY action is ignored."""
        mock_tracking.entry_method = EntryMethod.EMERGENCY_ENTRY

        action = PositionEntered(
            entry_type=EntryType.EMERGENCY_ENTRY,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not update state again
        mock_uow.audit_logs.create.assert_not_called()


class TestTakeProfitHit:
    """Tests for TakeProfitHit action."""

    @pytest.mark.asyncio
    async def test_creates_tp_hit_record(self, processor, mock_tracking, mock_uow):
        """Test that TP hit creates TpHit record."""
        mock_tracking.actual_entry_price = Decimal("48000.00")
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("52000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.tp_hits.create.assert_called_once()
        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        assert call_kwargs["tracking_id"] == 100
        assert call_kwargs["position"] == 1
        assert call_kwargs["price"] == Decimal("52000.00")
        assert call_kwargs["hit_at"] == action.timestamp

    @pytest.mark.asyncio
    async def test_updates_highest_target(self, processor, mock_tracking, mock_uow):
        """Test that TP hit updates highest_target_hit."""
        mock_tracking.actual_entry_price = Decimal("48000.00")
        mock_tracking.highest_target_hit = 0
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=2,
            price=Decimal("54000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.highest_target_hit == 2

    @pytest.mark.asyncio
    async def test_duplicate_tp_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate TP action is ignored via database check."""
        mock_tracking.actual_entry_price = Decimal("48000.00")

        # Mock existing TpHit in database
        existing_tp = Mock()
        existing_tp.position = 1
        mock_uow.tp_hits.by_tracking.return_value = [existing_tp]

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("52000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not create duplicate
        mock_uow.tp_hits.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_calculates_profit_percentage_long(self, processor, mock_tracking, mock_uow):
        """Test profit calculation for LONG position."""
        mock_tracking.actual_entry_price = Decimal("48000.00")
        mock_tracking.signal.direction = Direction.LONG
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("52000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        profit = call_kwargs["profit_percent"]
        # (52000 - 48000) / 48000 * 100 = 8.33%
        assert profit == Decimal("8.33")

    @pytest.mark.asyncio
    async def test_calculates_profit_percentage_short(self, processor, mock_tracking, mock_uow):
        """Test profit calculation for SHORT position."""
        mock_tracking.actual_entry_price = Decimal("48000.00")
        mock_tracking.signal.direction = Direction.SHORT
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("44000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        profit = call_kwargs["profit_percent"]
        # (48000 - 44000) / 48000 * 100 = 8.33%
        assert profit == Decimal("8.33")

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that TP hit creates audit log."""
        mock_tracking.actual_entry_price = Decimal("48000.00")
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("52000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should create audit log
        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.TARGET_HIT
        assert call_kwargs["payload"]["target_number"] == 1


class TestStopLossHit:
    """Tests for StopLossHit action."""

    @pytest.mark.asyncio
    async def test_closes_tracking(self, processor, mock_tracking, mock_uow):
        """Test that SL closes tracking."""
        action = StopLossHit(
            price=Decimal("46000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.status == TrackingStatus.CLOSED
        assert mock_tracking.is_active is False
        assert mock_tracking.closed_at == action.timestamp

    @pytest.mark.asyncio
    async def test_duplicate_close_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate SL action is ignored."""
        mock_tracking.is_active = False

        action = StopLossHit(
            price=Decimal("46000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not create audit log
        mock_uow.audit_logs.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that SL creates audit log."""
        action = StopLossHit(
            price=Decimal("46000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.SIGNAL_CLOSED
        assert call_kwargs["payload"]["reason"] == "stop_loss"
        assert call_kwargs["payload"]["price"] == "46000.00"


class TestWaitingEntryExpired:
    """Tests for WaitingEntryExpired action."""

    @pytest.mark.asyncio
    async def test_cancels_tracking_timeout(self, processor, mock_tracking, mock_uow):
        """Test that timeout expiry cancels tracking."""
        action = WaitingEntryExpired(
            reason="timeout",
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.status == TrackingStatus.CANCELLED
        assert mock_tracking.is_active is False
        assert mock_tracking.closed_at == action.timestamp

    @pytest.mark.asyncio
    async def test_cancels_tracking_tp1_crossed(self, processor, mock_tracking, mock_uow):
        """Test that tp1_crossed expiry cancels tracking."""
        action = WaitingEntryExpired(
            reason="tp1_crossed",
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.status == TrackingStatus.CANCELLED
        assert mock_tracking.is_active is False

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that expiry creates audit log."""
        action = WaitingEntryExpired(
            reason="timeout",
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.SIGNAL_EXPIRED
        assert call_kwargs["payload"]["reason"] == "timeout"


class TestRiskFreed:
    """Tests for RiskFreed action."""

    @pytest.mark.asyncio
    async def test_closes_tracking_as_risk_free(self, processor, mock_tracking, mock_uow):
        """Test that risk free closes tracking with correct status."""
        action = RiskFreed(
            price=Decimal("48500.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.status == TrackingStatus.RISK_FREE
        assert mock_tracking.is_active is False
        assert mock_tracking.closed_at == action.timestamp

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that risk free creates audit log."""
        action = RiskFreed(
            price=Decimal("48500.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.SIGNAL_CLOSED
        assert call_kwargs["payload"]["reason"] == "risk_free"
        assert call_kwargs["payload"]["price"] == "48500.00"


class TestTrackingCompleted:
    """Tests for TrackingCompleted action."""

    @pytest.mark.asyncio
    async def test_closes_tracking(self, processor, mock_tracking, mock_uow):
        """Test that completion closes tracking."""
        action = TrackingCompleted(
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.status == TrackingStatus.CLOSED
        assert mock_tracking.is_active is False
        assert mock_tracking.closed_at == action.timestamp

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that completion creates audit log."""
        action = TrackingCompleted(
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.SIGNAL_CLOSED
        assert call_kwargs["payload"]["reason"] == "all_targets_hit"


class TestIdempotency:
    """Integration tests for idempotency."""

    @pytest.mark.asyncio
    async def test_multiple_different_actions_processed_correctly(self, processor, mock_tracking, mock_uow):
        """Test that multiple different actions are processed correctly."""
        mock_uow.tp_hits.by_tracking.return_value = []

        actions = [
            PositionEntered(
                entry_type=EntryType.ENTRY_1,
                price=Decimal("48000.00"),
                timestamp=datetime.now(UTC),
            ),
            TakeProfitHit(
                target_number=1,
                price=Decimal("52000.00"),
                timestamp=datetime.now(UTC),
            ),
        ]

        await processor.process(mock_tracking, actions)

        # Both actions should be processed
        assert mock_uow.audit_logs.create.call_count == 2
        assert mock_uow.tp_hits.create.call_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_actions_in_same_batch_handled(self, processor, mock_tracking, mock_uow):
        """Test that duplicate actions in same batch are handled correctly."""
        actions = [
            PositionEntered(
                entry_type=EntryType.ENTRY_1,
                price=Decimal("48000.00"),
                timestamp=datetime.now(UTC),
            ),
            PositionEntered(
                entry_type=EntryType.ENTRY_1,
                price=Decimal("48000.00"),
                timestamp=datetime.now(UTC),
            ),
        ]

        await processor.process(mock_tracking, actions)

        # Only first action should be processed
        assert mock_uow.audit_logs.create.call_count == 1

    @pytest.mark.asyncio
    async def test_entry2_without_entry1_still_processes(self, processor, mock_tracking, mock_uow):
        """Test that ENTRY_2 can process even if ENTRY_1 hasn't occurred."""
        # This tests that the actions are independent
        action = PositionEntered(
            entry_type=EntryType.ENTRY_2,
            price=Decimal("47000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should process successfully
        assert mock_uow.audit_logs.create.call_count >= 1
        assert mock_tracking.entry2_touched is True


class TestProfitCalculation:
    """Tests for profit calculation logic."""

    @pytest.mark.asyncio
    async def test_zero_profit_when_no_entry_price(self, processor, mock_tracking, mock_uow):
        """Test that profit is zero when actual_entry_price is None."""
        mock_tracking.actual_entry_price = None
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("52000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        assert call_kwargs["profit_percent"] == Decimal("0")

    @pytest.mark.asyncio
    async def test_profit_calculation_precision(self, processor, mock_tracking, mock_uow):
        """Test that profit calculation is precise to 2 decimal places."""
        mock_tracking.actual_entry_price = Decimal("48123.45")
        mock_tracking.signal.direction = Direction.LONG
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        profit = call_kwargs["profit_percent"]
        
        # Verify it's quantized to 2 decimal places
        assert profit == profit.quantize(Decimal("0.01"))
