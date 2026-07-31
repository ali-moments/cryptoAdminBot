from datetime import timedelta
from decimal import Decimal

from app.database.enums import Direction, EntryMethod
from app.database.models import Tracking, SignalEntry, SignalTarget
from app.market.dto import PriceTick
from app.engine.actions import PositionEntered, WaitingEntryExpired, EntryType


class EntryRule:
    """
    Entry detection rule implementing the entry state machine.
    
    State transitions:
    
    WAITING_ENTRY
        │
        ├─→ Entry1 hit → TRACKING (ENTRY_1)
        │       └─→ Entry2 allowed
        │
        ├─→ 5min timeout + TP1 not crossed → Emergency entry eligible
        │       └─→ Emergency price hit → TRACKING (EMERGENCY_ENTRY)
        │               └─→ Entry2 NOT allowed
        │
        └─→ TP1 crossed → CANCELLED (signal missed)
    """
    
    # Emergency entry timeout
    EMERGENCY_ENTRY_TIMEOUT = timedelta(minutes=5)
    
    async def apply(
        self,
        tracking: Tracking,
        tick: PriceTick,
        first_entry: SignalEntry | None,
        second_entry: SignalEntry | None,
    ) -> list[PositionEntered | WaitingEntryExpired]:
        signal = tracking.signal
        current_price = tick.price
        current_time = tick.timestamp
        
        # ============================================================
        # State: WAITING_ENTRY (no position yet)
        # ============================================================
        if not tracking.has_entered:
            return await self._handle_waiting_entry(
                tracking=tracking,
                current_price=current_price,
                current_time=current_time,
                first_entry=first_entry,
            )
        
        # ============================================================
        # State: TRACKING (position active)
        # ============================================================
        # Check if entry2 is allowed and hit
        return await self._handle_entry2(
            tracking=tracking,
            current_price=current_price,
            current_time=current_time,
            second_entry=second_entry,
        )
    
    async def _handle_waiting_entry(
        self,
        tracking: Tracking,
        current_price: Decimal,
        current_time,
        first_entry: SignalEntry | None,
    ) -> list[PositionEntered | WaitingEntryExpired]:
        """Handle entry detection while in WAITING_ENTRY state.
        
        This method is pure - it inspects state and returns actions.
        It never mutates the tracking object.
        """
        signal = tracking.signal
        direction = signal.direction
        
        # Get TP1 for crossing check
        tp1 = signal.targets[0] if signal.targets else None
        
        # Check if emergency mode is active (timeout elapsed and entry1 not touched)
        timeout_elapsed = (current_time - tracking.started_at) >= self.EMERGENCY_ENTRY_TIMEOUT
        emergency_mode_active = timeout_elapsed and not tracking.entry1_touched
        
        # ============================================================
        # Check 1: TP1 crossed before entry? → Signal missed
        # BUT: Skip this check if emergency mode is active
        # ============================================================
        if tp1 and not emergency_mode_active:
            if self._tp_crossed(direction, current_price, tp1.price):
                return [
                    WaitingEntryExpired(
                        reason="tp1_crossed",
                        timestamp=current_time,
                    )
                ]
        
        # ============================================================
        # Check 2: Entry1 hit?
        # ============================================================
        if first_entry and self._entry_hit(direction, current_price, first_entry.price):
            return [
                PositionEntered(
                    entry_type=EntryType.ENTRY_1,
                    price=first_entry.price,
                    timestamp=current_time,
                )
            ]
        
        # ============================================================
        # Check 3: Emergency entry conditions
        # ============================================================
        if emergency_mode_active:
            # Calculate emergency entry price on-demand (deterministic)
            # Emergency entry always uses EntryHigh (the higher absolute price)
            # regardless of direction
            emergency_price = self._calculate_emergency_entry_price(
                direction=direction,
                signal=signal,
                tp1_price=tp1.price if tp1 else None,
            )
            
            if emergency_price is None:
                # Cannot calculate emergency entry (missing data)
                return []
            
            # Check if emergency entry price is hit
            # Emergency entry logic is DIFFERENT from normal entry:
            # - For LONG: emergency is ABOVE tp1, enter when price >= emergency
            # - For SHORT: emergency is BELOW tp1, enter when price <= emergency
            if self._tp_crossed(direction, current_price, emergency_price):
                return [
                    PositionEntered(
                        entry_type=EntryType.EMERGENCY_ENTRY,
                        price=emergency_price,
                        timestamp=current_time,
                    )
                ]
        
        return []
    
    async def _handle_entry2(
        self,
        tracking: Tracking,
        current_price: Decimal,
        current_time,
        second_entry: SignalEntry | None,
    ) -> list[PositionEntered]:
        """Handle entry2 detection after position is active."""
        
        # Entry2 only allowed after ENTRY_1 (not after EMERGENCY_ENTRY)
        if tracking.entry_method != EntryMethod.ENTRY_1:
            return []
        
        # Entry2 already touched?
        if tracking.entry2_touched:
            return []
        
        # No second entry defined?
        if not second_entry:
            return []
        
        # Check if entry2 is hit
        signal = tracking.signal
        direction = signal.direction
        
        if self._entry_hit(direction, current_price, second_entry.price):
            return [
                PositionEntered(
                    entry_type=EntryType.ENTRY_2,
                    price=second_entry.price,
                    timestamp=current_time,
                )
            ]
        
        return []
    
    def _entry_hit(
        self,
        direction: Direction,
        current_price: Decimal,
        entry_price: Decimal,
    ) -> bool:
        """Check if entry price is hit based on direction."""
        if direction == Direction.LONG:
            # For LONG: enter when price comes down to or below entry
            return current_price <= entry_price
        
        # For SHORT: enter when price comes up to or above entry
        return current_price >= entry_price
    
    def _tp_crossed(
        self,
        direction: Direction,
        current_price: Decimal,
        tp_price: Decimal,
    ) -> bool:
        """Check if take profit level is crossed."""
        if direction == Direction.LONG:
            # For LONG: TP crossed when price goes up to or above TP
            return current_price >= tp_price
        
        # For SHORT: TP crossed when price goes down to or below TP
        return current_price <= tp_price
    
    def _calculate_emergency_entry_price(
        self,
        direction: Direction,
        signal,
        tp1_price: Decimal | None,
    ) -> Decimal | None:
        """
        Calculate emergency entry price.
        
        Formula (both LONG and SHORT):
        - Emergency = TP1 + (TP1 - EntryHigh) / 4 for LONG
        - Emergency = TP1 - (EntryHigh - TP1) / 4 for SHORT
        
        EntryHigh is always the higher absolute price, regardless of direction.
        
        This places emergency entry BEYOND TP1 (worse entry, but catches late signals).
        """
        if not signal.entries or tp1_price is None:
            return None
        
        # EntryHigh is always the higher price
        sorted_entries = sorted(signal.entries, key=lambda e: e.price)
        entry_high_price = sorted_entries[-1].price
        
        distance = abs(tp1_price - entry_high_price)
        quarter_distance = distance / Decimal("4")
        
        if direction == Direction.LONG:
            # Emergency entry above TP1
            return tp1_price + quarter_distance
        else:
            # Emergency entry below TP1
            return tp1_price - quarter_distance

