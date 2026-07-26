from app.database.enums import Direction
from app.database.models import Tracking, SignalEntry
from app.market.dto import PriceTick
from app.engine.actions import StopLossHit, RiskFreed


class StopLossRule:
    async def apply(
        self,
        tracking: Tracking,
        tick: PriceTick,
        first_entry: SignalEntry | None,
        second_entry: SignalEntry | None,
    ) -> list[StopLossHit | RiskFreed]:
        # Position not entered yet
        if not tracking.has_entered:
            return []

        # Already finished
        if not tracking.is_active:
            return []

        signal = tracking.signal
        current_price = tick.price
        
        # Use current_stop_loss (can be moved), not original signal.stop_loss
        stop_loss = tracking.current_stop_loss

        # Check if stop loss is hit
        if signal.direction == Direction.LONG:
            hit = current_price <= stop_loss
        else:
            hit = current_price >= stop_loss

        if not hit:
            return []

        # Stop loss hit - check if it's risk-free
        # Conditions: halfway to TP1 reached, TP1 never hit
        if tracking.halfway_to_tp1_reached and tracking.highest_target_hit == 0:
            return [
                RiskFreed(
                    price=current_price,
                    timestamp=tick.timestamp,
                )
            ]

        # Normal stop loss
        return [
            StopLossHit(
                price=current_price,
                timestamp=tick.timestamp,
            )
        ]
