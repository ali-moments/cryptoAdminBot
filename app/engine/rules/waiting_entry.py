from datetime import timedelta
from decimal import Decimal

from app.config.settings import settings
from app.database.models import Tracking, SignalEntry
from app.market.dto import PriceTick
from app.engine.actions import WaitingEntryExpired


class WaitingEntryRule:
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

        # Expired - check against settings.signal_entry_timeout (in hours)
        waiting_timeout = timedelta(hours=settings.signal_entry_timeout)
        if tick.timestamp >= signal.created_at + waiting_timeout:
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
