"""
Scoring service for calculating signal source quality scores.

Implements a 0-1000 point scoring system with the following weighted components:
- TP Hit Rate: 30%
- Profitability: 25% 
- Average Profit per Signal: 15%
- Best Single-Signal Profit: 10%
- Stop-Loss Rate: 10% (inverted - lower is better)
- Sample Size Confidence: 10%

Final score is displayed as score/100 (e.g., 891 -> 8.91/10).
"""

import statistics
from decimal import Decimal
from typing import Dict, List

from app.core.dto import SignalStatistics, ScoreBreakdown, TimeWindow
from app.services.statistics import StatisticsService
from app.services.validation import ScoringValidator, ScoringValidationError


class ScoringService:
    """Service for calculating signal source quality scores."""
    
    def __init__(self, statistics_service: StatisticsService) -> None:
        self._statistics = statistics_service
        self._validator = ScoringValidator()

    async def calculate_source_score(
        self,
        source_id: int,
        time_window: TimeWindow | None = None,
    ) -> ScoreBreakdown:
        """
        Calculate comprehensive quality score for a signal source.
        
        Returns a ScoreBreakdown with the final score (0-1000) and
        detailed component breakdown for transparency.
        """
        
        # Validate inputs
        source_id = self._validator.validate_source_id(source_id)
        time_window = self._validator.validate_time_window(time_window)
        
        try:
            # Get source statistics
            source_stats = await self._statistics.get_source_statistics(source_id, time_window)
            
            # Handle new/minimal data sources
            if source_stats.total_signals == 0:
                return self._validator.handle_new_source_scoring(source_id, 0)
            
            early_stage_score = self._validator.handle_new_source_scoring(source_id, source_stats.total_signals)
            if early_stage_score is not None:
                return self._validator.validate_score_breakdown(early_stage_score)
            
            # Get population data for percentile normalization
            percentiles = await self._statistics.get_profit_percentiles(time_window)
            
            # Calculate normalized component scores (0.0 - 1.0)
            tp_hit_rate_score = float(source_stats.tp_hit_rate)
            
            profitability_score = self._calculate_percentile_score_safe(
                source_stats.total_profit,
                percentiles["total_profits"]
            )
            
            average_profit_score = self._calculate_percentile_score_safe(
                source_stats.average_profit,
                percentiles["average_profits"]
            )
            
            best_profit_score = self._calculate_percentile_score_safe(
                source_stats.best_profit or Decimal("0"),
                percentiles["best_profits"]
            )
            
            stop_loss_score = 1.0 - float(source_stats.stop_loss_rate)
            
            confidence_score = await self._statistics.calculate_confidence_score(
                source_stats.total_signals
            )
            
            # Apply weights and calculate raw score
            raw_score = (
                0.30 * tp_hit_rate_score +
                0.25 * profitability_score +
                0.15 * average_profit_score +
                0.10 * best_profit_score +
                0.10 * stop_loss_score +
                0.10 * confidence_score
            )
            
            # Convert to 0-1000 scale and clamp
            score = self._clamp(round(1000 * raw_score), 0, 1000)
            display_score = score / 100.0
            
            breakdown = ScoreBreakdown(
                score=score,
                display_score=display_score,
                tp_hit_rate_score=tp_hit_rate_score,
                profitability_score=profitability_score,
                average_profit_score=average_profit_score,
                best_profit_score=best_profit_score,
                stop_loss_score=stop_loss_score,
                confidence_score=confidence_score,
                # Raw values for debugging
                tp_hit_rate=source_stats.tp_hit_rate,
                stop_loss_rate=source_stats.stop_loss_rate,
                total_profit=source_stats.total_profit,
                average_profit=source_stats.average_profit,
                best_profit=source_stats.best_profit,
                signal_count=source_stats.total_signals,
            )
            
            return self._validator.validate_score_breakdown(breakdown)
            
        except Exception as e:
            if isinstance(e, ScoringValidationError):
                raise
            
            # Handle unexpected errors gracefully
            raise ScoringValidationError(f"Failed to calculate score for source {source_id}: {str(e)}")

    async def calculate_all_scores(
        self,
        time_window: TimeWindow | None = None,
    ) -> Dict[int, ScoreBreakdown]:
        """Calculate scores for all active signal sources."""
        
        all_stats = await self._statistics.get_all_sources_statistics(time_window)
        
        scores = {}
        for source_id in all_stats.keys():
            score_breakdown = await self.calculate_source_score(source_id, time_window)
            scores[source_id] = score_breakdown
        
        return scores

    def _calculate_percentile_score_safe(
        self,
        value: Decimal,
        population: List[Decimal],
    ) -> float:
        """
        Calculate percentile-based score with edge case handling.
        """
        # Handle empty or insufficient population
        safe_result = self._validator.handle_empty_profit_population(value, population)
        if safe_result is not None:
            return safe_result
        
        # Proceed with normal calculation
        return self._calculate_percentile_score(value, population)

    def _calculate_percentile_score(
        self,
        value: Decimal,
        population: List[Decimal],
    ) -> float:
        """
        Calculate percentile-based score for robust normalization.
        
        Uses the percentile rank of the value within the population
        to avoid outlier distortion. Returns 0.0 - 1.0.
        """
        if not population or value is None:
            return 0.0
        
        # Handle edge cases
        if len(population) == 1:
            return 1.0 if value >= population[0] else 0.0
        
        # Convert to float for statistics module
        float_value = float(value)
        float_population = [float(p) for p in population]
        
        try:
            # Calculate percentile rank (0-100)
            percentile_rank = self._percentile_rank(float_value, float_population)
            # Convert to 0.0-1.0 range
            return percentile_rank / 100.0
        except (ValueError, ZeroDivisionError):
            # Fallback for edge cases
            return 0.0

    def _percentile_rank(self, value: float, population: List[float]) -> float:
        """
        Calculate the percentile rank of a value within a population.
        
        Returns a value between 0 and 100.
        """
        if not population:
            return 0.0
        
        # Sort the population
        sorted_pop = sorted(population)
        
        # Count values less than the target
        less_than = sum(1 for x in sorted_pop if x < value)
        
        # Count values equal to the target  
        equal_to = sum(1 for x in sorted_pop if x == value)
        
        # Calculate percentile rank
        if len(population) == 1:
            return 100.0 if equal_to > 0 else 0.0
        
        percentile = (less_than + 0.5 * equal_to) / len(population) * 100.0
        
        return max(0.0, min(100.0, percentile))

    def _clamp(self, value: int, min_val: int, max_val: int) -> int:
        """Clamp value between min and max inclusive."""
        return max(min_val, min(max_val, value))

    def convert_to_display_score(self, score: int) -> float:
        """Convert internal score (0-1000) to display score (0.00-10.00)."""
        return round(score / 100.0, 2)

    def convert_from_display_score(self, display_score: float) -> int:
        """Convert display score (0.00-10.00) to internal score (0-1000)."""
        return self._clamp(round(display_score * 100), 0, 1000)

    async def update_source_score(
        self,
        source_id: int,
        time_window: TimeWindow | None = None,
    ) -> int:
        """
        Update the stored score for a signal source.
        
        Returns the new score (0-1000).
        """
        score_breakdown = await self.calculate_source_score(source_id, time_window)
        
        # Update the SignalSource model with the new score
        # This would typically be done through the repository/UoW pattern
        # Implementation depends on how scores are persisted
        
        return score_breakdown.score

    def explain_score(self, breakdown: ScoreBreakdown) -> str:
        """
        Generate human-readable explanation of how the score was calculated.
        
        Useful for debugging and transparency.
        """
        lines = [
            f"Score: {breakdown.display_score:.2f}/10 ({breakdown.score}/1000)",
            "",
            "Component Breakdown:",
            f"  TP Hit Rate: {breakdown.tp_hit_rate_score:.3f} × 30% = {breakdown.tp_hit_rate_score * 0.30:.3f}",
            f"    (Raw: {float(breakdown.tp_hit_rate):.3f} = {float(breakdown.tp_hit_rate) * 100:.1f}%)",
            "",
            f"  Profitability: {breakdown.profitability_score:.3f} × 25% = {breakdown.profitability_score * 0.25:.3f}",
            f"    (Total profit: {breakdown.total_profit:.4f}%)",
            "",
            f"  Avg Profit: {breakdown.average_profit_score:.3f} × 15% = {breakdown.average_profit_score * 0.15:.3f}",
            f"    (Raw: {breakdown.average_profit:.4f}%)",
            "",
            f"  Best Profit: {breakdown.best_profit_score:.3f} × 10% = {breakdown.best_profit_score * 0.10:.3f}",
            f"    (Raw: {breakdown.best_profit or Decimal('0'):.4f}%)",
            "",
            f"  Stop Loss: {breakdown.stop_loss_score:.3f} × 10% = {breakdown.stop_loss_score * 0.10:.3f}",
            f"    (SL Rate: {float(breakdown.stop_loss_rate):.3f} = {float(breakdown.stop_loss_rate) * 100:.1f}%)",
            "",
            f"  Confidence: {breakdown.confidence_score:.3f} × 10% = {breakdown.confidence_score * 0.10:.3f}",
            f"    (Signal count: {breakdown.signal_count})",
        ]
        
        return "\n".join(lines)