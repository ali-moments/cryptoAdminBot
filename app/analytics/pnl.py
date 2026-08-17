"""
PNL analytics module.

Calculates channel PNL for reporting purposes.
This is read-only analytics - does NOT affect signal processing.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from loguru import logger
from app.database.uow import UnitOfWork
from app.database.enums import CloseReason
from app.telegram.common.dto import PNLItem, PnlDTO
from app.market.cache import PriceCache
from app.database.enums import Direction
from app.analytics.utils import MathUtils, safe_percentage, PerformanceMonitor
from app.services.validation import ScoringValidator, ScoringValidationError


class PnlAnalytics:
    """Calculate PNL for channel signals (analytics/reporting only)."""
    
    def __init__(self, uow: UnitOfWork, price_cache: PriceCache) -> None:
        self._uow = uow
        self._price_cache = price_cache
        self._validator = ScoringValidator()
    
    @PerformanceMonitor.monitor_performance("calculate_24h_pnl")
    async def get_24h_pnl(self) -> PnlDTO:
        """Calculate 24-hour channel PNL for signals created TODAY."""
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)  # Today 00:00
        end = start + timedelta(days=1)  # Tomorrow 00:00
        logger.info(f"Calculating 24h PNL for period: {start} to {end}")
        return await self._calculate_pnl(start, end)
    
    @PerformanceMonitor.monitor_performance("calculate_weekly_pnl")
    async def get_weekly_pnl(self) -> PnlDTO:
        """Calculate weekly channel PNL for signals created THIS WEEK."""
        now = datetime.now(timezone.utc)
        # Start of current week (Monday 00:00)
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)  # Next Monday 00:00
        logger.info(f"Calculating weekly PNL for period: {start} to {end}")
        return await self._calculate_pnl(start, end)
        return await self._calculate_pnl(start, end)
    
    async def _calculate_pnl(
        self,
        start: datetime,
        end: datetime,
    ) -> PnlDTO:
        """Calculate PNL for signals created in time window."""
        # Validate date parameters
        if not isinstance(start, datetime):
            raise ScoringValidationError(f"Start time must be datetime, got {type(start)}")
        
        if not isinstance(end, datetime):
            raise ScoringValidationError(f"End time must be datetime, got {type(end)}")
        
        if start >= end:
            raise ScoringValidationError(f"Start time {start} must be before end time {end}")
        
        # Check for reasonable time ranges
        time_diff = end - start
        if time_diff.total_seconds() <= 0:
            raise ScoringValidationError("Time window must be positive")
        
        if time_diff.days > 365:
            logger.warning(f"Very large time window: {time_diff.days} days")
        
        items = []
        total_pnl = Decimal("0")
        tp_count = 0  # Count of signals that hit TP
        stop_count = 0  # Count of signals that hit STOP
        
        async with self._uow:
            # Get ALL trackings (active + closed) for signals created in period
            all_trackings = await self._uow.trackings.get_by_signal_creation_window(start, end)
            logger.debug(f"Found {len(all_trackings)} trackings for PNL calculation in window {start} to {end}")
            
            # Process each tracking
            for tracking in all_trackings:
                pnl_item = await self._build_pnl_item(tracking)
                if pnl_item:
                    items.append(pnl_item)
                    
                    # Count for win rate calculation (exclude OPEN signals)
                    if pnl_item.status.startswith("TP"):
                        tp_count += 1
                    elif pnl_item.status == "STOP":
                        stop_count += 1
                    
                    # Only realized PNL contributes to total
                    if pnl_item.status != "OPEN":
                        total_pnl += pnl_item.pnl
        
        # Calculate win rate: (TP signals / (TP signals + STOP signals)) * 100
        total_closed_signals = tp_count + stop_count
        if total_closed_signals > 0:
            win_rate = safe_percentage(tp_count, total_closed_signals)
            win_rate = MathUtils.round_decimal(win_rate, 2)
        else:
            win_rate = Decimal("0")
        
        logger.info(f"PNL calculation complete: {len(items)} items, total PNL: {total_pnl:.2f}%, win rate: {win_rate:.1f}% ({tp_count}/{total_closed_signals})")
        return PnlDTO(items=items, total=total_pnl, win_rate=win_rate)
    
    async def _build_pnl_item(self, tracking) -> Optional[PNLItem]:
        """Build PNL item for any tracking (active or closed)."""
        signal = tracking.signal
        
        # Get signal message
        signal_msg = await self._uow.telegram_messages.signal_message(signal.id)
        signal_msg_id = signal_msg.message_id if signal_msg else 0
        
        # Skip cancelled/expired signals
        if (tracking.close_reason in (CloseReason.CANCELLED, CloseReason.EXPIRED) or
            not tracking.has_entered):  # Never entered
            return None
        
        # Check if any TPs were hit (even if still active)
        if tracking.highest_target_hit > 0:
            # Show highest TP hit
            highest_tp = tracking.highest_target_hit
            status = f"TP{highest_tp}"
            
            # Get TARGET_HIT messages to find corresponding message ID
            tp_messages = await self._uow.telegram_messages.get_target_hit_messages(tracking.id)
            if len(tp_messages) >= highest_tp:
                status_msg_id = tp_messages[highest_tp - 1].message_id
            else:
                status_msg_id = None
            
            # Use profit from the highest TP hit
            tp_hits = [tp for tp in tracking.tp_hits if tp.position == highest_tp]
            if tp_hits:
                pnl = tp_hits[0].profit_percent
            else:
                pnl = Decimal("0")
                
        elif (tracking.close_reason in (CloseReason.ORIGINAL_STOP_LOSS, CloseReason.MOVED_STOP_LOSS)):
            # Stop loss hit
            status = "STOP"
            close_msg = await self._uow.telegram_messages.get_close_message(tracking.id)
            status_msg_id = close_msg.message_id if close_msg else None
            pnl = tracking.profit_percent or Decimal("0")
            
        elif tracking.is_active:
            # Still active - calculate unrealized P&L
            status = "OPEN"
            status_msg_id = signal_msg_id  # Use signal message
            pnl = await self._calculate_unrealized_pnl(tracking)
            
        else:
            # Closed for other reasons (ALL_TARGETS_HIT handled by TP logic above)
            return None
        
        # Apply leverage to the profit for display purposes
        leveraged_pnl = pnl * signal.leverage
        
        return PNLItem(
            symbol=signal.symbol,
            signal_msg_id=signal_msg_id,
            status=status,
            status_msg_id=status_msg_id,
            pnl=leveraged_pnl,
        )
    
    async def _calculate_unrealized_pnl(self, tracking) -> Decimal:
        """Calculate current unrealized P&L for active tracking."""
        if not tracking.actual_entry_price:
            return Decimal("0")
        
        # Get current market price
        current_price = self._price_cache.get_price(tracking.signal.symbol)
        if not current_price:
            return Decimal("0")
        
        # Calculate unrealized P&L percentage
        direction = tracking.signal.direction
        entry_price = tracking.actual_entry_price
        
        if direction == Direction.LONG:
            pnl_pct = safe_percentage(current_price - entry_price, entry_price)
        else:  # SHORT
            pnl_pct = safe_percentage(entry_price - current_price, entry_price)
        
        return MathUtils.round_decimal(pnl_pct, 2)