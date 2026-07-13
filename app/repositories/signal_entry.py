from sqlalchemy import select

from app.database.models import SignalEntry
from app.repositories.base import BaseRepository


class SignalEntryRepository(BaseRepository[SignalEntry]):
    model = SignalEntry

    async def by_signal(
        self,
        signal_id: int,
    ) -> list[SignalEntry]:
        stmt = (
            select(SignalEntry)
            .where(SignalEntry.signal_id == signal_id)
            .order_by(SignalEntry.entry_number)
        )

        result = await self.session.scalars(stmt)
        return list(result)
