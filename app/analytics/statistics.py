"""
Analytics statistics module.

This module provides high-level statistical analysis for the trading system,
consuming the statistics service to provide analytical insights.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List

from app.core.dto import SignalStatistics, TimeWindow
from app.services.statistics import StatisticsService
from app.database.uow import UnitOfWork


class AnalyticsStatistics:
    """High-level statistical analysis for analytics purposes."""
    
    def __init__(self, uow: UnitOfWork) -> None:
        self._statistics_service = StatisticsService(uow)
        self._uow = uow

    async def get_system_overview(self, time_window: TimeWindow | None = None) -> Dict:
        """Get system-wide statistics overview."""
        
        all_stats = await self._statistics_service.get_all_sources_statistics(time_window)
        
        if not all_stats:
            return {
                "total_sources": 0,
                "total_signals": 0,
                "total_completed": 0,
                "total_active": 0,
                "system_tp_rate": Decimal("0.0000"),
                "system_stop_loss_rate": Decimal("0.0000"),
                "system_total_profit": Decimal("0.0000"),
                "system_average_profit": Decimal("0.0000"),
                "best_performing_source": None,
                "worst_performing_source": None,
            }
        
        # Aggregate system-wide metrics
        total_signals = sum(stats.total_signals for stats in all_stats.values())
        total_completed = sum(stats.completed_signals for stats in all_stats.values())
        total_active = sum(stats.active_signals for stats in all_stats.values())
        total_tp_hits = sum(stats.tp_hit_count for stats in all_stats.values())
        total_stop_losses = sum(stats.stop_loss_count for stats in all_stats.values())
        
        # Calculate system rates
        system_tp_rate = (
            Decimal(total_tp_hits) / Decimal(total_completed)
            if total_completed > 0 else Decimal("0.0000")
        )
        system_stop_loss_rate = (
            Decimal(total_stop_losses) / Decimal(total_completed)
            if total_completed > 0 else Decimal("0.0000")
        )
        
        # Calculate system profit metrics
        system_total_profit = sum(stats.total_profit for stats in all_stats.values())
        completed_signals_with_profit = sum(
            stats.completed_signals for stats in all_stats.values() 
            if stats.completed_signals > 0
        )
        system_average_profit = (
            system_total_profit / Decimal(completed_signals_with_profit)
            if completed_signals_with_profit > 0 else Decimal("0.0000")
        )
        
        # Find best and worst performing sources by TP rate
        best_source = max(
            all_stats.items(),
            key=lambda x: x[1].tp_hit_rate,
            default=(None, None)
        )
        worst_source = min(
            all_stats.items(),
            key=lambda x: x[1].tp_hit_rate,
            default=(None, None)
        )
        
        return {
            "total_sources": len(all_stats),
            "total_signals": total_signals,
            "total_completed": total_completed,
            "total_active": total_active,
            "system_tp_rate": system_tp_rate,
            "system_stop_loss_rate": system_stop_loss_rate,
            "system_total_profit": system_total_profit,
            "system_average_profit": system_average_profit,
            "best_performing_source": best_source[0] if best_source[1] else None,
            "worst_performing_source": worst_source[0] if worst_source[1] else None,
        }

    async def get_performance_distribution(
        self, 
        time_window: TimeWindow | None = None
    ) -> Dict:
        """Get distribution of performance metrics across sources."""
        
        all_stats = await self._statistics_service.get_all_sources_statistics(time_window)
        
        if not all_stats:
            return {
                "tp_rate_distribution": [],
                "profit_distribution": [],
                "signal_count_distribution": [],
                "quartiles": {},
            }
        
        # Extract metrics for distribution analysis
        tp_rates = [float(stats.tp_hit_rate) for stats in all_stats.values()]
        total_profits = [float(stats.total_profit) for stats in all_stats.values()]
        signal_counts = [stats.total_signals for stats in all_stats.values()]
        
        # Calculate quartiles for TP rates
        tp_rates_sorted = sorted(tp_rates)
        n = len(tp_rates_sorted)
        
        quartiles = {}
        if n > 0:
            quartiles["tp_rate"] = {
                "q1": tp_rates_sorted[n // 4] if n >= 4 else tp_rates_sorted[0],
                "q2": tp_rates_sorted[n // 2],
                "q3": tp_rates_sorted[3 * n // 4] if n >= 4 else tp_rates_sorted[-1],
            }
        
        # Create distribution buckets
        tp_rate_buckets = self._create_distribution_buckets(tp_rates, 10)
        profit_buckets = self._create_distribution_buckets(total_profits, 10)
        signal_count_buckets = self._create_distribution_buckets(signal_counts, 5)
        
        return {
            "tp_rate_distribution": tp_rate_buckets,
            "profit_distribution": profit_buckets,
            "signal_count_distribution": signal_count_buckets,
            "quartiles": quartiles,
        }

    async def get_time_series_analysis(self) -> Dict:
        """Get performance trends over different time windows."""
        
        windows = [
            TimeWindow.last_48h(),
            TimeWindow.last_7d(),
            TimeWindow.last_30d(),
            TimeWindow.all_time(),
        ]
        
        time_series = {}
        
        for window in windows:
            overview = await self.get_system_overview(window)
            time_series[window.name] = {
                "tp_rate": overview["system_tp_rate"],
                "stop_loss_rate": overview["system_stop_loss_rate"],
                "total_profit": overview["system_total_profit"],
                "average_profit": overview["system_average_profit"],
                "total_signals": overview["total_signals"],
                "completed_signals": overview["total_completed"],
            }
        
        return time_series

    async def get_source_comparison(
        self, 
        source_ids: List[int],
        time_window: TimeWindow | None = None
    ) -> Dict[int, SignalStatistics]:
        """Compare specific sources side by side."""
        
        comparison = {}
        for source_id in source_ids:
            stats = await self._statistics_service.get_source_statistics(source_id, time_window)
            comparison[source_id] = stats
        
        return comparison

    async def get_top_performers(
        self, 
        metric: str = "tp_hit_rate",
        limit: int = 10,
        time_window: TimeWindow | None = None
    ) -> List[tuple[int, SignalStatistics]]:
        """Get top performing sources by specified metric."""
        
        all_stats = await self._statistics_service.get_all_sources_statistics(time_window)
        
        # Sort by specified metric
        sort_key_map = {
            "tp_hit_rate": lambda x: x[1].tp_hit_rate,
            "total_profit": lambda x: x[1].total_profit,
            "average_profit": lambda x: x[1].average_profit,
            "signal_count": lambda x: x[1].total_signals,
        }
        
        sort_key = sort_key_map.get(metric, sort_key_map["tp_hit_rate"])
        
        sorted_sources = sorted(
            all_stats.items(),
            key=sort_key,
            reverse=True
        )
        
        return sorted_sources[:limit]

    async def calculate_correlation_matrix(
        self, 
        time_window: TimeWindow | None = None
    ) -> Dict:
        """Calculate correlations between different performance metrics."""
        
        all_stats = await self._statistics_service.get_all_sources_statistics(time_window)
        
        if len(all_stats) < 2:
            return {"error": "Insufficient data for correlation analysis"}
        
        # Extract metrics
        metrics = {}
        for source_id, stats in all_stats.items():
            metrics[source_id] = {
                "tp_rate": float(stats.tp_hit_rate),
                "stop_loss_rate": float(stats.stop_loss_rate),
                "total_profit": float(stats.total_profit),
                "average_profit": float(stats.average_profit),
                "signal_count": stats.total_signals,
            }
        
        # Simple correlation calculation (Pearson)
        correlations = {}
        metric_names = ["tp_rate", "stop_loss_rate", "total_profit", "average_profit", "signal_count"]
        
        for i, metric1 in enumerate(metric_names):
            correlations[metric1] = {}
            for metric2 in metric_names:
                values1 = [metrics[sid][metric1] for sid in metrics.keys()]
                values2 = [metrics[sid][metric2] for sid in metrics.keys()]
                
                correlation = self._calculate_pearson_correlation(values1, values2)
                correlations[metric1][metric2] = correlation
        
        return correlations

    def _create_distribution_buckets(
        self, 
        values: List[float], 
        bucket_count: int
    ) -> List[Dict]:
        """Create distribution buckets for histogram-style analysis."""
        
        if not values:
            return []
        
        min_val = min(values)
        max_val = max(values)
        
        if min_val == max_val:
            return [{"range": f"{min_val:.3f}", "count": len(values)}]
        
        bucket_size = (max_val - min_val) / bucket_count
        buckets = []
        
        for i in range(bucket_count):
            bucket_min = min_val + i * bucket_size
            bucket_max = min_val + (i + 1) * bucket_size
            
            count = sum(
                1 for v in values 
                if bucket_min <= v < bucket_max or (i == bucket_count - 1 and v == bucket_max)
            )
            
            buckets.append({
                "range": f"{bucket_min:.3f}-{bucket_max:.3f}",
                "count": count,
            })
        
        return buckets

    def _calculate_pearson_correlation(self, x: List[float], y: List[float]) -> float:
        """Calculate Pearson correlation coefficient."""
        
        if len(x) != len(y) or len(x) < 2:
            return 0.0
        
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        sum_y2 = sum(yi * yi for yi in y)
        
        numerator = n * sum_xy - sum_x * sum_y
        denominator_x = n * sum_x2 - sum_x * sum_x
        denominator_y = n * sum_y2 - sum_y * sum_y
        
        if denominator_x <= 0 or denominator_y <= 0:
            return 0.0
        
        denominator = (denominator_x * denominator_y) ** 0.5
        
        if denominator == 0:
            return 0.0
        
        return numerator / denominator