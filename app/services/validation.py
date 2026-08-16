from loguru import logger
from decimal import Decimal
from typing import List
from app.core.dto import ParsedSignal, ValidatedSignal, SignalStatistics, ScoreBreakdown, TimeWindow
from app.database.enums import Direction
from app.database.uow import UnitOfWork
from app.market.symbol_registry import OurbitRegistry


class ScoringValidationError(Exception):
    """Raised when scoring validation fails."""
    pass


class ValidationService:
    def __init__(
        self,
        registry: OurbitRegistry,
    ) -> None:
        self._registry = registry

    async def validate(
        self,
        signal: ParsedSignal,
        uow: UnitOfWork,
    ) -> ValidatedSignal | None:
        if not self._validate_structure(signal):
            logger.trace("signal structure is not valid.")
            return None

        # Check if stop loss has issues and needs to be recalculated
        if not self._is_stop_loss_valid(signal):
            logger.info("Stop loss has issues, calculating automatically...")
            signal = self._calculate_stop_loss(signal)

        if not self._validate_prices(signal):
            logger.trace("signal prices is not valid.")
            return None

        if not self._registry.contains(signal.symbol):
            logger.trace("Symbol is not in Ourbit Registry!")
            return None

        return ValidatedSignal(
            symbol=signal.symbol,
            direction=signal.direction,
            leverage=signal.leverage,
            entries=signal.entries,
            targets=signal.targets,
            stop_loss=signal.stop_loss,
        )

    def _validate_structure(
        self,
        signal: ParsedSignal,
    ) -> bool:
        if signal.leverage < 1 or signal.leverage > 100:
            return False

        if not signal.entries:
            return False

        if not signal.targets:
            return False

        entries = [entry.price for entry in signal.entries]
        targets = [target.price for target in signal.targets]

        if len(set(entries)) != len(entries):
            return False

        if len(set(targets)) != len(targets):
            return False

        if any(price <= 0 for price in entries):
            return False

        if any(price <= 0 for price in targets):
            return False

        if signal.stop_loss <= 0:
            return False

        return True

    def _is_stop_loss_valid(self, signal: ParsedSignal) -> bool:
        """Check if the stop loss is valid according to direction and price relationships."""
        if signal.stop_loss <= 0:
            return False

        entries = [entry.price for entry in signal.entries]
        sl = signal.stop_loss

        if signal.direction is Direction.LONG:
            # For LONG: stop loss should be below entries
            if sl >= min(entries):
                return False
        else:
            # For SHORT: stop loss should be above entries
            if sl <= max(entries):
                return False

        return True

    def _calculate_stop_loss(self, signal: ParsedSignal) -> ParsedSignal:
        """Calculate stop loss as 3 times the difference between entry1 and tp1."""
        if not signal.entries or not signal.targets:
            logger.warning("Cannot calculate stop loss: missing entries or targets")
            return signal

        entry1_price = signal.entries[0].price  # First entry
        tp1_price = signal.targets[0].price     # First target

        # Calculate the difference between entry1 and tp1
        entry_tp_diff = abs(tp1_price - entry1_price)

        # Calculate stop loss as 3 times this difference
        stop_loss_distance = entry_tp_diff * 3

        if signal.direction is Direction.LONG:
            # For LONG: stop loss is below entry1
            calculated_sl = entry1_price - stop_loss_distance
        else:
            # For SHORT: stop loss is above entry1
            calculated_sl = entry1_price + stop_loss_distance

        # Ensure stop loss is positive
        calculated_sl = max(Decimal('0.00000001'), calculated_sl)

        logger.info(
            f"Calculated stop loss for {signal.symbol}: "
            f"Entry1={entry1_price}, TP1={tp1_price}, "
            f"Diff={entry_tp_diff}, SL={calculated_sl}"
        )

        # Create a new ParsedSignal with the calculated stop loss
        return ParsedSignal(
            symbol=signal.symbol,
            direction=signal.direction,
            leverage=signal.leverage,
            entries=signal.entries,
            targets=signal.targets,
            stop_loss=calculated_sl,
        )

    def _validate_prices(
        self,
        signal: ParsedSignal,
    ) -> bool:
        entries = [entry.price for entry in signal.entries]
        targets = [target.price for target in signal.targets]
        sl = signal.stop_loss

        ascending = all(
            previous < current
            for previous, current in zip(
                targets,
                targets[1:],
            )
        )

        descending = all(
            previous > current
            for previous, current in zip(
                targets,
                targets[1:],
            )
        )

        if signal.direction is Direction.LONG:
            if not ascending:
                return False

            if sl >= min(entries):
                return False

            if min(targets) <= max(entries):
                return False

        else:
            if not descending:
                return False

            if sl <= max(entries):
                return False

            if max(targets) >= min(entries):
                return False

        return True


