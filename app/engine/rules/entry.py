from app.database.enums import Direction
from app.database.models import Tracking, SignalEntry
from app.market.dto import PriceTick
from app.engine.actions import PositionEntered


class EntryRule:
    async def apply(
        self,
        tracking: Tracking,
        tick: PriceTick,
        first_entry: SignalEntry | None,
        second_entry: SignalEntry | None,
    ) -> list[PositionEntered]:
        # Already entered
        if tracking.has_entered:
            return []

        signal = tracking.signal
        current_price = tick.price

        # Check first entry
        if first_entry and self._entry_hit(signal.direction, current_price, first_entry.price):
            return [
                PositionEntered(
                    entry_number=1,
                    price=first_entry.price,
                    timestamp=tick.timestamp,
                )
            ]

        # Check second entry
        if second_entry and self._entry_hit(signal.direction, current_price, second_entry.price):
            return [
                PositionEntered(
                    entry_number=2,
                    price=second_entry.price,
                    timestamp=tick.timestamp,
                )
            ]

        return []

    def _entry_hit(
        self,
        direction: Direction,
        current_price,
        entry_price,
    ) -> bool:
        if direction == Direction.LONG:
            return current_price <= entry_price

        return current_price >= entry_price
