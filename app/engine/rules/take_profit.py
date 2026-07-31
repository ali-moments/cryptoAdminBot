from app.database.enums import Direction
from app.database.models import Tracking, SignalEntry
from app.market.dto import PriceTick
from app.engine.actions import TakeProfitHit, TrackingCompleted


class TakeProfitRule:
    async def apply(
        self,
        tracking: Tracking,
        tick: PriceTick,
        first_entry: SignalEntry | None,
        second_entry: SignalEntry | None,
    ) -> list[TakeProfitHit | TrackingCompleted]:
        # Position not entered yet
        if not tracking.has_entered:
            return []

        signal = tracking.signal
        current_price = tick.price
        direction = signal.direction

        targets = sorted(
            signal.targets,
            key=lambda t: t.position,
        )

        actions: list[TakeProfitHit | TrackingCompleted] = []

        for target in targets:
            # Already processed
            if target.position <= tracking.highest_target_hit:
                continue

            # Use current_tp1_price if TP1 was recalculated (entry2 was hit)
            target_price = target.price
            if target.position == 1 and tracking.current_tp1_price is not None:
                target_price = tracking.current_tp1_price

            if direction == Direction.LONG:
                hit = current_price >= target_price
            else:
                hit = current_price <= target_price

            if not hit:
                break

            actions.append(
                TakeProfitHit(
                    target_number=target.position,
                    price=target_price,
                    timestamp=tick.timestamp,
                )
            )

        # Check if all targets are now hit
        if actions:
            new_highest = tracking.highest_target_hit + len(actions)
            total_targets = len(signal.targets)
            
            if new_highest >= total_targets:
                actions.append(
                    TrackingCompleted(
                        timestamp=tick.timestamp,
                    )
                )

        return actions
