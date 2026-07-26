from app.database.enums import TrackingStatus
from app.database.models import Tracking
from app.engine.actions import (
    PositionEntered,
    WaitingEntryExpired,
    StopLossHit,
    TakeProfitHit,
    RiskFreed,
    TrackingCompleted,
)


class TrackingHandler:
    """Stateless action processor.
    
    Receives an attached Tracking object and mutates it based on Actions.
    Never opens UnitOfWork, never commits, never re-fetches.
    Transaction lifecycle is owned by TrackingManager.
    """

    async def handle(
        self,
        tracking: Tracking,
        actions: list,
    ) -> None:
        """Process actions by mutating the tracking object.
        
        Args:
            tracking: Tracking object attached to an active session
            actions: List of actions to process
        """
        for action in actions:
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

    async def _position_entered(
        self,
        tracking: Tracking,
        action: PositionEntered,
    ) -> None:
        if action.entry_number == 1:
            tracking.entry1_touched = True
            tracking.entry1_at = action.timestamp
        else:
            tracking.entry2_touched = True
            tracking.entry2_at = action.timestamp

        # Store the actual executed entry price (first entry only)
        if tracking.entry_price is None:
            tracking.entry_price = action.price

        # Initialize peak price tracking
        if tracking.peak_price_after_entry is None:
            tracking.peak_price_after_entry = action.price

        tracking.status = TrackingStatus.TRACKING

    async def _waiting_entry_expired(
        self,
        tracking: Tracking,
        action: WaitingEntryExpired,
    ) -> None:
        tracking.status = TrackingStatus.CANCELLED
        tracking.is_active = False
        tracking.closed_at = action.timestamp

    async def _stop_loss_hit(
        self,
        tracking: Tracking,
        action: StopLossHit,
    ) -> None:
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp

    async def _take_profit_hit(
        self,
        tracking: Tracking,
        action: TakeProfitHit,
    ) -> None:
        # Update highest target hit
        if action.target_number > tracking.highest_target_hit:
            tracking.highest_target_hit = action.target_number
        
        # Create TP hit record
        # (Will be implemented when we add TP hit repository logic)

    async def _risk_freed(
        self,
        tracking: Tracking,
        action: RiskFreed,
    ) -> None:
        tracking.status = TrackingStatus.RISK_FREE
        tracking.is_active = False
        tracking.closed_at = action.timestamp

    async def _tracking_completed(
        self,
        tracking: Tracking,
        action: TrackingCompleted,
    ) -> None:
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp
