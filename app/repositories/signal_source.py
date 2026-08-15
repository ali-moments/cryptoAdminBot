from sqlalchemy import select

from app.database.models import SignalSource
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository[SignalSource]):
    model = SignalSource

    async def get_by_channel(
        self,
        telegram_channel_id: int,
    ) -> SignalSource | None:
        stmt = select(SignalSource).where(
            SignalSource.telegram_channel_id == telegram_channel_id,
        )

        return await self.session.scalar(stmt)

    async def active(self) -> list[SignalSource]:
        stmt = (
            select(SignalSource)
            .where(SignalSource.is_active.is_(True))
            .order_by(
                SignalSource.score.desc(),
                SignalSource.manual_priority.desc(),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def all(self) -> list[SignalSource]:
        """Get all sources (active and inactive) ordered by score."""
        stmt = (
            select(SignalSource)
            .order_by(
                SignalSource.score.desc(),
                SignalSource.manual_priority.desc(),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def get_by_channel_id(
        self,
        channel_id: int,
    ) -> SignalSource | None:
        stmt = select(SignalSource).where(
            SignalSource.telegram_channel_id == channel_id,
        )

        result = await self.session.execute(stmt)

        return result.scalar_one_or_none()

    # === Scoring-specific methods ===
    async def update_score(
        self,
        source_id: int,
        score: int,
    ) -> bool:
        """Update the score for a signal source."""
        source = await self.get(source_id)
        if not source:
            return False

        source.score = score
        await self.session.flush()
        return True

    async def update_statistics(
        self,
        source_id: int,
        **stats,
    ) -> bool:
        """
        Update statistics fields for a signal source.

        Accepts keyword arguments matching SignalSource fields:
        - total_signals, winning_signals, losing_signals, etc.
        """
        source = await self.get(source_id)
        if not source:
            return False

        # Update only provided fields
        for field, value in stats.items():
            if hasattr(source, field):
                setattr(source, field, value)

        await self.session.flush()
        return True

    async def batch_update_scores(
        self,
        score_updates: dict[int, int],
    ) -> int:
        """
        Update scores for multiple sources in a batch operation.

        Args:
            score_updates: Dict mapping source_id to new score

        Returns:
            Number of sources updated
        """
        updated_count = 0

        for source_id, score in score_updates.items():
            if await self.update_score(source_id, score):
                updated_count += 1

        return updated_count

    async def get_sources_needing_score_update(
        self,
        max_age_hours: int = 24,
    ) -> list[int]:
        """
        Get source IDs that need score updates based on age criteria.

        This is a placeholder implementation. In practice, you might want
        to track when scores were last updated.
        """
        sources = await self.active()
        return [source.id for source in sources]

    async def get_score_distribution(self) -> dict[str, int]:
        """Get distribution of scores across all active sources."""
        sources = await self.active()

        distribution = {
            "elite": 0,      # 900+
            "excellent": 0,  # 800-899
            "good": 0,       # 700-799
            "average": 0,    # 600-699
            "below_avg": 0,  # 500-599
            "poor": 0,       # <500
        }

        for source in sources:
            score = source.score
            if score >= 900:
                distribution["elite"] += 1
            elif score >= 800:
                distribution["excellent"] += 1
            elif score >= 700:
                distribution["good"] += 1
            elif score >= 600:
                distribution["average"] += 1
            elif score >= 500:
                distribution["below_avg"] += 1
            else:
                distribution["poor"] += 1

        return distribution
