from datetime import datetime, timedelta, UTC
from loguru import logger
from app.core.dto import ValidatedSignal
from app.database.enums import AuditEventType, SignalStatus
from app.database.models import (
    AuditLog,
    Signal,
    SignalEntry,
    SignalSource,
    SignalTarget,
)
from app.database.uow import UnitOfWork


class SignalLifecycleService:
    async def _find_duplicate(
        self,
        signal: ValidatedSignal,
        uow: UnitOfWork,
    ) -> Signal | None:

        candidates = await uow.signals.find_active_candidates(
            symbol=signal.symbol,
            direction=signal.direction,
        )

        new_entries = {
            entry.price
            for entry in signal.entries
        }

        new_targets = {
            target.price
            for target in signal.targets
        }

        for candidate in candidates:
            db_entries = {
                entry.price
                for entry in candidate.entries
            }

            db_targets = {
                target.price
                for target in candidate.targets
            }

            entries_match = (
                new_entries <= db_entries
                or db_entries <= new_entries
            )

            targets_match = (
                new_targets <= db_targets
                or db_targets <= new_targets
            )

            if entries_match and targets_match:
                return candidate

        return None


    async def create_signal(
        self,
        signal: ValidatedSignal,
        source: SignalSource,
        uow: UnitOfWork,
    ) -> Signal:
        duplicate = await self._find_duplicate(
            signal=signal,
            uow=uow,
        )

        if duplicate is not None:
            logger.trace("Duplicate signal ignored.")
            return duplicate

        db_signal = Signal(
            source_id=source.id,
            symbol=signal.symbol,
            direction=signal.direction,
            leverage=signal.leverage,
            stop_loss=signal.stop_loss,
            expires_at=datetime.now(UTC) + timedelta(hours=72),
            status=SignalStatus.WAITING_ENTRY,
        )

        await uow.signals.add(db_signal)
        await uow.flush()

        logger.trace("Signal added to db.")

        for entry in signal.entries:
            await uow.signal_entries.add(
                SignalEntry(
                    signal_id = db_signal.id,
                    position = entry.position,
                    price = entry.price,
                )
            )
        logger.trace("Entries added to db.")

        for target in signal.targets:
            await uow.signal_targets.add(
                SignalTarget(
                    signal_id = db_signal.id,
                    position = target.position,
                    price = target.price,
                )
            )
        logger.trace("Targets added to db.")

        await uow.audit_logs.add(
            AuditLog(
                signal_id=db_signal.id,
                event=AuditEventType.SIGNAL_RECEIVED,
                payload={
                    "symbol": db_signal.symbol,
                    "direction": db_signal.direction,
                    "source_id": db_signal.source_id,
                    "leverage": db_signal.leverage,
                    "stop_loss": str(db_signal.stop_loss),
                    "entries": str(signal.entries),
                    "targets": str(signal.targets),
                }
            )
        )
        logger.trace("Audit_log for the Signal saved to db.")

        return db_signal
