from decimal import Decimal
from loguru import logger
from app.database.models import Tracking, Signal, SignalEntry
from app.database.enums import Direction
from app.market.dto import PriceTick

from app.engine.rules.entry import EntryRule
from app.engine.rules.waiting_entry import WaitingEntryRule
from app.engine.rules.stop_loss import StopLossRule
from app.engine.rules.take_profit import TakeProfitRule
from app.engine.rules.expiry import ExpiryRule
from app.engine.actions import (
    WaitingEntryExpired,
    PositionEntered,
    StopLossHit,
    RiskFreed,
    TrackingCompleted,
    SignalExpired,
)


class Tracker:
    def __init__(self) -> None:
        self._waiting_entry = WaitingEntryRule()
        self._entry = EntryRule()
        self._stop_loss = StopLossRule()
        self._take_profit = TakeProfitRule()
        self._expiry = ExpiryRule()

    async def track(
        self,
        tracking: Tracking,
        tick: PriceTick,
    ) -> list:
        """Execute rules in correct order with explicit control flow."""

        logger.debug(f"TRACKER_START: {tracking.signal.symbol} (tracking_id={tracking.id}) - Starting rule execution")

        # Update tracking state BEFORE rules execute
        self._update_tracking_state(tracking, tick.price)

        # Get ordered entries
        first_entry, second_entry = self._get_ordered_entries(tracking.signal)

        actions = []

        # 0. Signal expiry check (72-hour limit) - applies to all active signals
        # logger.trace(f"RULE_CHECK: Expiry rule for {tracking.signal.symbol} (tracking_id={tracking.id})")
        expiry_actions = await self._expiry.apply(
            tracking, tick, first_entry, second_entry
        )
        if expiry_actions:
            logger.info(f"RULE_HIT: Expiry rule generated {len(expiry_actions)} actions for {tracking.signal.symbol}")
            actions.extend(expiry_actions)
            # Signal expired - no further rules apply
            return actions

        # 1. Waiting entry timeout (only if not entered)
        if not tracking.has_entered:
            logger.trace(f"RULE_CHECK: Waiting entry rule for {tracking.signal.symbol} (tracking_id={tracking.id})")
            waiting_actions = await self._waiting_entry.apply(
                tracking, tick, first_entry, second_entry
            )
            if waiting_actions:
                logger.info(f"RULE_HIT: Waiting entry rule generated {len(waiting_actions)} actions for {tracking.signal.symbol}")
                actions.extend(waiting_actions)
                # Expired - no further rules apply
                return actions

        # 2. Entry check
        # logger.trace(f"RULE_CHECK: Entry rule for {tracking.signal.symbol} (tracking_id={tracking.id})")
        entry_actions = await self._entry.apply(
            tracking, tick, first_entry, second_entry
        )

        # Track if this is the first entry (before processing actions)
        was_first_entry = not tracking.has_entered

        if entry_actions:
            logger.info(f"RULE_HIT: Entry rule generated {len(entry_actions)} actions for {tracking.signal.symbol}")
            actions.extend(entry_actions)
            # If just entered for the first time, don't check SL/TP on same tick
            # This prevents SL/TP check on the entry tick itself
            # But DO allow gap scenario (multiple entry actions) to be processed
            if was_first_entry:
                logger.debug(f"FIRST_ENTRY: Skipping SL/TP rules this tick for {tracking.signal.symbol}")
                return actions

        # 3. Stop loss check (only if entered)
        if tracking.has_entered:
            # logger.trace(f"RULE_CHECK: Stop loss rule for {tracking.signal.symbol} (tracking_id={tracking.id})")
            sl_actions = await self._stop_loss.apply(
                tracking, tick, first_entry, second_entry
            )
            if sl_actions:
                logger.info(f"RULE_HIT: Stop loss rule generated {len(sl_actions)} actions for {tracking.signal.symbol}")
                actions.extend(sl_actions)
                # SL or RiskFreed hit - stop
                return actions

        # 4. Take profit check (only if entered and SL not hit)
        if tracking.has_entered:
            # logger.trace(f"RULE_CHECK: Take profit rule for {tracking.signal.symbol} (tracking_id={tracking.id})")
            tp_actions = await self._take_profit.apply(
                tracking, tick, first_entry, second_entry
            )
            if tp_actions:
                logger.info(f"RULE_HIT: Take profit rule generated {len(tp_actions)} actions for {tracking.signal.symbol}")
                actions.extend(tp_actions)
                # Check if TrackingCompleted is in actions
                if any(isinstance(a, TrackingCompleted) for a in tp_actions):
                    # Completed - stop
                    return actions

        # logger.trace(f"TRACKER_END: {tracking.signal.symbol} (tracking_id={tracking.id}) - {len(actions)} total actions")
        return actions

    def _get_ordered_entries(
        self,
        signal: Signal,
    ) -> tuple[SignalEntry | None, SignalEntry | None]:
        """Calculate first and second entry based on direction.

        LONG: higher price = first entry, lower price = second entry
        SHORT: lower price = first entry, higher price = second entry
        """
        if not signal.entries:
            return None, None

        if len(signal.entries) == 1:
            return signal.entries[0], None

        # Sort by price
        sorted_entries = sorted(signal.entries, key=lambda e: e.price)
        low = sorted_entries[0]
        high = sorted_entries[-1]

        if signal.direction == Direction.LONG:
            # LONG: higher first, lower second
            return high, low
        else:
            # SHORT: lower first, higher second
            return low, high

    def _update_tracking_state(
        self,
        tracking: Tracking,
        current_price: Decimal,
    ) -> None:
        """Update peak price and halfway flag before rules execute.

        This runs on EVERY tick, ensuring state is current.
        """
        if not tracking.has_entered:
            return

        signal = tracking.signal
        direction = signal.direction

        # Initialize peak price on first update after entry
        if tracking.peak_price_after_entry is None:
            tracking.peak_price_after_entry = current_price

        # Update peak price
        if direction == Direction.LONG:
            tracking.peak_price_after_entry = max(
                tracking.peak_price_after_entry,
                current_price
            )
        else:
            tracking.peak_price_after_entry = min(
                tracking.peak_price_after_entry,
                current_price
            )

        # Check halfway to TP1 (only if not already flagged)
        if tracking.halfway_to_tp1_reached:
            return

        if not signal.targets:
            return

        # Use current_tp1_price if TP1 was recalculated, otherwise use original TP1
        tp1_price = tracking.current_tp1_price if tracking.current_tp1_price else signal.targets[0].price
        entry = tracking.actual_entry_price

        if entry is None:
            return

        if direction == Direction.LONG:
            halfway = entry + (tp1_price - entry) / 2
            if current_price >= halfway:
                tracking.halfway_to_tp1_reached = True
        else:
            halfway = entry - (entry - tp1_price) / 2
            if current_price <= halfway:
                tracking.halfway_to_tp1_reached = True
