from app.database.models import Tracking, SignalEntry
from app.market.dto import PriceTick
from app.engine.actions import SignalExpired


class ExpiryRule:
    """Enforces 72-hour signal expiration rule.
    
    This rule checks if a signal has exceeded its expires_at timestamp
    and emits SignalExpired action for active trackings that need to be closed.
    
    Applied to all active trackings regardless of status to ensure
    no signal continues beyond its 72-hour lifetime.
    """

    async def apply(
        self,
        tracking: Tracking,
        tick: PriceTick,
        first_entry: SignalEntry | None,
        second_entry: SignalEntry | None,
    ) -> list[SignalExpired]:
        signal = tracking.signal
        
        # Check if signal has expired based on expires_at timestamp
        if tick.timestamp >= signal.expires_at:
            return [
                SignalExpired(
                    reason="72_hour_limit",
                    timestamp=tick.timestamp,
                    expires_at=signal.expires_at,
                )
            ]
        
        return []