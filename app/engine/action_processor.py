from decimal import Decimal
from loguru import logger
from typing import TYPE_CHECKING

from app.database.enums import TrackingStatus, AuditEventType, Direction, EntryMethod, SignalStatus, CloseReason
from app.database.models import Tracking
from app.database.uow import UnitOfWork
from app.engine.actions import (
    PositionEntered,
    WaitingEntryExpired,
    StopLossHit,
    TakeProfitHit,
    RiskFreed,
    TrackingCompleted,
    SignalExpired,
    EntryType,
)

if TYPE_CHECKING:
    from app.services.telegram import TelegramService


class ActionProcessor:
    """Executes side effects for tracking actions.

    Responsibilities:
    - Process Tracker actions
    - Apply tracking state changes
    - Create internal tracking records (TpHit)
    - Write AuditLog entries
    - Emit structured logs

    Transaction lifecycle is owned by TrackingManager.
    ActionProcessor uses the current transaction context.
    """

    def __init__(self, telegram_service: "TelegramService") -> None:
        self._telegram = telegram_service

    async def process(
        self,
        tracking: Tracking,
        actions: list,
        uow: UnitOfWork,
    ) -> None:
        """Process actions with idempotency checks.

        Args:
            tracking: Tracking object attached to an active session
            actions: List of actions to process
            uow: UnitOfWork instance to use for database operations
        """
        if not actions:
            return

        # Log actions to be processed
        action_names = [action.__class__.__name__ for action in actions]
        logger.info(f"ACTION_PROCESSING_START: {tracking.signal.symbol} (tracking_id={tracking.id}) - {len(actions)} actions: {', '.join(action_names)}")

        for action in actions:
            # Check if already processed
            if await self._is_already_processed(tracking, action, uow):
                logger.debug(
                    f"ACTION_SKIP: Already processed - tracking={tracking.id} action={action.__class__.__name__}",
                )
                continue

            # Log individual action processing
            logger.info(f"ACTION_PROCESS: {action.__class__.__name__} for {tracking.signal.symbol} (tracking_id={tracking.id})")

            # Route to handler
            match action:
                case PositionEntered():
                    await self._position_entered(tracking, action, uow)
                case WaitingEntryExpired():
                    await self._waiting_entry_expired(tracking, action, uow)
                case SignalExpired():
                    await self._signal_expired(tracking, action, uow)
                case StopLossHit():
                    await self._stop_loss_hit(tracking, action, uow)
                case TakeProfitHit():
                    await self._take_profit_hit(tracking, action, uow)
                case RiskFreed():
                    await self._risk_freed(tracking, action, uow)
                case TrackingCompleted():
                    await self._tracking_completed(tracking, action, uow)

    async def _is_already_processed(
        self,
        tracking: Tracking,
        action,
        uow: UnitOfWork,
    ) -> bool:
        """Check if action was already processed using existing state."""
        match action:
            case PositionEntered():
                if action.entry_type == EntryType.ENTRY_1:
                    return tracking.entry1_touched

                if action.entry_type == EntryType.ENTRY_2:
                    return tracking.entry2_touched

                if action.entry_type == EntryType.EMERGENCY_ENTRY:
                    return tracking.entry_method == EntryMethod.EMERGENCY_ENTRY

                return False

            case TakeProfitHit():
                # Query database for existing TpHit record
                # Database uniqueness constraint is the source of truth
                existing = await uow.tp_hits.by_tracking(tracking.id)
                return any(
                    tp.position == action.target_number
                    for tp in existing
                )

            case StopLossHit() | RiskFreed() | WaitingEntryExpired() | SignalExpired() | TrackingCompleted():
                # Check if already closed
                return not tracking.is_active

        return False

    async def _position_entered(
        self,
        tracking: Tracking,
        action: PositionEntered,
        uow: UnitOfWork,
    ) -> None:
        """Handle position entry.

        Entry types:
        - ENTRY_1: Initial entry at entry1 price
        - ENTRY_2: Scaling entry at entry2 price (triggers TP1 recalculation)
        - EMERGENCY_ENTRY: Fallback entry after timeout
        """
        signal = tracking.signal

        if action.entry_type == EntryType.ENTRY_1:
            # ============================================================
            # ENTRY_1: Initial position entry
            # ============================================================
            tracking.entry1_touched = True

            # Only set entry_method and actual_entry_price if not already set by emergency entry
            # This preserves emergency entry state if entry1 is touched after emergency
            if tracking.entry_method != EntryMethod.EMERGENCY_ENTRY:
                tracking.entry_method = EntryMethod.ENTRY_1
                tracking.actual_entry_price = action.price
                tracking.status = TrackingStatus.TRACKING
                # Initialize peak price tracking
                tracking.peak_price_after_entry = action.price

            # Write audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.ENTRY1_HIT,
                payload={
                    "entry_type": "ENTRY_1",
                    "price": str(action.price),
                    "timestamp": action.timestamp.isoformat(),
                },
            )

            logger.info(f"✓ ENTRY1 HIT: {signal.symbol} @ {action.price} (tracking_id={tracking.id})")

            # # Send Telegram notification
            # await self._telegram.send_entry_hit(
            #     tracking=tracking,
            #     entry_type=1,
            #     entry_price=str(action.price),
            #     uow=uow
            # )

        elif action.entry_type == EntryType.ENTRY_2:
            # ============================================================
            # ENTRY_2: Scaling entry (DCA)
            # Note: entry_method is NOT modified here - preserves EMERGENCY_ENTRY if set
            # ============================================================
            tracking.entry2_touched = True

            # Recalculate TP1
            # OLD Formula: new_tp1 = FirstEntry + (original_tp1 - FirstEntry) / 2
            # NEW Formula: (20/leverage)% from first entry price
            # FirstEntry is direction-dependent:
            #   - LONG: higher price (market approaches from above)
            #   - SHORT: lower price (market approaches from below)
            if signal.targets and len(signal.entries) >= 2:
                # Determine FirstEntry based on direction
                sorted_entries = sorted(signal.entries, key=lambda e: e.price)

                if signal.direction == Direction.LONG:
                    # LONG: first entry is higher price
                    first_entry_price = sorted_entries[-1].price
                else:
                    # SHORT: first entry is lower price
                    first_entry_price = sorted_entries[0].price

                original_tp1 = signal.targets[0].price

                # OLD FORMULA (commented out):
                # Calculate new TP1 (halfway between FirstEntry and original TP1)
                # new_tp1 = first_entry_price + (original_tp1 - first_entry_price) / Decimal("2")
                
                # NEW FORMULA: Calculate new TP1 at (20/leverage)% from first entry price
                target_percentage = Decimal(20) / Decimal(signal.leverage)  # e.g., 20/20 = 1%
                target_distance = first_entry_price * target_percentage / Decimal(100)  # Convert % to actual price distance
                
                if signal.direction == Direction.LONG:
                    new_tp1 = first_entry_price + target_distance
                else:
                    new_tp1 = first_entry_price - target_distance
                    
                tracking.current_tp1_price = new_tp1

                # Write TP1 recalculation audit log
                await uow.audit_logs.create(
                    tracking_id=tracking.id,
                    signal_id=tracking.signal_id,
                    event=AuditEventType.TP1_RECALCULATED,
                    payload={
                        "original_tp1": str(original_tp1),
                        "new_tp1": str(new_tp1),
                        "first_entry": str(first_entry_price),
                        "entry_low_touched": str(action.price),
                        "timestamp": action.timestamp.isoformat(),
                    },
                )

                logger.info(f"✓ TP1 RECALCULATED: {signal.symbol} {original_tp1} → {new_tp1} (tracking_id={tracking.id})")

            # Write entry2 audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.ENTRY2_HIT,
                payload={
                    "entry_type": "ENTRY_2",
                    "price": str(action.price),
                    "timestamp": action.timestamp.isoformat(),
                },
            )

            logger.info(f"✓ ENTRY2 HIT: {signal.symbol} @ {action.price} (tracking_id={tracking.id})")

            # Send Telegram notification
            await self._telegram.send_entry_hit(
                tracking=tracking,
                entry_type=2,
                entry_price=str(action.price),
                uow=uow,
                target=new_tp1,
            )

        elif action.entry_type == EntryType.EMERGENCY_ENTRY:
            # ============================================================
            # EMERGENCY_ENTRY: Fallback entry after timeout
            # Business Rule: Emergency Entry sets entry1_touched = True
            # entry1_touched means "initial entry state reached", not
            # necessarily that EntryHigh price was physically touched.
            # ============================================================
            tracking.entry1_touched = True  # Mark as entered via emergency
            tracking.entry_method = EntryMethod.EMERGENCY_ENTRY
            tracking.actual_entry_price = action.price
            tracking.emergency_entry_triggered_at = action.timestamp
            tracking.status = TrackingStatus.TRACKING

            # Initialize peak price tracking
            tracking.peak_price_after_entry = action.price

            # Calculate emergency entry price for audit log (debugging)
            # This is the same deterministic calculation used by EntryRule
            calculated_emergency_price = self._calculate_emergency_entry_price_for_audit(
                signal=signal,
            )

            # Write audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.EMERGENCY_ENTRY_HIT,
                payload={
                    "entry_type": "EMERGENCY_ENTRY",
                    "price": str(action.price),
                    "calculated_emergency_price": str(calculated_emergency_price) if calculated_emergency_price else None,
                    "timestamp": action.timestamp.isoformat(),
                },
            )

            logger.info(f"✓ EMERGENCY ENTRY: {signal.symbol} @ {action.price} (tracking_id={tracking.id})")

            # # Send Telegram notification for emergency entry (treated as entry1)
            # await self._telegram.send_entry_hit(
            #     tracking=tracking,
            #     entry_type=1,
            #     entry_price=str(action.price),
            #     uow=uow
            # )

    async def _waiting_entry_expired(
        self,
        tracking: Tracking,
        action: WaitingEntryExpired,
        uow: UnitOfWork,
    ) -> None:
        """Handle waiting entry expiration.

        Reasons:
        - timeout: Signal expired after configured timeout
        - tp1_crossed: TP1 reached before entry (signal opportunity missed)
        """
        # Update tracking state
        tracking.status = TrackingStatus.CANCELLED
        tracking.is_active = False
        tracking.closed_at = action.timestamp
        tracking.close_reason = CloseReason.CANCELLED

        # For TP1 crossed scenarios, calculate the profit that was available
        if action.reason == "tp1_crossed" and tracking.signal.entries and tracking.signal.targets:
            entry1_price = tracking.signal.entries[0].price
            tp1_price = tracking.signal.targets[0].price
            
            # Calculate profit percentage from entry1 to TP1
            tp1_profit_pct = self._calculate_profit_percentage(
                tracking.signal.direction,
                entry1_price,
                tp1_price,
            )
            
            tracking.profit_percent = tp1_profit_pct
            tracking.final_price = tp1_price

        # Write audit log
        await uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_EXPIRED,
            payload={
                "reason": action.reason,
                "profit_percent": str(tracking.profit_percent) if tracking.profit_percent else None,
                "final_price": str(tracking.final_price) if tracking.final_price else None,
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        profit_msg = f" (+{tracking.profit_percent:.2f}% available)" if tracking.profit_percent else ""
        logger.info(f"✓ WAITING ENTRY EXPIRED: {tracking.signal.symbol} - {action.reason}{profit_msg} (tracking_id={tracking.id})")

        # # Send Telegram notification
        await self._telegram.send_signal_cancelled(tracking, action.reason, uow)

        # TODO: Update statistics

    async def _signal_expired(
        self,
        tracking: Tracking,
        action: SignalExpired,
        uow: UnitOfWork,
    ) -> None:
        """Handle signal expiration due to 72-hour limit.

        This is different from waiting entry expiration. Signal expiration
        can occur on any active tracking regardless of status when the
        72-hour lifetime is exceeded.
        """
        # Update tracking state
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp

        # Update signal status
        tracking.signal.status = SignalStatus.EXPIRED

        # Write audit log
        await uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_EXPIRED,
            payload={
                "reason": action.reason,
                "timestamp": action.timestamp.isoformat(),
                "expires_at": action.expires_at.isoformat(),
            },
        )

        # Log event
        logger.info(f"✓ SIGNAL EXPIRED: {tracking.signal.symbol} - {action.reason} at {action.expires_at} (tracking_id={tracking.id})")

        # Send Telegram notification
        #await self._telegram.send_signal_closed(tracking, "expired", uow)

        # TODO: Update statistics (increment expired_signals counter)

    async def _stop_loss_hit(
        self,
        tracking: Tracking,
        action: StopLossHit,
        uow: UnitOfWork,
    ) -> None:
        """Handle stop loss hit."""
        # Update tracking state
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp
        tracking.close_reason = CloseReason.ORIGINAL_STOP_LOSS
        tracking.final_price = action.price

        # Calculate loss percentage for persistence and notification using effective entry price (average if both entries touched)
        effective_entry = self._get_effective_entry_price(tracking)
        if effective_entry:
            loss_pct = abs(self._calculate_profit_percentage(
                tracking.signal.direction,
                effective_entry,
                action.price,
            ))
            # Store as negative percentage for losses
            tracking.profit_percent = -loss_pct
            
            # Apply leverage to the loss percentage for display
            leveraged_loss_pct = loss_pct * tracking.signal.leverage
            await self._telegram.send_sl_hit(tracking, f"{leveraged_loss_pct:.2f}", uow)

        # Write audit log
        await uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_CLOSED,
            payload={
                "reason": "stop_loss",
                "price": str(action.price),
                "profit_percent": str(tracking.profit_percent) if tracking.profit_percent else None,
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(f"✓ STOP LOSS HIT: {tracking.signal.symbol} @ {action.price} ({tracking.profit_percent:.2f}%) (tracking_id={tracking.id})")

        # TODO: Update statistics (loss)

    async def _take_profit_hit(
        self,
        tracking: Tracking,
        action: TakeProfitHit,
        uow: UnitOfWork,
    ) -> None:
        """Handle take profit hit."""
        # Calculate profit percentage using effective entry price (average if both entries touched)
        effective_entry = self._get_effective_entry_price(tracking)
        if effective_entry:
            profit_pct = self._calculate_profit_percentage(
                tracking.signal.direction,
                effective_entry,
                action.price,
            )
        else:
            profit_pct = Decimal("0")

        # Create TpHit record (unique constraint prevents duplicates)
        tp_hit = await uow.tp_hits.create(
            tracking_id=tracking.id,
            position=action.target_number,
            price=action.price,
            profit_percent=profit_pct,
            hit_at=action.timestamp,
        )

        # Update tracking state
        if action.target_number > tracking.highest_target_hit:
            tracking.highest_target_hit = action.target_number

        # Write audit log
        await uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.TARGET_HIT,
            payload={
                "target_number": action.target_number,
                "price": str(action.price),
                "profit_percent": str(profit_pct),
                "effective_entry": str(effective_entry) if effective_entry else None,
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(f"✓ TP{action.target_number} HIT: {tracking.signal.symbol} @ {action.price} (+{profit_pct:.2f}%) (tracking_id={tracking.id})")

        # Send Telegram notification using the just-created TpHit record
        await self._telegram.send_tp_hit(tracking, tp_hit, uow)

    async def _risk_freed(
        self,
        tracking: Tracking,
        action: RiskFreed,
        uow: UnitOfWork,
    ) -> None:
        """Handle risk free event."""
        # Update tracking state
        tracking.status = TrackingStatus.RISK_FREE
        tracking.is_active = False
        tracking.closed_at = action.timestamp
        tracking.close_reason = CloseReason.EXPIRED
        tracking.final_price = action.price

        # Write audit log
        await uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_CLOSED,
            payload={
                "reason": "risk_free",
                "price": str(action.price),
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(f"✓ RISK FREED: {tracking.signal.symbol} @ {action.price} (tracking_id={tracking.id})")

        # Send Telegram notification
        # await self._telegram.send_signal_closed(tracking, "risk_free", uow)

        # TODO: Update statistics (risk free)

    async def _tracking_completed(
        self,
        tracking: Tracking,
        action: TrackingCompleted,
        uow: UnitOfWork,
    ) -> None:
        """Handle tracking completion."""
        # Update tracking state
        tracking.status = TrackingStatus.CLOSED
        tracking.is_active = False
        tracking.closed_at = action.timestamp
        tracking.close_reason = CloseReason.ALL_TARGETS_HIT

        # Write audit log
        await uow.audit_logs.create(
            tracking_id=tracking.id,
            signal_id=tracking.signal_id,
            event=AuditEventType.SIGNAL_CLOSED,
            payload={
                "reason": "all_targets_hit",
                "timestamp": action.timestamp.isoformat(),
            },
        )

        # Log event
        logger.info(f"✓ TRACKING COMPLETED: {tracking.signal.symbol} - all targets hit (tracking_id={tracking.id})")

        # Send Telegram notification
        #await self._telegram.send_signal_closed(tracking, "all_targets_hit", uow)

        # TODO: Update statistics (win)

    def _get_effective_entry_price(self, tracking: Tracking) -> Decimal | None:
        """
        Calculate the effective entry price for profit/loss calculations.

        If both entry1 and entry2 are touched, returns the average: (entry1 + entry2) / 2
        If emergency entry and entry1 are both touched, returns average of emergency and entry1
        Otherwise, returns the actual_entry_price.
        """
        if not tracking.actual_entry_price:
            return None

        signal_entries = tracking.signal.entries
        
        # If both entries are touched, calculate average entry
        if tracking.entry1_touched and tracking.entry2_touched:
            if len(signal_entries) >= 2:
                # Get both entry prices
                entry1_price = signal_entries[0].price
                entry2_price = signal_entries[1].price
                # Return average
                avg_entry = (entry1_price + entry2_price) / Decimal("2")
                return avg_entry.quantize(Decimal("0.00000001"))
        elif tracking.entry1_touched and tracking.entry_method == EntryMethod.EMERGENCY_ENTRY:
            # Both emergency and entry1 touched - average them
            emergency_price = self._calculate_emergency_entry_price_for_audit(tracking.signal)
            if emergency_price and len(signal_entries) >= 1:
                entry1_price = signal_entries[0].price
                # Return average of emergency entry and entry1
                avg_entry = (emergency_price + entry1_price) / Decimal("2")
                return avg_entry.quantize(Decimal("0.00000001"))
        
        # Otherwise, use the actual entry price
        return tracking.actual_entry_price

    def _calculate_profit_percentage(
        self,
        direction: Direction,
        entry_price: Decimal,
        exit_price: Decimal,
    ) -> Decimal:
        """Calculate profit percentage based on direction."""
        if direction == Direction.LONG:
            profit = ((exit_price - entry_price) / entry_price) * Decimal("100")
        else:
            profit = ((entry_price - exit_price) / entry_price) * Decimal("100")

        return profit.quantize(Decimal("0.01"))

    def _calculate_emergency_entry_price_for_audit(
        self,
        signal,
    ) -> Decimal | None:
        """
        Calculate emergency entry price for audit logging purposes.

        This duplicates the logic from EntryRule for diagnostic purposes.
        The actual business logic uses EntryRule's calculation.

        Formula:
        - LONG: emergency = EntryHigh + (TP1 - EntryHigh) / 5
        - SHORT: emergency = EntryLow - (EntryLow - TP1) / 5

        For LONG: EntryHigh is the higher price, emergency is between EntryHigh and TP1
        For SHORT: EntryLow is the lower price, emergency is between EntryLow and TP1
        """
        if not signal.entries or not signal.targets:
            return None

        sorted_entries = sorted(signal.entries, key=lambda e: e.price)
        tp1_price = signal.targets[0].price
        direction = signal.direction

        if direction == Direction.LONG:
            # LONG: EntryHigh is the higher price
            entry_high_price = sorted_entries[-1].price
            distance = tp1_price - entry_high_price
            fifth_distance = distance / Decimal("5")
            # Emergency entry is EntryHigh + fifth distance toward TP1
            return entry_high_price + fifth_distance
        else:
            # SHORT: EntryLow is the lower price
            entry_low_price = sorted_entries[0].price
            distance = entry_low_price - tp1_price
            fifth_distance = distance / Decimal("5")
            # Emergency entry is EntryLow - fifth distance toward TP1
            return entry_low_price - fifth_distance
