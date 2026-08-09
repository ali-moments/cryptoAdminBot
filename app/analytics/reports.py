"""
Analytics reports module.

This module provides comprehensive reporting functionality for signal sources,
generating formatted reports for various stakeholders and use cases.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any
from dataclasses import dataclass

from app.core.dto import TimeWindow, SignalStatistics, ScoreBreakdown
from app.analytics.statistics import AnalyticsStatistics
from app.analytics.ranking import AnalyticsRanking, RankingCriteria
from app.database.uow import UnitOfWork


@dataclass
class ReportSection:
    """Represents a section in a report."""
    title: str
    content: Dict[str, Any]
    format_type: str = "table"  # "table", "list", "summary", "chart_data"


@dataclass
class GeneratedReport:
    """Complete generated report with metadata."""
    title: str
    generated_at: datetime
    time_window: str
    sections: List[ReportSection]
    summary: Dict[str, Any]


class AnalyticsReports:
    """Generates comprehensive reports for signal source performance analysis."""
    
    def __init__(self, uow: UnitOfWork) -> None:
        self._analytics_stats = AnalyticsStatistics(uow)
        self._analytics_ranking = AnalyticsRanking(uow)
        self._uow = uow

    async def generate_executive_summary(
        self,
        time_window: TimeWindow | None = None,
    ) -> GeneratedReport:
        """
        Generate executive summary report for high-level overview.
        
        Suitable for management and stakeholders who need key metrics.
        """
        
        # Get system overview
        overview = await self._analytics_stats.get_system_overview(time_window)
        
        # Get performance tiers
        tiers = await self._analytics_ranking.get_performance_tiers(time_window)
        
        # Get top performers
        top_performers = await self._analytics_ranking.get_score_leaderboard(
            RankingCriteria(metric="score", time_window=time_window),
            limit=5
        )
        
        # Build sections
        sections = [
            ReportSection(
                title="System Overview",
                content={
                    "total_sources": overview["total_sources"],
                    "total_signals": overview["total_signals"],
                    "completed_signals": overview["total_completed"],
                    "active_signals": overview["total_active"],
                    "system_tp_rate": f"{float(overview['system_tp_rate']) * 100:.1f}%",
                    "system_stop_loss_rate": f"{float(overview['system_stop_loss_rate']) * 100:.1f}%",
                    "system_total_profit": f"{overview['system_total_profit']:.2f}%",
                    "system_average_profit": f"{overview['system_average_profit']:.2f}%",
                },
                format_type="summary"
            ),
            
            ReportSection(
                title="Performance Distribution",
                content={
                    "elite_sources": len(tiers["elite"]),
                    "excellent_sources": len(tiers["excellent"]),
                    "good_sources": len(tiers["good"]),
                    "average_sources": len(tiers["average"]),
                    "below_average_sources": len(tiers["below_average"]),
                    "poor_sources": len(tiers["poor"]),
                },
                format_type="chart_data"
            ),
            
            ReportSection(
                title="Top 5 Performers",
                content={
                    "performers": [
                        {
                            "rank": p.rank,
                            "source_name": p.source_name or f"Source {p.source_id}",
                            "score": f"{p.score_breakdown.display_score:.2f}/10",
                            "tp_rate": f"{float(p.score_breakdown.tp_hit_rate) * 100:.1f}%",
                            "total_profit": f"{p.score_breakdown.total_profit:.2f}%",
                            "signal_count": p.score_breakdown.signal_count,
                        }
                        for p in top_performers
                    ]
                },
                format_type="table"
            ),
        ]
        
        summary = {
            "key_insights": self._generate_executive_insights(overview, tiers, top_performers),
            "recommendations": self._generate_executive_recommendations(overview, tiers),
        }
        
        return GeneratedReport(
            title="Executive Summary Report",
            generated_at=datetime.now(timezone.utc),
            time_window=time_window.name if time_window else "all-time",
            sections=sections,
            summary=summary,
        )

    async def generate_detailed_performance_report(
        self,
        time_window: TimeWindow | None = None,
    ) -> GeneratedReport:
        """
        Generate detailed performance analysis report.
        
        Comprehensive report with all metrics and analysis.
        """
        
        # Get comprehensive data
        overview = await self._analytics_stats.get_system_overview(time_window)
        distribution = await self._analytics_stats.get_performance_distribution(time_window)
        leaderboard = await self._analytics_ranking.get_score_leaderboard(
            RankingCriteria(metric="score", time_window=time_window)
        )
        metric_leaders = await self._analytics_ranking.get_metric_leaders(time_window)
        rising_stars = await self._analytics_ranking.get_rising_stars()
        
        sections = [
            ReportSection(
                title="System Metrics",
                content=overview,
                format_type="summary"
            ),
            
            ReportSection(
                title="Complete Rankings",
                content={
                    "rankings": [
                        {
                            "rank": source.rank,
                            "source_id": source.source_id,
                            "source_name": source.source_name or f"Source {source.source_id}",
                            "score": f"{source.score_breakdown.display_score:.2f}/10",
                            "tp_rate": f"{float(source.score_breakdown.tp_hit_rate) * 100:.1f}%",
                            "stop_loss_rate": f"{float(source.score_breakdown.stop_loss_rate) * 100:.1f}%",
                            "total_profit": f"{source.score_breakdown.total_profit:.2f}%",
                            "avg_profit": f"{source.score_breakdown.average_profit:.2f}%",
                            "best_profit": f"{source.score_breakdown.best_profit or Decimal('0'):.2f}%",
                            "signal_count": source.score_breakdown.signal_count,
                        }
                        for source in leaderboard
                    ]
                },
                format_type="table"
            ),
            
            ReportSection(
                title="Metric Leaders",
                content={
                    "leaders": {
                        metric: {
                            "source_name": leader.source_name or f"Source {leader.source_id}",
                            "score": f"{leader.score_breakdown.display_score:.2f}/10",
                            "value": self._get_metric_display_value(metric, leader.score_breakdown),
                        }
                        for metric, leader in metric_leaders.items()
                    }
                },
                format_type="summary"
            ),
            
            ReportSection(
                title="Rising Stars",
                content={
                    "stars": rising_stars[:10]  # Top 10 improving sources
                },
                format_type="table"
            ),
            
            ReportSection(
                title="Performance Distribution",
                content=distribution,
                format_type="chart_data"
            ),
        ]
        
        summary = {
            "total_sources_analyzed": len(leaderboard),
            "top_score": leaderboard[0].score_breakdown.display_score if leaderboard else 0,
            "average_system_score": sum(s.score_breakdown.score for s in leaderboard) / len(leaderboard) / 100 if leaderboard else 0,
            "improvement_candidates": len([s for s in leaderboard if s.score_breakdown.score < 600]),
        }
        
        return GeneratedReport(
            title="Detailed Performance Analysis",
            generated_at=datetime.now(timezone.utc),
            time_window=time_window.name if time_window else "all-time",
            sections=sections,
            summary=summary,
        )

    async def generate_source_spotlight_report(
        self,
        source_id: int,
        time_window: TimeWindow | None = None,
    ) -> GeneratedReport:
        """
        Generate detailed report focused on a specific signal source.
        
        Deep dive into individual source performance.
        """
        
        # Get source statistics and score breakdown
        from app.services.statistics import StatisticsService
        from app.services.scoring import ScoringService
        
        stats_service = StatisticsService(self._uow)
        scoring_service = ScoringService(stats_service)
        
        source_stats = await stats_service.get_source_statistics(source_id, time_window)
        score_breakdown = await scoring_service.calculate_source_score(source_id, time_window)
        
        # Get source info
        async with self._uow:
            source = await self._uow.signal_sources.get(source_id)
            source_name = source.name if source else f"Source {source_id}"
        
        # Get comparative ranking
        leaderboard = await self._analytics_ranking.get_score_leaderboard(
            RankingCriteria(metric="score", time_window=time_window)
        )
        source_rank = next(
            (s.rank for s in leaderboard if s.source_id == source_id),
            None
        )
        
        # Time series comparison
        time_series = await self._get_source_time_series(source_id)
        
        sections = [
            ReportSection(
                title="Source Information",
                content={
                    "source_name": source_name,
                    "source_id": source_id,
                    "current_rank": source_rank,
                    "total_sources": len(leaderboard),
                    "percentile": round((1 - (source_rank - 1) / len(leaderboard)) * 100, 1) if source_rank else None,
                },
                format_type="summary"
            ),
            
            ReportSection(
                title="Score Breakdown",
                content={
                    "overall_score": f"{score_breakdown.display_score:.2f}/10",
                    "components": {
                        "TP Hit Rate": f"{score_breakdown.tp_hit_rate_score:.3f} (30% weight)",
                        "Profitability": f"{score_breakdown.profitability_score:.3f} (25% weight)",
                        "Average Profit": f"{score_breakdown.average_profit_score:.3f} (15% weight)",
                        "Best Profit": f"{score_breakdown.best_profit_score:.3f} (10% weight)",
                        "Stop Loss Control": f"{score_breakdown.stop_loss_score:.3f} (10% weight)",
                        "Sample Confidence": f"{score_breakdown.confidence_score:.3f} (10% weight)",
                    },
                    "explanation": scoring_service.explain_score(score_breakdown),
                },
                format_type="summary"
            ),
            
            ReportSection(
                title="Performance Metrics",
                content={
                    "total_signals": source_stats.total_signals,
                    "completed_signals": source_stats.completed_signals,
                    "active_signals": source_stats.active_signals,
                    "tp_hit_rate": f"{float(source_stats.tp_hit_rate) * 100:.1f}%",
                    "stop_loss_rate": f"{float(source_stats.stop_loss_rate) * 100:.1f}%",
                    "total_profit": f"{source_stats.total_profit:.2f}%",
                    "average_profit": f"{source_stats.average_profit:.2f}%",
                    "best_profit": f"{source_stats.best_profit or Decimal('0'):.2f}%",
                    "worst_profit": f"{source_stats.worst_profit or Decimal('0'):.2f}%",
                    "profitable_signals": source_stats.profitable_signal_count,
                    "losing_signals": source_stats.losing_signal_count,
                },
                format_type="summary"
            ),
            
            ReportSection(
                title="Time Series Analysis",
                content=time_series,
                format_type="chart_data"
            ),
        ]
        
        summary = {
            "strengths": self._identify_source_strengths(score_breakdown, source_stats),
            "areas_for_improvement": self._identify_improvement_areas(score_breakdown, source_stats),
            "recommendation": self._generate_source_recommendation(score_breakdown, source_stats),
        }
        
        return GeneratedReport(
            title=f"Source Spotlight: {source_name}",
            generated_at=datetime.now(timezone.utc),
            time_window=time_window.name if time_window else "all-time",
            sections=sections,
            summary=summary,
        )

    async def generate_comparison_report(
        self,
        source_ids: List[int],
        time_window: TimeWindow | None = None,
    ) -> GeneratedReport:
        """Generate side-by-side comparison report for multiple sources."""
        
        comparison = await self._analytics_ranking.compare_sources(source_ids, time_window)
        
        # Get detailed statistics for each source
        from app.services.statistics import StatisticsService
        from app.services.scoring import ScoringService
        
        stats_service = StatisticsService(self._uow)
        scoring_service = ScoringService(stats_service)
        
        detailed_data = []
        for source_id in source_ids:
            stats = await stats_service.get_source_statistics(source_id, time_window)
            score = await scoring_service.calculate_source_score(source_id, time_window)
            
            # Get source name
            async with self._uow:
                source = await self._uow.signal_sources.get(source_id)
                source_name = source.name if source else f"Source {source_id}"
            
            detailed_data.append({
                "source_id": source_id,
                "source_name": source_name,
                "stats": stats,
                "score": score,
            })
        
        sections = [
            ReportSection(
                title="Score Comparison",
                content={
                    "sources": [
                        {
                            "name": data["source_name"],
                            "score": f"{data['score'].display_score:.2f}/10",
                            "rank": next(c.rank for c in comparison if c.source_id == data["source_id"]),
                        }
                        for data in detailed_data
                    ]
                },
                format_type="table"
            ),
            
            ReportSection(
                title="Detailed Metrics Comparison",
                content={
                    "comparison_table": [
                        {
                            "metric": "TP Hit Rate",
                            **{
                                data["source_name"]: f"{float(data['stats'].tp_hit_rate) * 100:.1f}%"
                                for data in detailed_data
                            }
                        },
                        {
                            "metric": "Stop Loss Rate", 
                            **{
                                data["source_name"]: f"{float(data['stats'].stop_loss_rate) * 100:.1f}%"
                                for data in detailed_data
                            }
                        },
                        {
                            "metric": "Total Profit",
                            **{
                                data["source_name"]: f"{data['stats'].total_profit:.2f}%"
                                for data in detailed_data
                            }
                        },
                        {
                            "metric": "Average Profit",
                            **{
                                data["source_name"]: f"{data['stats'].average_profit:.2f}%"
                                for data in detailed_data
                            }
                        },
                        {
                            "metric": "Signal Count",
                            **{
                                data["source_name"]: str(data['stats'].total_signals)
                                for data in detailed_data
                            }
                        },
                    ]
                },
                format_type="table"
            ),
        ]
        
        # Find winner for each category
        winners = self._identify_category_winners(detailed_data)
        
        summary = {
            "sources_compared": len(source_ids),
            "overall_winner": comparison[0].source_name if comparison else "None",
            "category_winners": winners,
        }
        
        return GeneratedReport(
            title="Source Comparison Analysis",
            generated_at=datetime.now(timezone.utc),
            time_window=time_window.name if time_window else "all-time",
            sections=sections,
            summary=summary,
        )

    def format_report_as_text(self, report: GeneratedReport) -> str:
        """Format a report as plain text for console or file output."""
        
        lines = []
        lines.append("=" * 80)
        lines.append(f"{report.title}")
        lines.append(f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append(f"Time Window: {report.time_window}")
        lines.append("=" * 80)
        lines.append("")
        
        for section in report.sections:
            lines.append(f"## {section.title}")
            lines.append("-" * 40)
            
            if section.format_type == "summary":
                for key, value in section.content.items():
                    lines.append(f"{key.replace('_', ' ').title()}: {value}")
            
            elif section.format_type == "table" and "rankings" in section.content:
                lines.append("Rank | Source | Score | TP Rate | Profit | Signals")
                lines.append("-" * 60)
                for item in section.content["rankings"][:10]:  # Top 10
                    lines.append(
                        f"{item['rank']:4d} | {item['source_name'][:15]:15s} | "
                        f"{item['score']:7s} | {item['tp_rate']:7s} | "
                        f"{item['total_profit']:8s} | {item['signal_count']:7d}"
                    )
            
            lines.append("")
        
        if report.summary:
            lines.append("## Summary")
            lines.append("-" * 40)
            for key, value in report.summary.items():
                if isinstance(value, list):
                    lines.append(f"{key.replace('_', ' ').title()}:")
                    for item in value:
                        lines.append(f"  • {item}")
                else:
                    lines.append(f"{key.replace('_', ' ').title()}: {value}")
        
        return "\n".join(lines)

    # Helper methods
    
    def _generate_executive_insights(self, overview, tiers, top_performers) -> List[str]:
        """Generate key insights for executive summary."""
        insights = []
        
        elite_count = len(tiers["elite"])
        poor_count = len(tiers["poor"])
        total_sources = overview["total_sources"]
        
        if elite_count > 0:
            insights.append(f"{elite_count} sources achieving elite performance (9.0+/10)")
        
        if poor_count > total_sources * 0.2:
            insights.append(f"{poor_count} sources underperforming (below 5.0/10) - {poor_count/total_sources*100:.0f}% of total")
        
        if overview["system_tp_rate"] > Decimal("0.7"):
            insights.append("Strong system-wide TP hit rate above 70%")
        
        if top_performers and top_performers[0].score_breakdown.signal_count < 10:
            insights.append("Top performer has limited signal history - monitor for consistency")
        
        return insights

    def _generate_executive_recommendations(self, overview, tiers) -> List[str]:
        """Generate recommendations for executive summary."""
        recommendations = []
        
        poor_count = len(tiers["poor"])
        total_sources = overview["total_sources"]
        
        if poor_count > 0:
            recommendations.append(f"Review {poor_count} underperforming sources for potential removal")
        
        if len(tiers["elite"]) < total_sources * 0.1:
            recommendations.append("Focus on identifying and promoting high-quality signal sources")
        
        if overview["system_tp_rate"] < Decimal("0.5"):
            recommendations.append("System TP rate below 50% - review signal quality and entry strategies")
        
        return recommendations

    def _get_metric_display_value(self, metric: str, breakdown: ScoreBreakdown) -> str:
        """Get formatted display value for a metric."""
        if metric == "tp_rate":
            return f"{float(breakdown.tp_hit_rate) * 100:.1f}%"
        elif metric == "profit":
            return f"{breakdown.total_profit:.2f}%"
        elif metric == "avg_profit":
            return f"{breakdown.average_profit:.2f}%"
        elif metric == "signal_count":
            return str(breakdown.signal_count)
        else:
            return f"{breakdown.display_score:.2f}/10"

    async def _get_source_time_series(self, source_id: int) -> Dict:
        """Get time series data for a specific source."""
        # Simplified implementation - would need historical scoring data
        windows = [
            TimeWindow.last_48h(),
            TimeWindow.last_7d(),
            TimeWindow.last_30d(),
            TimeWindow.all_time(),
        ]
        
        from app.services.statistics import StatisticsService
        stats_service = StatisticsService(self._uow)
        
        series_data = {}
        for window in windows:
            stats = await stats_service.get_source_statistics(source_id, window)
            series_data[window.name] = {
                "tp_rate": float(stats.tp_hit_rate),
                "total_profit": float(stats.total_profit),
                "signal_count": stats.total_signals,
            }
        
        return series_data

    def _identify_source_strengths(self, score_breakdown: ScoreBreakdown, stats: SignalStatistics) -> List[str]:
        """Identify strengths of a source."""
        strengths = []
        
        if score_breakdown.tp_hit_rate_score > 0.8:
            strengths.append("Excellent TP hit rate consistency")
        
        if score_breakdown.profitability_score > 0.8:
            strengths.append("Strong overall profitability")
        
        if score_breakdown.confidence_score >= 1.0:
            strengths.append("Large sample size provides statistical confidence")
        
        if score_breakdown.stop_loss_score > 0.9:
            strengths.append("Excellent risk management with low stop loss rate")
        
        return strengths

    def _identify_improvement_areas(self, score_breakdown: ScoreBreakdown, stats: SignalStatistics) -> List[str]:
        """Identify areas needing improvement."""
        areas = []
        
        if score_breakdown.tp_hit_rate_score < 0.5:
            areas.append("TP hit rate below expectations")
        
        if score_breakdown.profitability_score < 0.3:
            areas.append("Overall profitability needs improvement")
        
        if score_breakdown.confidence_score < 0.5:
            areas.append("Limited signal history - need more data for reliable assessment")
        
        if score_breakdown.stop_loss_score < 0.7:
            areas.append("High stop loss rate indicates poor risk management")
        
        return areas

    def _generate_source_recommendation(self, score_breakdown: ScoreBreakdown, stats: SignalStatistics) -> str:
        """Generate overall recommendation for a source."""
        score = score_breakdown.score
        
        if score >= 900:
            return "Elite performer - maintain current allocation and monitor for consistency"
        elif score >= 750:
            return "Strong performer - consider increased allocation"
        elif score >= 600:
            return "Average performer - monitor closely and optimize where possible"
        elif score >= 400:
            return "Below average - review performance and consider reducing allocation"
        else:
            return "Poor performer - recommend removal or significant review"

    def _identify_category_winners(self, detailed_data: List[Dict]) -> Dict[str, str]:
        """Identify the winner in each performance category."""
        if not detailed_data:
            return {}
        
        winners = {}
        
        # Overall score
        best_score = max(detailed_data, key=lambda x: x["score"].score)
        winners["overall_score"] = best_score["source_name"]
        
        # TP hit rate
        best_tp = max(detailed_data, key=lambda x: x["stats"].tp_hit_rate)
        winners["tp_hit_rate"] = best_tp["source_name"]
        
        # Total profit
        best_profit = max(detailed_data, key=lambda x: x["stats"].total_profit)
        winners["total_profit"] = best_profit["source_name"]
        
        # Average profit
        best_avg = max(detailed_data, key=lambda x: x["stats"].average_profit)
        winners["average_profit"] = best_avg["source_name"]
        
        # Signal volume
        most_signals = max(detailed_data, key=lambda x: x["stats"].total_signals)
        winners["signal_volume"] = most_signals["source_name"]
        
        return winners