from datetime import datetime
from decimal import Decimal
from sqlalchemy import select, func

from app.database.models import TpHit
from app.repositories.base import BaseRepository


class TpHitRepository(BaseRepository[TpHit]):
    model = TpHit

    async def by_tracking(
        self,
        tracking_id: int,
    ) -> list[TpHit]:
        stmt = (
            select(TpHit)
            .where(TpHit.tracking_id == tracking_id)
            .order_by(TpHit.position)
        )

        result = await self.session.scalars(stmt)
        return list(result)
    # === Scoring-specific queries ===

    async def count_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> int:
        """Count TP hits for a specific source."""
        from app.database.models import Tracking, Signal
        
        stmt = (
            select(func.count())
            .select_from(TpHit)
            .join(Tracking)
            .join(Signal)
            .where(Signal.source_id == source_id)
        )
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        return (await self.session.scalar(stmt)) or 0

    async def get_by_source_with_details(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> list[TpHit]:
        """Get TP hits for a source with tracking and signal details."""
        from app.database.models import Tracking, Signal
        
        stmt = (
            select(TpHit)
            .join(Tracking)
            .join(Signal)
            .where(Signal.source_id == source_id)
            .options(
                selectinload(TpHit.tracking).selectinload(Tracking.signal)
            )
            .order_by(TpHit.hit_at.desc())
        )
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        result = await self.session.scalars(stmt)
        return list(result)

    async def get_performance_stats_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> dict:
        """Get TP hit performance statistics for a source."""
        from app.database.models import Tracking, Signal
        
        stmt = (
            select(
                func.count().label('total_hits'),
                func.avg(TpHit.profit_percent).label('avg_tp_profit'),
                func.max(TpHit.profit_percent).label('best_tp_profit'),
                func.min(TpHit.profit_percent).label('worst_tp_profit'),
            )
            .select_from(TpHit)
            .join(Tracking)
            .join(Signal)
            .where(Signal.source_id == source_id)
        )
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        result = await self.session.execute(stmt)
        row = result.first()
        
        return {
            'total_hits': row.total_hits or 0,
            'avg_tp_profit': row.avg_tp_profit or Decimal('0'),
            'best_tp_profit': row.best_tp_profit or Decimal('0'),
            'worst_tp_profit': row.worst_tp_profit or Decimal('0'),
        }