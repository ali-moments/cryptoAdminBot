import pytest
from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from app.database.enums import TrackingStatus, Direction, AuditEventType
from app.database.models import Tracking, Signal
from app.engine.action_processor import ActionProcessor
from app.engine.actions import (
    PositionEntered,
    WaitingEntryExpired,
    StopLossHit,
    TakeProfitHit,
    RiskFreed,
    TrackingCompleted,
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
    """Create a mock Signal."""
    signal = Mock(spec=Signal)
    signal.id = 1
    signal.symbol = "BTCUSDT"
    signal.direction = Direction.LONG
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
    tracking.entry_price = None
    tracking.peak_price_after_entry = None
    tracking.status = TrackingStatus.WAITING_ENTRY
    tracking.is_active = True
    tracking.highest_target_hit = 0
    tracking.closed_at = None
    return tracking


@pytest.fixture
def processor(mock_uow):
    """Create ActionProcessor instance."""
    return ActionProcessor(mock_uow)


class TestPositionEntered:
    """Tests for PositionEntered action."""

    @pytest.mark.asyncio
    async def test_entry1_updates_state(self, processor, mock_tracking, mock_uow):
        """Test that entry 1 updates tracking state correctly."""
        action = PositionEntered(
            entry_number=1,
            price=Decimal("50000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.entry1_touched is True
        assert mock_tracking.entry_price == Decimal("50000.00")
        assert mock_tracking.peak_price_after_entry == Decimal("50000.00")
        assert mock_tracking.status == TrackingStatus.TRACKING

    @pytest.mark.asyncio
    async def test_entry2_updates_state(self, processor, mock_tracking, mock_uow):
        """Test that entry 2 updates tracking state correctly."""
        action = PositionEntered(
            entry_number=2,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.entry2_touched is True

    @pytest.mark.asyncio
    async def test_duplicate_entry1_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate entry 1 action is ignored."""
        mock_tracking.entry1_touched = True

        action = PositionEntered(
            entry_number=1,
            price=Decimal("50000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not update state again
        mock_uow.audit_logs.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_entry_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that entry creates audit log entry."""
        action = PositionEntered(
            entry_number=1,
            price=Decimal("50000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["tracking_id"] == 100
        assert call_kwargs["signal_id"] == 1
        assert call_kwargs["event"] == AuditEventType.ENTRY1_HIT


class TestTakeProfitHit:
    """Tests for TakeProfitHit action."""

    @pytest.mark.asyncio
    async def test_creates_tp_hit_record(self, processor, mock_tracking, mock_uow):
        """Test that TP hit creates TpHit record."""
        mock_tracking.entry_price = Decimal("50000.00")
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("51000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.tp_hits.create.assert_called_once()
        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        assert call_kwargs["tracking_id"] == 100
        assert call_kwargs["position"] == 1
        assert call_kwargs["price"] == Decimal("51000.00")

    @pytest.mark.asyncio
    async def test_updates_highest_target(self, processor, mock_tracking, mock_uow):
        """Test that TP hit updates highest_target_hit."""
        mock_tracking.entry_price = Decimal("50000.00")
        mock_tracking.highest_target_hit = 0
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=2,
            price=Decimal("52000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.highest_target_hit == 2

    @pytest.mark.asyncio
    async def test_duplicate_tp_is_ignored(self, processor, mock_tracking, mock_uow):
        """Test that duplicate TP action is ignored via database check."""
        mock_tracking.entry_price = Decimal("50000.00")

        # Mock existing TpHit in database
        existing_tp = Mock()
        existing_tp.position = 1
        mock_uow.tp_hits.by_tracking.return_value = [existing_tp]

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("51000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not create duplicate
        mock_uow.tp_hits.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_calculates_profit_percentage_long(self, processor, mock_tracking, mock_uow):
        """Test profit calculation for LONG position."""
        mock_tracking.entry_price = Decimal("50000.00")
        mock_tracking.signal.direction = Direction.LONG
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("51000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        profit = call_kwargs["profit_percent"]
        assert profit == Decimal("2.00")  # 2% gain

    @pytest.mark.asyncio
    async def test_calculates_profit_percentage_short(self, processor, mock_tracking, mock_uow):
        """Test profit calculation for SHORT position."""
        mock_tracking.entry_price = Decimal("50000.00")
        mock_tracking.signal.direction = Direction.SHORT
        mock_uow.tp_hits.by_tracking.return_value = []

        action = TakeProfitHit(
            target_number=1,
            price=Decimal("49000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        call_kwargs = mock_uow.tp_hits.create.call_args[1]
        profit = call_kwargs["profit_percent"]
        assert profit == Decimal("2.00")  # 2% gain


class TestStopLossHit:
    """Tests for StopLossHit action."""

    @pytest.mark.asyncio
    async def test_closes_tracking(self, processor, mock_tracking, mock_uow):
        """Test that SL closes tracking."""
        action = StopLossHit(
            price=Decimal("48000.00"),
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
            price=Decimal("48000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        # Should not create audit log
        mock_uow.audit_logs.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that SL creates audit log."""
        action = StopLossHit(
            price=Decimal("48000.00"),
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.SIGNAL_CLOSED
        assert call_kwargs["payload"]["reason"] == "stop_loss"


class TestWaitingEntryExpired:
    """Tests for WaitingEntryExpired action."""

    @pytest.mark.asyncio
    async def test_cancels_tracking(self, processor, mock_tracking, mock_uow):
        """Test that expiry cancels tracking."""
        action = WaitingEntryExpired(
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        assert mock_tracking.status == TrackingStatus.CANCELLED
        assert mock_tracking.is_active is False
        assert mock_tracking.closed_at == action.timestamp

    @pytest.mark.asyncio
    async def test_creates_audit_log(self, processor, mock_tracking, mock_uow):
        """Test that expiry creates audit log."""
        action = WaitingEntryExpired(
            timestamp=datetime.now(UTC),
        )

        await processor.process(mock_tracking, [action])

        mock_uow.audit_logs.create.assert_called_once()
        call_kwargs = mock_uow.audit_logs.create.call_args[1]
        assert call_kwargs["event"] == AuditEventType.SIGNAL_EXPIRED


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
        assert call_kwargs["payload"]["reason"] == "risk_free"


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
        assert call_kwargs["payload"]["reason"] == "all_targets_hit"


class TestIdempotency:
    """Integration tests for idempotency."""

    @pytest.mark.asyncio
    async def test_multiple_actions_processed_correctly(self, processor, mock_tracking, mock_uow):
        """Test that multiple actions are processed correctly."""
        mock_uow.tp_hits.by_tracking.return_value = []

        actions = [
            PositionEntered(
                entry_number=1,
                price=Decimal("50000.00"),
                timestamp=datetime.now(UTC),
            ),
            TakeProfitHit(
                target_number=1,
                price=Decimal("51000.00"),
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
        mock_uow.tp_hits.by_tracking.return_value = []

        actions = [
            PositionEntered(
                entry_number=1,
                price=Decimal("50000.00"),
                timestamp=datetime.now(UTC),
            ),
            PositionEntered(
                entry_number=1,
                price=Decimal("50000.00"),
                timestamp=datetime.now(UTC),
            ),
        ]

        await processor.process(mock_tracking, actions)

        # Only first action should be processed
        assert mock_uow.audit_logs.create.call_count == 1
