"""
Analytics ranking module.

This module provides ranking functionality for signal sources based on
their calculated scores and various performance metrics.
"""

from decimal import Decimal
from typing import Dict, List, Tuple
from dataclasses import dataclass

from app.core.dto import TimeWindow, ScoreBreakdown
from app.services.statistics import StatisticsService
from app.services.scoring import ScoringService
from app.database.uow import UnitOfWork


@dataclass
class RankedSource:
    """Represents a ranked signal source with its performance data."""
    source_id: int
    rank: int
    score_breakdown: ScoreBreakdown
    source_name: str | None = None


@dataclass
class RankingCriteria:
    """Criteria for ranking signal sources."""
    metric: str  # "score", "tp_rate", "profit", "avg_profit", "signal_count"
    time_window: TimeWindow | None = None
    min_signals: int = 0  # Minimum signal count for inclusion
    ascending: bool = False  # True for ascending order, False for descending


class AnalyticsRanking:
    """Provides ranking and leaderboard functionality for signal sources."""
    
    def __init__(self, uow: UnitOfWork) -> None:
        self._statistics_service = StatisticsService(uow)
        self._scoring_service = ScoringService(self._statistics_service)
        self._uow = uow

    async def get_score_leaderboard(
        self,
        criteria: RankingCriteria | None = None,
        limit: int | None = None,
    ) -> List[RankedSource]:
        """
        Get ranked leaderboard of signal sources by score.
        
        Default ranking is by overall score in descending order.
        """
        if criteria is None:
            criteria = RankingCriteria(metric="score", ascending=False)
        
        # Get all source scores
        all_scores = await self._scoring_service.calculate_all_scores(criteria.time_window)
        
        if not all_scores:
            return []
        
        # Filter by minimum signals if specified
        if criteria.min_signals > 0:
            filtered_scores = {}
            for source_id, breakdown in all_scores.items():
                if breakdown.signal_count >= criteria.min_signals:
                    filtered_scores[source_id] = breakdown
            all_scores = filtered_scores
        
        # Sort by specified metric
        sorted_sources = await self._sort_by_metric(all_scores, criteria)
        
        # Get source names
        source_names = await self._get_source_names(list(all_scores.keys()))
        
        # Create ranked results
        ranked_sources = []
        for rank, (source_id, score_breakdown) in enumerate(sorted_sources, 1):
            ranked_source = RankedSource(
                source_id=source_id,
                rank=rank,
                score_breakdown=score_breakdown,
                source_name=source_names.get(source_id),
            )
            ranked_sources.append(ranked_source)
        
        # Apply limit if specified
        if limit is not None:
            ranked_sources = ranked_sources[:limit]
        
        return ranked_sources

    async def get_performance_tiers(
        self,
        time_window: TimeWindow | None = None,
    ) -> Dict[str, List[RankedSource]]:
        """
        Classify sources into performance tiers based on their scores.
        
        Tiers:
        - Elite: 9.0+/10 (900+ score)
        - Excellent: 8.0-8.9/10 (800-899 score) 
        - Good: 7.0-7.9/10 (700-799 score)
        - Average: 6.0-6.9/10 (600-699 score)
        - Below Average: 5.0-5.9/10 (500-599 score)
        - Poor: <5.0/10 (<500 score)
        """
        
        leaderboard = await self.get_score_leaderboard(
            RankingCriteria(metric="score", time_window=time_window)
        )
        
        tiers = {
            "elite": [],
            "excellent": [],
            "good": [],
            "average": [],
            "below_average": [],
            "poor": [],
        }
        
        for ranked_source in leaderboard:
            score = ranked_source.score_breakdown.score
            
            if score >= 900:
                tiers["elite"].append(ranked_source)
            elif score >= 800:
                tiers["excellent"].append(ranked_source)
            elif score >= 700:
                tiers["good"].append(ranked_source)
            elif score >= 600:
                tiers["average"].append(ranked_source)
            elif score >= 500:
                tiers["below_average"].append(ranked_source)
            else:
                tiers["poor"].append(ranked_source)
        
        return tiers

    async def get_rising_stars(
        self,
        comparison_window_hours: int = 168,  # 7 days
        min_recent_signals: int = 5,
        limit: int = 10,
    ) -> List[Dict]:
        """
        Find sources that have significantly improved recently.
        
        Compares recent performance vs historical average.
        """
        
        recent_window = TimeWindow("recent", comparison_window_hours)
        all_time_window = TimeWindow.all_time()
        
        # Get recent and all-time scores
        recent_scores = await self._scoring_service.calculate_all_scores(recent_window)
        all_time_scores = await self._scoring_service.calculate_all_scores(all_time_window)
        
        rising_stars = []
        
        for source_id in recent_scores.keys():
            recent = recent_scores[source_id]
            all_time = all_time_scores.get(source_id)
            
            if (all_time and 
                recent.signal_count >= min_recent_signals and 
                all_time.signal_count > recent.signal_count):
                
                score_improvement = recent.score - all_time.score
                
                # Only consider sources with meaningful improvement
                if score_improvement > 50:  # At least 0.5 point improvement
                    rising_stars.append({
                        "source_id": source_id,
                        "recent_score": recent.score,
                        "all_time_score": all_time.score,
                        "improvement": score_improvement,
                        "recent_signals": recent.signal_count,
                        "total_signals": all_time.signal_count,
                    })
        
        # Sort by improvement
        rising_stars.sort(key=lambda x: x["improvement"], reverse=True)
        
        return rising_stars[:limit]

    async def get_consistency_ranking(
        self,
        time_window: TimeWindow | None = None,
    ) -> List[Dict]:
        """
        Rank sources by consistency (low variance in performance).
        
        Measures how consistent the TP hit rate and profit performance is.
        """
        
        all_stats = await self._statistics_service.get_all_sources_statistics(time_window)
        
        consistency_scores = []
        
        for source_id, stats in all_stats.items():
            if stats.completed_signals < 10:  # Need minimum sample size
                continue
            
            # Calculate consistency metrics
            tp_consistency = self._calculate_tp_consistency(source_id, stats)
            profit_consistency = self._calculate_profit_consistency(source_id, stats)
            
            # Combined consistency score
            overall_consistency = (tp_consistency + profit_consistency) / 2
            
            consistency_scores.append({
                "source_id": source_id,
                "consistency_score": overall_consistency,
                "tp_consistency": tp_consistency,
                "profit_consistency": profit_consistency,
                "completed_signals": stats.completed_signals,
                "tp_hit_rate": stats.tp_hit_rate,
                "average_profit": stats.average_profit,
            })
        
        # Sort by consistency score (higher is better)
        consistency_scores.sort(key=lambda x: x["consistency_score"], reverse=True)
        
        return consistency_scores

    async def get_metric_leaders(
        self,
        time_window: TimeWindow | None = None,
    ) -> Dict[str, RankedSource]:
        """
        Get the leader in each key performance metric.
        
        Returns the top source for each metric category.
        """
        
        metrics = [
            ("tp_rate", False),  # metric, ascending
            ("profit", False),
            ("avg_profit", False),
            ("signal_count", False),
            ("consistency", False),
        ]
        
        leaders = {}
        
        for metric, ascending in metrics:
            criteria = RankingCriteria(
                metric=metric,
                time_window=time_window,
                ascending=ascending
            )
            
            leaderboard = await self.get_score_leaderboard(criteria, limit=1)
            
            if leaderboard:
                leaders[metric] = leaderboard[0]
        
        return leaders

    async def compare_sources(
        self,
        source_ids: List[int],
        time_window: TimeWindow | None = None,
    ) -> List[RankedSource]:
        """Compare specific sources and return them ranked."""
        
        all_scores = await self._scoring_service.calculate_all_scores(time_window)
        source_names = await self._get_source_names(source_ids)
        
        # Filter to requested sources only
        filtered_scores = {
            sid: breakdown for sid, breakdown in all_scores.items()
            if sid in source_ids
        }
        
        # Sort by score
        sorted_sources = sorted(
            filtered_scores.items(),
            key=lambda x: x[1].score,
            reverse=True
        )
        
        # Create ranked comparison
        comparison = []
        for rank, (source_id, score_breakdown) in enumerate(sorted_sources, 1):
            ranked_source = RankedSource(
                source_id=source_id,
                rank=rank,
                score_breakdown=score_breakdown,
                source_name=source_names.get(source_id),
            )
            comparison.append(ranked_source)
        
        return comparison

    async def _sort_by_metric(
        self,
        scores: Dict[int, ScoreBreakdown],
        criteria: RankingCriteria,
    ) -> List[Tuple[int, ScoreBreakdown]]:
        """Sort scores by specified metric."""
        
        def get_sort_value(item: Tuple[int, ScoreBreakdown]) -> float:
            source_id, breakdown = item
            
            if criteria.metric == "score":
                return breakdown.score
            elif criteria.metric == "tp_rate":
                return float(breakdown.tp_hit_rate)
            elif criteria.metric == "profit":
                return float(breakdown.total_profit)
            elif criteria.metric == "avg_profit":
                return float(breakdown.average_profit)
            elif criteria.metric == "signal_count":
                return breakdown.signal_count
            else:
                return breakdown.score  # Default to score
        
        return sorted(
            scores.items(),
            key=get_sort_value,
            reverse=not criteria.ascending
        )

    async def _get_source_names(self, source_ids: List[int]) -> Dict[int, str]:
        """Get source names for display."""
        async with self._uow:
            sources = []
            for source_id in source_ids:
                source = await self._uow.signal_sources.get(source_id)
                if source:
                    sources.append(source)
            
            return {source.id: source.name for source in sources}

    def _calculate_tp_consistency(self, source_id: int, stats) -> float:
        """
        Calculate TP hit rate consistency.
        
        This is a simplified implementation. In practice, you'd want to
        analyze variance across time periods or individual signals.
        """
        # Placeholder - would need historical TP rate data
        # Higher TP rate sources are assumed more consistent for now
        return min(1.0, float(stats.tp_hit_rate) * 1.2)

    def _calculate_profit_consistency(self, source_id: int, stats) -> float:
        """
        Calculate profit consistency.
        
        This is a simplified implementation. In practice, you'd want to
        analyze variance in profit across signals.
        """
        # Placeholder - would need individual signal profit data
        # Sources with positive average profit are assumed more consistent
        avg_profit = float(stats.average_profit)
        if avg_profit <= 0:
            return 0.0
        
        # Simple heuristic - higher average profit with lower variance would be better
        return min(1.0, avg_profit / 10.0)  # Normalize to 0-1 range