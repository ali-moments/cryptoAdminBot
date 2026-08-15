from datetime import datetime, UTC
from typing import TYPE_CHECKING

from loguru import logger

from app.database.enums import TrackingStatus, AuditEventType
from app.engine.actions import TrackingCompleted, WaitingEntryExpired
from app.services.settings import States
from app.services.statistics import StatisticsService

if TYPE_CHECKING:
    from app.engine.action_processor import ActionProcessor


class AdminService:
    """Service layer for admin bot operations.

    Handles admin commands while delegating to existing services and repositories.
    Never directly modifies tracking state - uses ActionProcessor for proper state transitions.
    """

    def __init__(
        self,
        uow_factory,
        statistics_service: StatisticsService,
        action_processor: "ActionProcessor",
        states: States,
    ) -> None:
        self._uow_factory = uow_factory
        self._statistics = statistics_service
        self._action_processor = action_processor
        self._states = states

    # ================================
    # Global Bot Control
    # ================================

    def get_bot_status(self) -> dict:
        """Get current bot status."""
        return {
            "bot_enabled": self._states.bot_enabled,
            "dev_mode": self._states.dev_mode,
            "target_channel": self._states.target_channel,
            "active_signals_limit": self._states.active_signals_limit,
        }

    def toggle_bot_enabled(self) -> bool:
        """Toggle global bot enabled state."""
        new_state = self._states.toggle_bot_enabled()
        logger.info(f"Bot {'enabled' if new_state else 'disabled'} by admin")
        return new_state

    # ================================
    # Signal Sources Management
    # ================================

    async def get_sources_page(self, page: int, page_size: int = 5) -> dict:
        """Get paginated list of signal sources with scores."""
        async with self._uow_factory() as uow:
            # Get all sources ordered by score
            sources = await uow.signal_sources.active()

            # Calculate pagination
            total = len(sources)
            total_pages = (total + page_size - 1) // page_size
            start_idx = page * page_size
            end_idx = start_idx + page_size
            page_sources = sources[start_idx:end_idx]

            # Get statistics for each source on this page
            source_data = []
            for source in page_sources:
                try:
                    stats = await self._statistics.get_source_statistics(source.id)
                    source_data.append({
                        "id": source.id,
                        "name": source.name,
                        "score": source.score / 100,  # Convert back from ×100 format
                        "is_active": source.is_active,
                        "total_signals": stats.total_signals,
                        "tp_hit_rate": float(stats.tp_hit_rate),
                        "winrate": float(source.winrate) if source.winrate else 0.0,
                    })
                except Exception as e:
                    logger.warning(f"Failed to get stats for source {source.id}: {e}")
                    # Fallback to basic data
                    source_data.append({
                        "id": source.id,
                        "name": source.name,
                        "score": source.score / 100,
                        "is_active": source.is_active,
                        "total_signals": source.total_signals,
                        "tp_hit_rate": 0.0,
                        "winrate": float(source.winrate) if source.winrate else 0.0,
                    })

            return {
                "sources": source_data,
                "current_page": page,
                "total_pages": total_pages,
                "total_sources": total,
                "has_prev": page > 0,
                "has_next": page < total_pages - 1,
            }

    async def toggle_source_active(self, source_id: int) -> dict:
        """Toggle source active status."""
        async with self._uow_factory() as uow:
            source = await uow.signal_sources.get(source_id)
            if not source:
                return {"success": False, "message": "Source not found"}

            old_status = source.is_active
            source.is_active = not source.is_active
            await uow.commit()

            logger.info(f"Source {source.name} {'enabled' if source.is_active else 'disabled'} by admin")

            return {
                "success": True,
                "source_name": source.name,
                "new_status": source.is_active,
                "old_status": old_status,
            }

    # ================================
    # Active Trackings Management
    # ================================

    async def get_trackings_page(self, page: int, page_size: int = 3) -> dict:
        """Get paginated list of active trackings."""
        async with self._uow_factory() as uow:
            trackings = await uow.trackings.get_active()

            # Calculate pagination
            total = len(trackings)
            total_pages = (total + page_size - 1) // page_size
            start_idx = page * page_size
            end_idx = start_idx + page_size
            page_trackings = trackings[start_idx:end_idx]

            tracking_data = []
            for tracking in page_trackings:
                tracking_data.append({
                    "id": tracking.id,
                    "symbol": tracking.signal.symbol,
                    "direction": tracking.signal.direction.value,
                    "source_name": tracking.signal.source.name,
                    "status": tracking.status.value,
                    "entry_method": tracking.entry_method.value if tracking.entry_method else "none",
                    "entry1_touched": tracking.entry1_touched,
                    "entry2_touched": tracking.entry2_touched,
                    "highest_target_hit": tracking.highest_target_hit,
                    "started_at": tracking.started_at.isoformat(),
                })

            return {
                "trackings": tracking_data,
                "current_page": page,
                "total_pages": total_pages,
                "total_trackings": total,
                "has_prev": page > 0,
                "has_next": page < total_pages - 1,
            }

    async def close_tracking(self, tracking_id: int, reason: str = "admin_close") -> dict:
        """Close a tracking using existing ActionProcessor."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return {"success": False, "message": "Tracking not found"}

            if not tracking.is_active:
                return {"success": False, "message": "Tracking already closed"}

            # Create appropriate action based on current status
            current_time = datetime.now(UTC)

            if tracking.status == TrackingStatus.WAITING_ENTRY:
                # For waiting entry, use WaitingEntryExpired
                action = WaitingEntryExpired(
                    timestamp=current_time,
                    reason=f"admin_cancelled_{reason}",
                )
            else:
                # For active tracking, use TrackingCompleted
                action = TrackingCompleted(
                    timestamp=current_time,
                )

            # Process action through ActionProcessor
            await self._action_processor.process(tracking, [action], uow)

            # Add admin audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.SIGNAL_CLOSED,
                payload={
                    "reason": f"admin_{reason}",
                    "admin_action": True,
                    "timestamp": current_time.isoformat(),
                },
            )

            await uow.commit()

            logger.info(f"Tracking {tracking_id} ({tracking.signal.symbol}) closed by admin")

            return {
                "success": True,
                "tracking_id": tracking_id,
                "symbol": tracking.signal.symbol,
                "action_type": "cancelled" if tracking.status == TrackingStatus.WAITING_ENTRY else "closed",
            }

    async def cancel_tracking(self, tracking_id: int) -> dict:
        """Cancel a tracking (alias for close_tracking with cancel reason)."""
        return await self.close_tracking(tracking_id, "cancel")

    # ================================
    # Dev Mode Management
    # ================================

    async def toggle_dev_mode(self) -> dict:
        """Toggle dev mode and properly close all active trackings first."""
        # Step 1: Get all active trackings
        async with self._uow_factory() as uow:
            active_trackings = await uow.trackings.get_active()

            if not active_trackings:
                # No active trackings, safe to toggle immediately
                new_dev_mode = self._states.toggle_dev_mode()
                logger.info(f"Dev mode {'enabled' if new_dev_mode else 'disabled'} by admin (no active trackings)")
                return {
                    "success": True,
                    "new_dev_mode": new_dev_mode,
                    "target_channel": self._states.target_channel,
                    "closed_trackings": 0,
                }

            # Step 2: Close all active trackings
            current_time = datetime.now(UTC)
            closed_count = 0

            for tracking in active_trackings:
                try:
                    # Choose appropriate action based on status
                    if tracking.status == TrackingStatus.WAITING_ENTRY:
                        action = WaitingEntryExpired(
                            timestamp=current_time,
                            reason="dev_mode_toggle",
                        )
                    else:
                        action = TrackingCompleted(
                            timestamp=current_time,
                        )

                    # Process through ActionProcessor
                    await self._action_processor.process(tracking, [action], uow)

                    # Add admin audit log
                    await uow.audit_logs.create(
                        tracking_id=tracking.id,
                        signal_id=tracking.signal_id,
                        event=AuditEventType.SIGNAL_CLOSED,
                        payload={
                            "reason": "dev_mode_toggle",
                            "admin_action": True,
                            "timestamp": current_time.isoformat(),
                        },
                    )

                    closed_count += 1

                except Exception as e:
                    logger.error(f"Failed to close tracking {tracking.id} during dev mode toggle: {e}")
                    # Continue with other trackings
                    continue

            await uow.commit()

            # Step 3: Only after all trackings are closed, toggle dev mode
            new_dev_mode = self._states.toggle_dev_mode()

            logger.info(f"Dev mode toggled by admin: {closed_count} trackings closed, new mode: {'dev' if new_dev_mode else 'production'}")

            return {
                "success": True,
                "new_dev_mode": new_dev_mode,
                "target_channel": self._states.target_channel,
                "closed_trackings": closed_count,
            }

    # ================================
    # System Information
    # ================================

    async def get_system_stats(self) -> dict:
        """Get overall system statistics."""
        async with self._uow_factory() as uow:
            active_trackings = await uow.trackings.get_active()
            active_sources = await uow.signal_sources.active()

            return {
                "active_trackings_count": len(active_trackings),
                "active_sources_count": len(active_sources),
                "bot_enabled": self._states.bot_enabled,
                "dev_mode": self._states.dev_mode,
                "signals_limit": self._states.active_signals_limit,
            }
