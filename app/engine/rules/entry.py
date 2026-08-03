from datetime import timedelta
from decimal import Decimal

from app.database.enums import Direction, EntryMethod
from app.database.models import Tracking, SignalEntry, SignalTarget
from app.market.dto import PriceTick
from app.engine.actions import PositionEntered, WaitingEntryExpired, EntryType


class EntryRule:
    """
    Entry detection rule implementing the entry state machine.
    
    Business Rules:
    - EntryHigh is the initial entry (higher price for LONG, lower for SHORT)
    - EntryLow is the averaging entry (lower price for LONG, higher for SHORT)
    - EntryLow only valid before any TP hit (highest_target_hit == 0)
    - EntryLow can fire after Entry1 OR Emergency Entry
    - Emergency Entry available after 5min timeout if EntryHigh not touched
    - Gap behavior: If both entries crossed in one tick, both fire
    
    State transitions:
    
    WAITING_ENTRY
        │
        ├─→ EntryHigh hit → TRACKING (ENTRY_1)
        │       └─→ EntryLow allowed (if highest_target_hit == 0)
        │
        ├─→ 5min timeout + EntryHigh not touched → Emergency mode
        │       └─→ Emergency price hit → TRACKING (EMERGENCY_ENTRY)
        │               └─→ EntryLow allowed (if highest_target_hit == 0)
        │
        └─→ TP1 crossed before entry → CANCELLED (signal missed)
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
        
        Business Rule: If price crosses both EntryHigh and EntryLow in single tick,
        both entries must be detected and returned as separate actions.
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
        # Check 2: Gap scenario - both entries crossed in single tick
        # ============================================================
        second_entry = self._get_second_entry(signal)
        
        if first_entry and second_entry:
            entry1_hit = self._entry_hit(direction, current_price, first_entry.price)
            entry2_hit = self._entry_hit(direction, current_price, second_entry.price)
            
            if entry1_hit and entry2_hit:
                # Gap detected - return both actions in order
                return [
                    PositionEntered(
                        entry_type=EntryType.ENTRY_1,
                        price=first_entry.price,
                        timestamp=current_time,
                    ),
                    PositionEntered(
                        entry_type=EntryType.ENTRY_2,
                        price=second_entry.price,
                        timestamp=current_time,
                    ),
                ]
        
        # ============================================================
        # Check 3: Entry1 only
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
        # Check 4: Emergency entry conditions
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
        """Handle entry2 (EntryLow) detection after position is active.
        
        Business Rule: EntryLow is only valid during entry phase.
        EntryLow becomes permanently disabled once any TP has been hit.
        
        EntryLow can trigger after ENTRY_1 OR EMERGENCY_ENTRY.
        Entry method does NOT determine whether EntryLow is allowed.
        """
        
        # Entry phase ended? (any TP hit)
        if tracking.highest_target_hit > 0:
            return []
        
        # Must have entered already
        if not tracking.has_entered:
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


    def _get_second_entry(self, signal) -> SignalEntry | None:
        """Get second entry (EntryLow) based on direction.
        
        LONG: EntryLow is the lower price
        SHORT: EntryLow is the higher price
        """
        if not signal.entries or len(signal.entries) < 2:
            return None
        
        sorted_entries = sorted(signal.entries, key=lambda e: e.price)
        
        if signal.direction == Direction.LONG:
            # LONG: lower price is EntryLow
            return sorted_entries[0]
        else:
            # SHORT: higher price is EntryLow
            return sorted_entries[-1]
