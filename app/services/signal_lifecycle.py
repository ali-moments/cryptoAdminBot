from app.core.dto import ParsedSignal
from app.database.enums import AuditEventType, SignalStatus
from app.database.models import Signal
from app.database.uow import UnitOfWork


class SignalLifecycleService:
    async def create_signal(
        self,
        parsed: ParsedSignal,
    ) -> Signal:
        async with UnitOfWork() as uow:
            signal = await uow.signals.create(
                source_id=parsed.source_id,
                symbol=parsed.symbol,
                direction=parsed.direction,
                leverage=parsed.leverage,
                stop_loss=parsed.stop_loss,
                expires_at=parsed.expires_at,
                status=SignalStatus.WAITING_ENTRY,
            )

            await uow.flush()

            for entry in parsed.entries:
                await uow.entries.create(
                    signal_id=signal.id,
                    entry_number=entry.number,
                    price=entry.price,
                )

            for target in parsed.targets:
                await uow.targets.create(
                    signal_id=signal.id,
                    target_number=target.number,
                    price=target.price,
                )

            await uow.audit.create(
                signal_id=signal.id,
                event=AuditEventType.SIGNAL_RECEIVED,
                payload={
                    "symbol": parsed.symbol,
                    "direction": parsed.direction.value,
                    "source_id": parsed.source_id,
                    "leverage": parsed.leverage,
                    "stop_loss": str(parsed.stop_loss),
                    "entries": [
                        {
                            "number": e.number,
                            "price": str(e.price),
                        }
                        for e in parsed.entries
                    ],
                    "targets": [
                        {
                            "number": t.number,
                            "price": str(t.price),
                        }
                        for t in parsed.targets
                    ],
                },
            )

            await uow.commit()

            return signal
