import logging
from decimal import Decimal

from app.database.enums import TrackingStatus, AuditEventType, Direction
from app.database.models import Tracking
from app.database.uow import UnitOfWork
from app.engine.actions import (
    PositionEntered,
    WaitingEntryExpired,
    StopLossHit,
    TakeProfitHit,
    RiskFreed,
    TrackingCompleted,
)


logger = logging.getLogger(__name__)


class ActionProcessor:
    """Executes side effects for tracking actions.
    
    Responsibilities:
    - Process Tracker actions
    - Apply tracking state changes
    - Create internal tracking records (TpHit)
    - Write AuditLog entries
    - Emit structured logs
    
    Transaction lifecycle is owned by TrackingManager.
    ActionProcessor uses the current transaction context.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def process(
        self,
        tracking: Tracking,
        actions: list,
    ) -> None:
        """Process actions with idempotency checks.
        
        Args:
            tracking: Tracking object attached to an active session
            actions: List of actions to process
        """
        for action in actions:
            # Check if already processed
            if await self._is_already_processed(tracking, action):
                logger.debug(
                    "Action already processed, skipping: tracking=%d action=%s",
                    tracking.id,
                    action.__class__.__name__,
                )
                continue

            # Route to handler
            match action:
                case PositionEntered():
                    await self._position_entered(tracking, action)
                case WaitingEntryExpired():
                    await self._waiting_entry_expired(tracking, action)
                case StopLossHit():
                    await self._stop_loss_hit(tracking, action)
                case TakeProfitHit():
                    await self._take_profit_hit(tracking, action)
                case RiskFreed():
                    await self._risk_freed(tracking, action)
                case TrackingCompleted():
                    await self._tracking_completed(tracking, action)

    async def _is_already_processed(
        self,
        tracking: Tracking,
        action,
    ) -> bool:
        """Check if action was already processed using existing state."""
        match action:
            case PositionEntered():
                # Check entry touched flags
                if action.entry_number == 1:
                    return tracking.entry1_touched
                else:
                    return tracking.entry2_touched

            case TakeProfitHit():
                # Query database for existing TpHit record
                # Database uniqueness constraint is the source of truth
                existing = await self._uow.tp_hits.by_tracking(tracking.id)
                return any(
                    tp.position == action.target_number
                    for tp in existing
                )

            case StopLossHit() | RiskFreed() | WaitingEntryExpired() | TrackingCompleted():
                # Check if already closed
                return not tracking.is_active

        return False

    async def _position_entered(
        self,
        tracking: Tracking,
        action: PositionEntered,
    ) -> None:
        """Handle position entry."""
        # Update tracking state
        if action.entry_number == 1:
            tracking.entry1_touched = True
        else:
            tracking.entry2_touched = True

        # Store the actual executed entry price (first entry only)
        if tracking.entry_price is None:
            tracking.entry_price = action.price

        # Initialize peak price tracking
        if tracking.peak_price_after_entry is None:
            tracking.peak_price_after_entry = action.price

        tracking.status = TrackingStatus.TRACKING

        # Write audit log
        await self._uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.ENTRY1_HIT if action.entry_number == 1 else AuditEventType.ENTRY2_HIT,
            payload={
                "entry_number": action.entry_number,
                "price": str(action.price),
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(
            "Entry %d hit: tracking=%d signal=%d symbol=%s price=%s",
            action.entry_number,
            tracking.id,
            tracking.signal_id,
            tracking.signal.symbol,
            action.price,
        )
        # TODO: Send Telegram notification: ENTRY1_HIT or ENTRY2_HIT

    async def _waiting_entry_expired(
        self,
        tracking: Tracking,
        action: WaitingEntryExpired,
    ) -> None:
        """Handle waiting entry expiration."""
        # Update tracking state
        tracking.status = TrackingStatus.CANCELLED
        tracking.is_active = False
        tracking.closed_at = action.timestamp

        # Write audit log
        await self._uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_EXPIRED,
            payload={
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(
            "Waiting entry expired: tracking=%d signal=%d symbol=%s",
            tracking.id,
            tracking.signal_id,
            tracking.signal.symbol,
        )
        # TODO: Send Telegram notification: SIGNAL_CANCELLED
        # TODO: Update statistics

    async def _stop_loss_hit(
        self,
        tracking: Tracking,
        action: StopLossHit,
    ) -> None:
        """Handle stop loss hit."""
        # Update tracking state
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp

        # Write audit log
        await self._uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_CLOSED,
            payload={
                "reason": "stop_loss",
                "price": str(action.price),
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(
            "Stop loss hit: tracking=%d signal=%d symbol=%s price=%s",
            tracking.id,
            tracking.signal_id,
            tracking.signal.symbol,
            action.price,
        )
        # TODO: Send Telegram notification: SIGNAL_CLOSED
        # TODO: Update statistics (loss)

    async def _take_profit_hit(
        self,
        tracking: Tracking,
        action: TakeProfitHit,
    ) -> None:
        """Handle take profit hit."""
        # Calculate profit percentage
        if tracking.entry_price:
            profit_pct = self._calculate_profit_percentage(
                tracking.signal.direction,
                tracking.entry_price,
                action.price,
            )
        else:
            profit_pct = Decimal("0")

        # Create TpHit record (unique constraint prevents duplicates)
        await self._uow.tp_hits.create(
            tracking_id=tracking.id,
            position=action.target_number,
            price=action.price,
            profit_percent=profit_pct,
            hit_at=action.timestamp,
        )

        # Update tracking state
        if action.target_number > tracking.highest_target_hit:
            tracking.highest_target_hit = action.target_number

        # Write audit log
        await self._uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.TARGET_HIT,
            payload={
                "target_number": action.target_number,
                "price": str(action.price),
                "profit_percent": str(profit_pct),
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(
            "TP%d hit: tracking=%d signal=%d symbol=%s price=%s profit=%.2f%%",
            action.target_number,
            tracking.id,
            tracking.signal_id,
            tracking.signal.symbol,
            action.price,
            profit_pct,
        )
        # TODO: Send Telegram notification: TARGET_HIT

    async def _risk_freed(
        self,
        tracking: Tracking,
        action: RiskFreed,
    ) -> None:
        """Handle risk free event."""
        # Update tracking state
        tracking.status = TrackingStatus.RISK_FREE
        tracking.is_active = False
        tracking.closed_at = action.timestamp

        # Write audit log
        await self._uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_CLOSED,
            payload={
                "reason": "risk_free",
                "price": str(action.price),
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(
            "Risk freed: tracking=%d signal=%d symbol=%s price=%s",
            tracking.id,
            tracking.signal_id,
            tracking.signal.symbol,
            action.price,
        )
        # TODO: Send Telegram notification: SIGNAL_CLOSED (risk free)
        # TODO: Update statistics (risk free)

    async def _tracking_completed(
        self,
        tracking: Tracking,
        action: TrackingCompleted,
    ) -> None:
        """Handle tracking completion."""
        # Update tracking state
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp

        # Write audit log
        await self._uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_CLOSED,
            payload={
                "reason": "all_targets_hit",
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(
            "Tracking completed: tracking=%d signal=%d symbol=%s all_targets_hit=True",
            tracking.id,
            tracking.signal_id,
            tracking.signal.symbol,
        )
        # TODO: Send Telegram notification: SIGNAL_CLOSED (all targets)
        # TODO: Update statistics (win)

    def _calculate_profit_percentage(
        self,
        direction: Direction,
        entry_price: Decimal,
        exit_price: Decimal,
    ) -> Decimal:
        """Calculate profit percentage based on direction."""
        if direction == Direction.LONG:
            profit = ((exit_price - entry_price) / entry_price) * Decimal("100")
        else:
            profit = ((entry_price - exit_price) / entry_price) * Decimal("100")

        return profit.quantize(Decimal("0.01"))
