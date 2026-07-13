from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import SessionLocal
from app.repositories.audit import AuditRepository
from app.repositories.signal import SignalRepository
from app.repositories.signal_entry import SignalEntryRepository
from app.repositories.signal_target import SignalTargetRepository
from app.repositories.source import SourceRepository
from app.repositories.telegram import TelegramRepository
from app.repositories.tp_hit import TpHitRepository
from app.repositories.tracking import TrackingRepository


class UnitOfWork:
    def __init__(self) -> None:
        self.session: AsyncSession | None = None

        self.signals: SignalRepository
        self.entries: SignalEntryRepository
        self.targets: SignalTargetRepository
        self.trackings: TrackingRepository
        self.tp_hits: TpHitRepository
        self.telegram: TelegramRepository
        self.audit: AuditRepository
        self.sources: SourceRepository

    async def __aenter__(self) -> "UnitOfWork":
        self.session = SessionLocal()

        self.signals = SignalRepository(self.session)
        self.entries = SignalEntryRepository(self.session)
        self.targets = SignalTargetRepository(self.session)
        self.trackings = TrackingRepository(self.session)
        self.tp_hits = TpHitRepository(self.session)
        self.telegram = TelegramRepository(self.session)
        self.audit = AuditRepository(self.session)
        self.sources = SourceRepository(self.session)

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc is not None:
                await self.rollback()
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def flush(self) -> None:
        await self.session.flush()
