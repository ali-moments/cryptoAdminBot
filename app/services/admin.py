from datetime import datetime, UTC
from typing import TYPE_CHECKING

from loguru import logger

from app.database.enums import TrackingStatus, AuditEventType, CloseReason
from app.services.settings import States
from app.services.statistics import StatisticsService

if TYPE_CHECKING:
    from app.services.telegram import TelegramService


class AdminService:
    """Service layer for admin bot operations.

    Handles admin commands while delegating to existing services and repositories.
    Directly manages tracking status changes for admin operations.
    """

    def __init__(
        self,
        uow_factory,
        statistics_service: StatisticsService,
        states: States,
        telegram_service: "TelegramService" = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._statistics = statistics_service
        self._states = states
        self._telegram = telegram_service

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
            sources = await uow.signal_sources.all()

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
                    # Only get statistics for sources with signals to avoid errors
                    if source.total_signals > 0:
                        stats = await self._statistics.get_source_statistics(source.id)
                        tp_hit_rate = float(stats.tp_hit_rate)
                        total_signals_from_stats = stats.total_signals
                    else:
                        tp_hit_rate = 0.0
                        total_signals_from_stats = source.total_signals
                        
                    source_data.append({
                        "id": source.id,
                        "name": source.name,
                        "score": source.score / 100,  # Convert back from ×100 format
                        "is_active": source.is_active,
                        "total_signals": total_signals_from_stats,
                        "tp_hit_rate": tp_hit_rate,
                        "winrate": float(source.winrate) if source.winrate else 0.0,
                    })
                except Exception as e:
                    logger.warning(f"Failed to get stats for source {source.id}: {e}")
                    # Fallback to basic data without statistics service
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
        """Close a tracking by setting status to CANCELLED and is_active to False."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return {"success": False, "message": "Tracking not found"}

            if not tracking.is_active:
                return {"success": False, "message": "Tracking already closed"}

            # Directly update tracking status and deactivate
            current_time = datetime.now(UTC)
            old_status = tracking.status
            
            tracking.status = TrackingStatus.CANCELLED
            tracking.is_active = False
            tracking.closed_at = current_time
            tracking.close_reason = CloseReason.CANCELLED

            # Add admin audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.SIGNAL_CLOSED,
                payload={
                    "reason": f"admin_{reason}",
                    "admin_action": True,
                    "timestamp": current_time.isoformat(),
                    "old_status": old_status.value,
                    "new_status": TrackingStatus.CANCELLED.value,
                },
            )

            await uow.commit()

            logger.info(f"Tracking {tracking_id} ({tracking.signal.symbol}) cancelled by admin")

            return {
                "success": True,
                "tracking_id": tracking_id,
                "symbol": tracking.signal.symbol,
                "action_type": "cancelled",
            }

    async def cancel_tracking(self, tracking_id: int) -> dict:
        """Cancel a tracking (alias for close_tracking with cancel reason)."""
        return await self.close_tracking(tracking_id, "cancel")

    async def stop_tracking(self, tracking_id: int) -> dict:
        """Stop tracking: set is_active=False, status=CLOSED, send stop message."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return {"success": False, "message": "Tracking not found"}

            if not tracking.is_active:
                return {"success": False, "message": "Tracking already stopped"}

            # Update tracking status and deactivate
            current_time = datetime.now(UTC)
            old_status = tracking.status
            
            tracking.status = TrackingStatus.CLOSED
            tracking.is_active = False
            tracking.closed_at = current_time
            tracking.close_reason = CloseReason.ADMIN_STOP

            # Add admin audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.SIGNAL_CLOSED,
                payload={
                    "reason": "admin_stop",
                    "admin_action": True,
                    "timestamp": current_time.isoformat(),
                    "old_status": old_status.value,
                    "new_status": TrackingStatus.CLOSED.value,
                },
            )

            await uow.commit()

            # Send stop message if telegram service available
            if self._telegram:
                try:
                    await self._telegram.send_admin_stop_message(tracking, uow)
                    logger.info(f"Stop message sent for tracking {tracking_id}")
                except Exception as e:
                    logger.error(f"Failed to send stop message for tracking {tracking_id}: {e}")

            logger.info(f"Tracking {tracking_id} ({tracking.signal.symbol}) stopped by admin")

            return {
                "success": True,
                "tracking_id": tracking_id,
                "symbol": tracking.signal.symbol,
                "tracking": tracking,  # Include tracking for sender integration
                "action_type": "stopped",
            }

    async def send_tp_hit(self, tracking_id: int, tp_position: int) -> dict:
        """Trigger TP hit: update highest_target_hit, send TP message."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return {"success": False, "message": "Tracking not found"}

            if not tracking.is_active:
                return {"success": False, "message": "Tracking not active"}

            # Validate TP position
            targets = tracking.signal.targets
            if tp_position < 1 or tp_position > len(targets):
                return {"success": False, "message": f"Invalid TP position: {tp_position}"}

            # Check if TP already hit
            if tracking.highest_target_hit >= tp_position:
                return {"success": False, "message": f"TP{tp_position} already hit"}

            # Update highest target hit
            old_highest = tracking.highest_target_hit
            tracking.highest_target_hit = max(tracking.highest_target_hit, tp_position)

            # Create TP hit record if not exists
            existing_tp = await uow.tp_hits.get_by_tracking_and_position(tracking_id, tp_position)
            if not existing_tp:
                target_price = targets[tp_position - 1].price  # TP positions are 1-indexed
                
                await uow.tp_hits.create(
                    tracking_id=tracking_id,
                    position=tp_position,
                    price=target_price,
                    profit_percent=0,  # Will be calculated later if needed
                    hit_at=datetime.now(UTC),
                )

            # Add admin audit log
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=AuditEventType.TARGET_HIT,
                payload={
                    "reason": "admin_tp_hit",
                    "admin_action": True,
                    "tp_position": tp_position,
                    "old_highest": old_highest,
                    "new_highest": tracking.highest_target_hit,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            await uow.commit()

            # Send TP hit message if telegram service available
            if self._telegram:
                try:
                    await self._telegram.send_admin_tp_hit(tracking, tp_position, uow)
                    logger.info(f"TP{tp_position} hit message sent for tracking {tracking_id}")
                except Exception as e:
                    logger.error(f"Failed to send TP hit message for tracking {tracking_id}: {e}")

            logger.info(f"TP{tp_position} hit manually for tracking {tracking_id} ({tracking.signal.symbol}) by admin")

            return {
                "success": True,
                "tracking_id": tracking_id,
                "symbol": tracking.signal.symbol,
                "tp_position": tp_position,
                "tracking": tracking,  # Include tracking for sender integration
                "action_type": f"tp{tp_position}_hit",
            }

    async def send_entry_hit(self, tracking_id: int, entry_position: int) -> dict:
        """Trigger entry hit: update entry1_touched/entry2_touched, send entry message."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return {"success": False, "message": "Tracking not found"}

            if not tracking.is_active:
                return {"success": False, "message": "Tracking not active"}

            # Validate entry position
            entries = tracking.signal.entries
            if entry_position < 1 or entry_position > len(entries):
                return {"success": False, "message": f"Invalid entry position: {entry_position}"}

            # Check if already touched
            if entry_position == 1 and tracking.entry1_touched:
                return {"success": False, "message": "Entry1 already touched"}
            if entry_position == 2 and tracking.entry2_touched:
                return {"success": False, "message": "Entry2 already touched"}

            # Update entry touched flags
            old_entry1 = tracking.entry1_touched
            old_entry2 = tracking.entry2_touched
            
            if entry_position == 1:
                tracking.entry1_touched = True
            elif entry_position == 2:
                tracking.entry2_touched = True

            # Add admin audit log
            event_type = AuditEventType.ENTRY1_HIT if entry_position == 1 else AuditEventType.ENTRY2_HIT
            await uow.audit_logs.create(
                tracking_id=tracking.id,
                signal_id=tracking.signal_id,
                event=event_type,
                payload={
                    "reason": "admin_entry_hit",
                    "admin_action": True,
                    "entry_position": entry_position,
                    "old_entry1_touched": old_entry1,
                    "old_entry2_touched": old_entry2,
                    "new_entry1_touched": tracking.entry1_touched,
                    "new_entry2_touched": tracking.entry2_touched,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            await uow.commit()

            # Send entry hit message if telegram service available
            if self._telegram:
                try:
                    await self._telegram.send_admin_entry_hit(tracking, entry_position, uow)
                    logger.info(f"Entry{entry_position} hit message sent for tracking {tracking_id}")
                except Exception as e:
                    logger.error(f"Failed to send entry hit message for tracking {tracking_id}: {e}")

            logger.info(f"Entry{entry_position} hit manually for tracking {tracking_id} ({tracking.signal.symbol}) by admin")

            return {
                "success": True,
                "tracking_id": tracking_id,
                "symbol": tracking.signal.symbol,
                "entry_position": entry_position,
                "tracking": tracking,  # Include tracking for sender integration
                "action_type": f"entry{entry_position}_hit",
            }

    async def get_tracking_detail(self, tracking_id: int) -> dict:
        """Get detailed tracking info with targets and entries for detail view."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return {"success": False, "message": "Tracking not found"}

            # Build tracking data with full details
            tracking_data = {
                "id": tracking.id,
                "symbol": tracking.signal.symbol,
                "direction": tracking.signal.direction.value,
                "source_name": tracking.signal.source.name,
                "status": tracking.status.value,
                "is_active": tracking.is_active,
                "entry_method": tracking.entry_method.value if tracking.entry_method else "none",
                "entry1_touched": tracking.entry1_touched,
                "entry2_touched": tracking.entry2_touched,
                "highest_target_hit": tracking.highest_target_hit,
                "started_at": tracking.started_at.isoformat() if tracking.started_at else None,
                "actual_entry_price": float(tracking.actual_entry_price) if tracking.actual_entry_price else None,
                "current_stop_loss": float(tracking.current_stop_loss),
                
                # Entries
                "entries": [
                    {
                        "position": i + 1,
                        "price": float(entry.price),
                        "touched": tracking.entry1_touched if i == 0 else (tracking.entry2_touched if i == 1 else False)
                    }
                    for i, entry in enumerate(tracking.signal.entries)
                ],
                
                # Targets
                "targets": [
                    {
                        "position": i + 1,
                        "price": float(target.price),
                        "hit": i + 1 <= tracking.highest_target_hit
                    }
                    for i, target in enumerate(tracking.signal.targets)
                ],
            }

            return {
                "success": True,
                "tracking": tracking_data,
            }

    async def get_tracking_targets(self, tracking_id: int) -> list:
        """Get available TP targets for selection menu."""
        async with self._uow_factory() as uow:
            tracking = await uow.trackings.get_full(tracking_id)
            if not tracking:
                return []

            targets = []
            for i, target in enumerate(tracking.signal.targets):
                targets.append({
                    "position": i + 1,
                    "price": float(target.price),
                    "hit": i + 1 <= tracking.highest_target_hit,
                    "available": i + 1 > tracking.highest_target_hit,  # Can only hit unhit targets
                })

            return targets

    # ================================
    # Dev Mode Management
    # ================================

    async def toggle_dev_mode(self) -> dict:
        """Toggle dev mode and properly cancel all active trackings first."""
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

            # Step 2: Cancel all active trackings by setting status to CANCELLED and is_active to False
            current_time = datetime.now(UTC)
            closed_count = 0

            for tracking in active_trackings:
                try:
                    # Directly update tracking status and deactivate
                    tracking.status = TrackingStatus.CANCELLED
                    tracking.is_active = False
                    tracking.closed_at = current_time
                    tracking.close_reason = CloseReason.CANCELLED

                    # Add admin audit log
                    await uow.audit_logs.create(
                        tracking_id=tracking.id,
                        signal_id=tracking.signal_id,
                        event=AuditEventType.SIGNAL_CLOSED,
                        payload={
                            "reason": "dev_mode_toggle",
                            "admin_action": True,
                            "timestamp": current_time.isoformat(),
                            "old_status": tracking.status.value,
                            "new_status": TrackingStatus.CANCELLED.value,
                        },
                    )

                    closed_count += 1
                    logger.info(f"Tracking {tracking.id} ({tracking.signal.symbol}) cancelled due to dev mode toggle")

                except Exception as e:
                    logger.error(f"Failed to cancel tracking {tracking.id} during dev mode toggle: {e}")
                    # Continue with other trackings
                    continue

            await uow.commit()

            # Step 3: Only after all trackings are cancelled, toggle dev mode
            new_dev_mode = self._states.toggle_dev_mode()

            logger.info(f"Dev mode toggled by admin: {closed_count} trackings cancelled, new mode: {'dev' if new_dev_mode else 'production'}")

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
            all_sources = await uow.signal_sources.all()
            active_sources = [s for s in all_sources if s.is_active]

            return {
                "active_trackings_count": len(active_trackings),
                "active_sources_count": len(active_sources),
                "total_sources_count": len(all_sources),
                "bot_enabled": self._states.bot_enabled,
                "dev_mode": self._states.dev_mode,
                "signals_limit": self._states.active_signals_limit,
            }
