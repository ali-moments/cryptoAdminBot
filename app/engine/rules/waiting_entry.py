from datetime import timedelta
from decimal import Decimal

from app.database.models import Tracking, SignalEntry
from app.market.dto import PriceTick
from app.engine.actions import WaitingEntryExpired


class WaitingEntryRule:
    WAITING_TIMEOUT = timedelta(hours=2)
    ENTRY_EXTENSION_RATIO = Decimal("0.25")

    async def apply(
        self,
        tracking: Tracking,
        tick: PriceTick,
        first_entry: SignalEntry | None,
        second_entry: SignalEntry | None,
    ) -> list[WaitingEntryExpired]:
        # Already entered
        if tracking.has_entered:
            return []

        signal = tracking.signal

        # Expired
        if tick.timestamp >= signal.created_at + self.WAITING_TIMEOUT:
            return [
                WaitingEntryExpired(
                    reason="timeout",
                    timestamp=tick.timestamp,
                )
            ]

        # Optional:
        # After one hour we can relax the first entry
        # to increase the chance of filling.
        #
        # We'll implement this later because it requires
        # persisting the adjusted entry or calculating it
        # dynamically every tick.
        #
        # if tick.timestamp >= signal.created_at + timedelta(hours=1):
        #     ...

        return []