class ScoringValidator:
    """Validates scoring inputs and handles edge cases."""

    def validate_source_statistics(self, stats: SignalStatistics) -> SignalStatistics:
        """
        Validate and sanitize source statistics.

        Handles edge cases like negative values, division by zero, etc.
        """
        # Ensure non-negative counts
        stats = self._sanitize_counts(stats)

        # Validate rates are between 0 and 1
        stats = self._sanitize_rates(stats)

        # Validate profit values
        stats = self._sanitize_profits(stats)

        # Ensure logical consistency
        self._validate_consistency(stats)

        return stats

    def validate_score_breakdown(self, breakdown: ScoreBreakdown) -> ScoreBreakdown:
        """
        Validate and sanitize score breakdown.

        Ensures all component scores are in valid ranges.
        """
        # Clamp component scores to 0.0-1.0
        breakdown = ScoreBreakdown(
            score=max(0, min(1000, breakdown.score)),
            display_score=max(0.0, min(10.0, breakdown.display_score)),
            tp_hit_rate_score=max(0.0, min(1.0, breakdown.tp_hit_rate_score)),
            profitability_score=max(0.0, min(1.0, breakdown.profitability_score)),
            average_profit_score=max(0.0, min(1.0, breakdown.average_profit_score)),
            best_profit_score=max(0.0, min(1.0, breakdown.best_profit_score)),
            stop_loss_score=max(0.0, min(1.0, breakdown.stop_loss_score)),
            confidence_score=max(0.0, min(1.0, breakdown.confidence_score)),
            # Keep raw values as-is for debugging
            tp_hit_rate=breakdown.tp_hit_rate,
            stop_loss_rate=breakdown.stop_loss_rate,
            total_profit=breakdown.total_profit,
            average_profit=breakdown.average_profit,
            best_profit=breakdown.best_profit,
            signal_count=breakdown.signal_count,
        )

        return breakdown

    def handle_new_source_scoring(self, source_id: int, signal_count: int) -> ScoreBreakdown:
        """
        Handle scoring for brand new sources with minimal or no data.

        New sources should NOT get artificially high scores.
        """
        if signal_count == 0:
            return ScoreBreakdown(
                score=0,
                display_score=0.0,
                tp_hit_rate_score=0.0,
                profitability_score=0.0,
                average_profit_score=0.0,
                best_profit_score=0.0,
                stop_loss_score=0.0,
                confidence_score=0.0,
                tp_hit_rate=Decimal('0.0000'),
                stop_loss_rate=Decimal('0.0000'),
                total_profit=Decimal('0.0000'),
                average_profit=Decimal('0.0000'),
                best_profit=None,
                signal_count=0,
            )

        # For very low signal counts, apply conservative scoring
        if signal_count < 5:
            # Cap the maximum possible score for low sample sizes
            max_possible_score = min(500, signal_count * 100)  # Very conservative

            return ScoreBreakdown(
                score=max_possible_score // 2,  # Even more conservative
                display_score=(max_possible_score // 2) / 100.0,
                tp_hit_rate_score=0.5,  # Neutral assumption
                profitability_score=0.3,  # Conservative assumption
                average_profit_score=0.3,
                best_profit_score=0.2,
                stop_loss_score=0.7,  # Assume reasonable risk management
                confidence_score=0.1,  # Very low confidence
                tp_hit_rate=Decimal('0.5000'),
                stop_loss_rate=Decimal('0.3000'),
                total_profit=Decimal('0.0000'),
                average_profit=Decimal('0.0000'),
                best_profit=Decimal('0.0000'),
                signal_count=signal_count,
            )

        # For moderate sample sizes (5-25), allow normal scoring but cap confidence
        return None  # Let normal scoring proceed, but confidence will be naturally low

    def handle_division_by_zero_cases(self, numerator: Decimal, denominator: int) -> Decimal:
        """
        Safely handle division by zero cases.

        Returns appropriate default values for rates when denominator is zero.
        """
        if denominator == 0:
            return Decimal('0.0000')

        return numerator / Decimal(denominator)

    def handle_empty_profit_population(
        self,
        value: Decimal,
        population: List[Decimal]
    ) -> float:
        """
        Handle percentile calculation when population is empty or insufficient.

        Returns conservative scores for edge cases.
        """
        if not population:
            return 0.0

        if len(population) == 1:
            return 1.0 if value >= population[0] else 0.0

        # If population is very small, be conservative
        if len(population) < 3:
            return 0.5  # Neutral score

        return None  # Proceed with normal percentile calculation

    def validate_time_window(self, time_window: TimeWindow | None) -> TimeWindow | None:
        """Validate time window parameters."""
        if time_window is None:
            return None

        if time_window.hours is not None:
            if time_window.hours <= 0:
                raise ScoringValidationError(f"Invalid time window: {time_window.hours} hours")

            if time_window.hours > 365 * 24:  # More than a year
                raise ScoringValidationError(f"Time window too large: {time_window.hours} hours")

        return time_window

    def validate_source_id(self, source_id: int) -> int:
        """Validate source ID parameter."""
        if not isinstance(source_id, int) or source_id <= 0:
            raise ScoringValidationError(f"Invalid source ID: {source_id}")

        return source_id

    def validate_score_value(self, score: int) -> int:
        """Validate score value is in valid range."""
        if not isinstance(score, int):
            raise ScoringValidationError(f"Score must be integer, got {type(score)}")

        if score < 0 or score > 1000:
            raise ScoringValidationError(f"Score {score} outside valid range 0-1000")

        return score

    def check_data_quality_warnings(self, stats: SignalStatistics) -> List[str]:
        """
        Check for data quality issues and return warnings.

        Helps identify potential problems with source data.
        """
        warnings = []

        # Check for very low sample sizes
        if stats.total_signals < 10:
            warnings.append(f"Low sample size: only {stats.total_signals} signals")

        # Check for no completed signals
        if stats.completed_signals == 0:
            warnings.append("No completed signals - cannot calculate meaningful statistics")

        # Check for extreme TP hit rates
        if stats.completed_signals > 0:
            tp_rate = float(stats.tp_hit_rate)
            if tp_rate > 0.95:
                warnings.append(f"Suspiciously high TP rate: {tp_rate:.1%}")
            elif tp_rate < 0.05:
                warnings.append(f"Very low TP rate: {tp_rate:.1%}")

        # Check for extreme stop loss rates
        if stats.completed_signals > 0:
            sl_rate = float(stats.stop_loss_rate)
            if sl_rate > 0.90:
                warnings.append(f"Very high stop loss rate: {sl_rate:.1%}")

        # Check for extreme profit values
        if stats.best_profit and float(stats.best_profit) > 1000:
            warnings.append(f"Extreme best profit: {stats.best_profit:.2f}%")

        if stats.worst_profit and float(stats.worst_profit) < -100:
            warnings.append(f"Extreme worst loss: {stats.worst_profit:.2f}%")

        # Check for inconsistent counts
        if stats.tp_hit_count + stats.stop_loss_count > stats.completed_signals:
            warnings.append("Inconsistent signal counts - data integrity issue")

        return warnings

    def _sanitize_counts(self, stats: SignalStatistics) -> SignalStatistics:
        """Ensure all counts are non-negative integers and maintain logical consistency."""
        # First, sanitize all counts to be non-negative
        total_signals = max(0, stats.total_signals)
        completed_signals = max(0, stats.completed_signals)
        active_signals = max(0, stats.active_signals)
        tp_hit_count = max(0, stats.tp_hit_count)
        stop_loss_count = max(0, stats.stop_loss_count)
        cancelled_count = max(0, stats.cancelled_count)
        expired_count = max(0, stats.expired_count)
        profitable_signal_count = max(0, stats.profitable_signal_count)
        losing_signal_count = max(0, stats.losing_signal_count)

        # Special case: if both total_signals and completed_signals were negative and became 0,
        # we should zero out all dependent counts to maintain consistency
        if (stats.total_signals < 0 and stats.completed_signals < 0 and
            total_signals == 0 and completed_signals == 0):
            active_signals = 0
            tp_hit_count = 0
            stop_loss_count = 0
            profitable_signal_count = 0
            losing_signal_count = 0

        return SignalStatistics(
            source_id=stats.source_id,
            total_signals=total_signals,
            completed_signals=completed_signals,
            active_signals=active_signals,
            tp_hit_count=tp_hit_count,
            stop_loss_count=stop_loss_count,
            cancelled_count=cancelled_count,
            expired_count=expired_count,
            tp_hit_rate=stats.tp_hit_rate,
            stop_loss_rate=stats.stop_loss_rate,
            total_profit=stats.total_profit,
            average_profit=stats.average_profit,
            best_profit=stats.best_profit,
            worst_profit=stats.worst_profit,
            profitable_signal_count=profitable_signal_count,
            losing_signal_count=losing_signal_count,
        )

    def _sanitize_rates(self, stats: SignalStatistics) -> SignalStatistics:
        """Ensure rates are between 0 and 1."""
        tp_rate = max(Decimal('0.0000'), min(Decimal('1.0000'), stats.tp_hit_rate))
        sl_rate = max(Decimal('0.0000'), min(Decimal('1.0000'), stats.stop_loss_rate))

        return SignalStatistics(
            source_id=stats.source_id,
            total_signals=stats.total_signals,
            completed_signals=stats.completed_signals,
            active_signals=stats.active_signals,
            tp_hit_count=stats.tp_hit_count,
            stop_loss_count=stats.stop_loss_count,
            cancelled_count=stats.cancelled_count,
            expired_count=stats.expired_count,
            tp_hit_rate=tp_rate,
            stop_loss_rate=sl_rate,
            total_profit=stats.total_profit,
            average_profit=stats.average_profit,
            best_profit=stats.best_profit,
            worst_profit=stats.worst_profit,
            profitable_signal_count=stats.profitable_signal_count,
            losing_signal_count=stats.losing_signal_count,
        )

    def _sanitize_profits(self, stats: SignalStatistics) -> SignalStatistics:
        """Sanitize profit values to reasonable ranges."""
        # Cap extreme profit values to prevent outlier distortion
        MAX_PROFIT = Decimal('10000.0000')  # 100x or 10,000%
        MIN_PROFIT = Decimal('-100.0000')   # -100% (total loss)

        total_profit = max(MIN_PROFIT * 100, min(MAX_PROFIT * 100, stats.total_profit))
        avg_profit = max(MIN_PROFIT, min(MAX_PROFIT, stats.average_profit))

        best_profit = stats.best_profit
        if best_profit is not None:
            best_profit = max(MIN_PROFIT, min(MAX_PROFIT, best_profit))

        worst_profit = stats.worst_profit
        if worst_profit is not None:
            worst_profit = max(MIN_PROFIT, min(MAX_PROFIT, worst_profit))

        return SignalStatistics(
            source_id=stats.source_id,
            total_signals=stats.total_signals,
            completed_signals=stats.completed_signals,
            active_signals=stats.active_signals,
            tp_hit_count=stats.tp_hit_count,
            stop_loss_count=stats.stop_loss_count,
            cancelled_count=stats.cancelled_count,
            expired_count=stats.expired_count,
            tp_hit_rate=stats.tp_hit_rate,
            stop_loss_rate=stats.stop_loss_rate,
            total_profit=total_profit,
            average_profit=avg_profit,
            best_profit=best_profit,
            worst_profit=worst_profit,
            profitable_signal_count=stats.profitable_signal_count,
            losing_signal_count=stats.losing_signal_count,
        )

    def _validate_consistency(self, stats: SignalStatistics) -> None:
        """Validate logical consistency between statistics."""
        # Total signals should be >= completed + active
        if stats.total_signals < stats.completed_signals + stats.active_signals:
            raise ScoringValidationError(
                f"Inconsistent signal counts: total={stats.total_signals}, "
                f"completed={stats.completed_signals}, active={stats.active_signals}"
            )

        # TP hits + stop losses should not exceed completed signals
        if stats.tp_hit_count + stats.stop_loss_count > stats.completed_signals:
            raise ScoringValidationError(
                f"TP hits ({stats.tp_hit_count}) + stop losses ({stats.stop_loss_count}) "
                f"exceeds completed signals ({stats.completed_signals})"
            )

        # Profitable + losing should not exceed completed
        if stats.profitable_signal_count + stats.losing_signal_count > stats.completed_signals:
            raise ScoringValidationError(
                f"Profitable ({stats.profitable_signal_count}) + losing ({stats.losing_signal_count}) "
                f"exceeds completed signals ({stats.completed_signals})"
            )
