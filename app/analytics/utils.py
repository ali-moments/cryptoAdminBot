"""
Analytics utilities module.

This module provides common helper functions for analytics calculations,
data processing, and formatting operations used across the analytics system.
"""

import math
import statistics
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Tuple, Union, Callable
from functools import wraps
from dataclasses import dataclass

from loguru import logger

# Optional psutil import for memory monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not available - memory monitoring will be disabled")


@dataclass
class PerformanceMetrics:
    """Performance metrics for analytics operations."""
    operation_name: str
    execution_time: float
    memory_before: float
    memory_after: float
    memory_delta: float
    items_processed: int
    start_time: datetime
    end_time: datetime


class PerformanceMonitor:
    """Performance monitoring utility for analytics operations."""
    
    @staticmethod
    def get_memory_usage() -> float:
        """Get current memory usage in MB."""
        if not PSUTIL_AVAILABLE:
            return 0.0
            
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # Convert bytes to MB
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0
    
    @staticmethod
    def monitor_performance(operation_name: str, log_results: bool = True):
        """
        Decorator to monitor performance of analytics operations.
        
        Args:
            operation_name: Name of the operation being monitored
            log_results: Whether to log the performance results
            
        Returns:
            Decorator function
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                # Record start metrics
                start_time = datetime.now(timezone.utc)
                memory_before = PerformanceMonitor.get_memory_usage()
                start_perf = time.perf_counter()
                
                try:
                    # Execute the function
                    result = await func(*args, **kwargs)
                    
                    # Record end metrics
                    end_perf = time.perf_counter()
                    end_time = datetime.now(timezone.utc)
                    memory_after = PerformanceMonitor.get_memory_usage()
                    
                    # Calculate metrics
                    execution_time = end_perf - start_perf
                    memory_delta = memory_after - memory_before
                    
                    # Determine items processed
                    items_processed = 0
                    if hasattr(result, '__len__'):
                        items_processed = len(result)
                    elif isinstance(result, dict):
                        items_processed = len(result)
                    elif hasattr(result, 'items') and hasattr(result.items, '__len__'):
                        items_processed = len(result.items)
                    
                    # Create performance metrics
                    metrics = PerformanceMetrics(
                        operation_name=operation_name,
                        execution_time=execution_time,
                        memory_before=memory_before,
                        memory_after=memory_after,
                        memory_delta=memory_delta,
                        items_processed=items_processed,
                        start_time=start_time,
                        end_time=end_time
                    )
                    
                    # Log results if requested
                    if log_results:
                        PerformanceMonitor.log_metrics(metrics)
                    
                    return result
                    
                except Exception as e:
                    # Log error with timing info
                    error_time = time.perf_counter() - start_perf
                    logger.error(
                        f"Performance monitoring - {operation_name} failed after {error_time:.3f}s: {e}"
                    )
                    raise
            
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Record start metrics
                start_time = datetime.now(timezone.utc)
                memory_before = PerformanceMonitor.get_memory_usage()
                start_perf = time.perf_counter()
                
                try:
                    # Execute the function
                    result = func(*args, **kwargs)
                    
                    # Record end metrics
                    end_perf = time.perf_counter()
                    end_time = datetime.now(timezone.utc)
                    memory_after = PerformanceMonitor.get_memory_usage()
                    
                    # Calculate metrics
                    execution_time = end_perf - start_perf
                    memory_delta = memory_after - memory_before
                    
                    # Determine items processed
                    items_processed = 0
                    if hasattr(result, '__len__'):
                        items_processed = len(result)
                    elif isinstance(result, dict):
                        items_processed = len(result)
                    
                    # Create performance metrics
                    metrics = PerformanceMetrics(
                        operation_name=operation_name,
                        execution_time=execution_time,
                        memory_before=memory_before,
                        memory_after=memory_after,
                        memory_delta=memory_delta,
                        items_processed=items_processed,
                        start_time=start_time,
                        end_time=end_time
                    )
                    
                    # Log results if requested
                    if log_results:
                        PerformanceMonitor.log_metrics(metrics)
                    
                    return result
                    
                except Exception as e:
                    # Log error with timing info
                    error_time = time.perf_counter() - start_perf
                    logger.error(
                        f"Performance monitoring - {operation_name} failed after {error_time:.3f}s: {e}"
                    )
                    raise
            
            # Return appropriate wrapper based on function type
            if hasattr(func, '__code__') and 'async' in func.__code__.co_flags.__str__():
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator
    
    @staticmethod
    def log_metrics(metrics: PerformanceMetrics) -> None:
        """Log performance metrics with appropriate log level."""
        # Determine log level based on execution time and memory usage
        if metrics.execution_time > 10.0:  # More than 10 seconds
            log_level = "warning"
        elif metrics.execution_time > 2.0:  # More than 2 seconds
            log_level = "info"
        else:
            log_level = "debug"
        
        # Format memory usage
        memory_str = ""
        if metrics.memory_delta != 0:
            if abs(metrics.memory_delta) > 100:  # More than 100MB
                memory_str = f", memory: {metrics.memory_delta:+.1f}MB"
            elif abs(metrics.memory_delta) > 10:  # More than 10MB
                memory_str = f", memory: {metrics.memory_delta:+.1f}MB"
        
        # Create log message
        message = (
            f"Performance - {metrics.operation_name}: "
            f"{metrics.execution_time:.3f}s"
            f"{memory_str}"
        )
        
        if metrics.items_processed > 0:
            items_per_sec = metrics.items_processed / metrics.execution_time if metrics.execution_time > 0 else 0
            message += f", processed: {metrics.items_processed} items ({items_per_sec:.1f}/sec)"
        
        # Log with appropriate level
        if log_level == "warning":
            logger.warning(message)
        elif log_level == "info":
            logger.info(message)
        else:
            logger.debug(message)
    
    @staticmethod
    def track_batch_operation(
        operation_name: str,
        total_items: int,
        batch_size: int = 100
    ) -> "BatchTracker":
        """
        Create a batch operation tracker for monitoring progress.
        
        Args:
            operation_name: Name of the batch operation
            total_items: Total number of items to process
            batch_size: Size of each batch for progress reporting
            
        Returns:
            BatchTracker instance
        """
        return BatchTracker(operation_name, total_items, batch_size)


class BatchTracker:
    """Tracks progress and performance of batch operations."""
    
    def __init__(self, operation_name: str, total_items: int, batch_size: int = 100):
        self.operation_name = operation_name
        self.total_items = total_items
        self.batch_size = batch_size
        self.processed_items = 0
        self.start_time = time.perf_counter()
        self.last_report_time = self.start_time
        self.start_memory = PerformanceMonitor.get_memory_usage()
        
        logger.info(f"Starting batch operation: {operation_name} ({total_items} items)")
    
    def update_progress(self, items_processed: int) -> None:
        """Update progress and log if batch threshold is reached."""
        self.processed_items += items_processed
        current_time = time.perf_counter()
        
        # Report progress every batch_size items or at completion
        if (self.processed_items % self.batch_size == 0 or 
            self.processed_items >= self.total_items or
            current_time - self.last_report_time > 30):  # Or every 30 seconds
            
            self._log_progress(current_time)
            self.last_report_time = current_time
    
    def _log_progress(self, current_time: float) -> None:
        """Log current progress with performance metrics."""
        elapsed_time = current_time - self.start_time
        progress_pct = (self.processed_items / self.total_items) * 100 if self.total_items > 0 else 0
        
        # Calculate rates
        items_per_sec = self.processed_items / elapsed_time if elapsed_time > 0 else 0
        
        # Estimate completion time
        if items_per_sec > 0 and self.processed_items < self.total_items:
            remaining_items = self.total_items - self.processed_items
            eta_seconds = remaining_items / items_per_sec
            eta_str = f", ETA: {TimeUtils.format_duration(eta_seconds)}"
        else:
            eta_str = ""
        
        # Memory usage
        current_memory = PerformanceMonitor.get_memory_usage()
        memory_delta = current_memory - self.start_memory
        memory_str = f", memory: {memory_delta:+.1f}MB" if abs(memory_delta) > 1 else ""
        
        logger.info(
            f"Batch progress - {self.operation_name}: "
            f"{self.processed_items}/{self.total_items} ({progress_pct:.1f}%) "
            f"in {elapsed_time:.1f}s ({items_per_sec:.1f}/sec)"
            f"{memory_str}{eta_str}"
        )
    
    def complete(self) -> PerformanceMetrics:
        """Mark operation as complete and return final metrics."""
        end_time = time.perf_counter()
        end_memory = PerformanceMonitor.get_memory_usage()
        
        metrics = PerformanceMetrics(
            operation_name=self.operation_name,
            execution_time=end_time - self.start_time,
            memory_before=self.start_memory,
            memory_after=end_memory,
            memory_delta=end_memory - self.start_memory,
            items_processed=self.processed_items,
            start_time=datetime.fromtimestamp(self.start_time, timezone.utc),
            end_time=datetime.fromtimestamp(end_time, timezone.utc)
        )
        
        logger.success(
            f"Batch complete - {self.operation_name}: "
            f"{self.processed_items} items in {metrics.execution_time:.3f}s"
        )
        
        PerformanceMonitor.log_metrics(metrics)
        return metrics


class MathUtils:
    """Mathematical utility functions for analytics calculations."""
    
    @staticmethod
    def safe_divide(numerator: Union[int, float, Decimal], denominator: Union[int, float, Decimal]) -> Decimal:
        """
        Safely divide two numbers, returning 0 if denominator is 0.
        
        Args:
            numerator: The numerator value
            denominator: The denominator value
            
        Returns:
            Decimal result of division, or 0 if denominator is 0
        """
        if denominator == 0:
            return Decimal("0.0000")
        
        return Decimal(numerator) / Decimal(denominator)
    
    @staticmethod
    def calculate_percentage(part: Union[int, float, Decimal], total: Union[int, float, Decimal]) -> Decimal:
        """
        Calculate percentage with safe division.
        
        Args:
            part: The part value
            total: The total value
            
        Returns:
            Percentage as decimal (e.g., 0.75 for 75%)
        """
        if total == 0:
            return Decimal("0.0000")
            
        return (Decimal(part) / Decimal(total)) * Decimal("100")
    
    @staticmethod
    def round_decimal(value: Union[int, float, Decimal], places: int = 2) -> Decimal:
        """
        Round decimal to specified places.
        
        Args:
            value: Value to round
            places: Number of decimal places
            
        Returns:
            Rounded decimal value
        """
        if places == 0:
            quantizer = Decimal("1")
        else:
            quantizer = Decimal("0." + "0" * (places - 1) + "1")
            
        return Decimal(value).quantize(quantizer, rounding=ROUND_HALF_UP)
    
    @staticmethod
    def clamp(value: Union[int, float], min_val: Union[int, float], max_val: Union[int, float]) -> Union[int, float]:
        """
        Clamp value between min and max inclusive.
        
        Args:
            value: Value to clamp
            min_val: Minimum allowed value
            max_val: Maximum allowed value
            
        Returns:
            Clamped value
        """
        return max(min_val, min(max_val, value))
    
    @staticmethod
    def calculate_confidence_score(signal_count: int, max_signals: int = 100) -> float:
        """
        Calculate confidence score based on sample size.
        
        Formula: min(1, sqrt(signal_count / max_signals))
        
        Args:
            signal_count: Number of signals in sample
            max_signals: Signal count for maximum confidence (default: 100)
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        if signal_count <= 0:
            return 0.0
        
        return min(1.0, math.sqrt(signal_count / max_signals))


class StatisticalUtils:
    """Statistical utility functions for analytics."""
    
    @staticmethod
    def calculate_percentile_rank(value: float, population: List[float]) -> float:
        """
        Calculate the percentile rank of a value within a population.
        
        Args:
            value: Value to rank
            population: Population of values
            
        Returns:
            Percentile rank (0-100)
        """
        if not population:
            return 0.0
        
        if len(population) == 1:
            return 100.0 if value >= population[0] else 0.0
        
        # Sort the population
        sorted_pop = sorted(population)
        
        # Count values less than and equal to target
        less_than = sum(1 for x in sorted_pop if x < value)
        equal_to = sum(1 for x in sorted_pop if x == value)
        
        # Calculate percentile rank
        percentile = (less_than + 0.5 * equal_to) / len(population) * 100.0
        return max(0.0, min(100.0, percentile))
    
    @staticmethod
    def create_distribution_buckets(values: List[float], bucket_count: int = 10) -> List[Dict[str, Any]]:
        """
        Create distribution buckets for histogram-style analysis.
        
        Args:
            values: List of values to distribute
            bucket_count: Number of buckets to create
            
        Returns:
            List of bucket dictionaries with range and count
        """
        if not values:
            return []
        
        min_val = min(values)
        max_val = max(values)
        
        if min_val == max_val:
            return [{"range": f"{min_val:.2f}", "count": len(values), "percentage": 100.0}]
        
        bucket_size = (max_val - min_val) / bucket_count
        buckets = []
        
        for i in range(bucket_count):
            bucket_min = min_val + i * bucket_size
            bucket_max = min_val + (i + 1) * bucket_size
            
            # Count values in this bucket
            count = 0
            for value in values:
                if i == bucket_count - 1:  # Last bucket includes max value
                    if bucket_min <= value <= bucket_max:
                        count += 1
                else:
                    if bucket_min <= value < bucket_max:
                        count += 1
            
            percentage = (count / len(values)) * 100
            
            buckets.append({
                "range": f"{bucket_min:.2f}-{bucket_max:.2f}",
                "count": count,
                "percentage": percentage,
            })
        
        return buckets
    
    @staticmethod
    def calculate_correlation(x_values: List[float], y_values: List[float]) -> float:
        """
        Calculate Pearson correlation coefficient between two datasets.
        
        Args:
            x_values: First dataset
            y_values: Second dataset
            
        Returns:
            Correlation coefficient (-1 to 1)
        """
        if len(x_values) != len(y_values) or len(x_values) < 2:
            return 0.0
        
        try:
            return statistics.correlation(x_values, y_values)
        except (statistics.StatisticsError, ValueError):
            return 0.0
    
    @staticmethod
    def calculate_quartiles(values: List[float]) -> Dict[str, float]:
        """
        Calculate quartiles for a dataset.
        
        Args:
            values: List of numeric values
            
        Returns:
            Dictionary with q1, q2 (median), q3 values
        """
        if not values:
            return {"q1": 0.0, "q2": 0.0, "q3": 0.0}
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        if n == 1:
            val = sorted_values[0]
            return {"q1": val, "q2": val, "q3": val}
        
        q2 = statistics.median(sorted_values)
        
        # Calculate Q1 and Q3
        if n >= 4:
            q1 = sorted_values[n // 4]
            q3 = sorted_values[3 * n // 4]
        else:
            q1 = sorted_values[0]
            q3 = sorted_values[-1]
        
        return {"q1": q1, "q2": q2, "q3": q3}


class TimeUtils:
    """Time and date utility functions for analytics."""
    
    @staticmethod
    def get_period_bounds(
        period_type: str, 
        reference_time: Optional[datetime] = None,
        timezone_info: timezone = timezone.utc
    ) -> Tuple[datetime, datetime]:
        """
        Get start and end bounds for different time periods.
        
        Args:
            period_type: Type of period ('24h', 'week', 'month', 'quarter', 'year')
            reference_time: Reference time (defaults to now)
            timezone_info: Timezone for calculations
            
        Returns:
            Tuple of (start_time, end_time)
        """
        if reference_time is None:
            reference_time = datetime.now(timezone_info)
        
        if period_type == "24h":
            start = reference_time.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        
        elif period_type == "week":
            # Start of current week (Monday)
            start = reference_time - timedelta(days=reference_time.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
        
        elif period_type == "month":
            # Start of current month
            start = reference_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # Start of next month
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
        
        elif period_type == "quarter":
            # Calculate quarter
            quarter_start_month = ((reference_time.month - 1) // 3) * 3 + 1
            start = reference_time.replace(
                month=quarter_start_month, 
                day=1, 
                hour=0, 
                minute=0, 
                second=0, 
                microsecond=0
            )
            
            # End of quarter (start of next quarter)
            if quarter_start_month == 10:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=quarter_start_month + 3)
        
        elif period_type == "year":
            # Start of current year
            start = reference_time.replace(
                month=1, 
                day=1, 
                hour=0, 
                minute=0, 
                second=0, 
                microsecond=0
            )
            end = start.replace(year=start.year + 1)
        
        else:
            raise ValueError(f"Unknown period type: {period_type}")
        
        return start, end
    
    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        Format duration in seconds to human-readable string.
        
        Args:
            seconds: Duration in seconds
            
        Returns:
            Formatted duration string
        """
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        elif seconds < 86400:
            hours = seconds / 3600
            return f"{hours:.1f}h"
        else:
            days = seconds / 86400
            return f"{days:.1f}d"


class FormattingUtils:
    """Formatting utility functions for display and output."""
    
    @staticmethod
    def format_percentage(
        value: Union[int, float, Decimal], 
        decimal_places: int = 1, 
        include_sign: bool = False
    ) -> str:
        """
        Format a decimal value as a percentage string.
        
        Args:
            value: Value to format (0.75 becomes "75.0%")
            decimal_places: Number of decimal places
            include_sign: Whether to include + sign for positive values
            
        Returns:
            Formatted percentage string
        """
        percentage = float(value)
        
        if include_sign and percentage > 0:
            return f"+{percentage:.{decimal_places}f}%"
        else:
            return f"{percentage:.{decimal_places}f}%"
    
    @staticmethod
    def format_decimal(
        value: Union[int, float, Decimal], 
        decimal_places: int = 2, 
        thousands_separator: bool = True
    ) -> str:
        """
        Format a decimal value with specified precision.
        
        Args:
            value: Value to format
            decimal_places: Number of decimal places
            thousands_separator: Whether to include thousands separator
            
        Returns:
            Formatted decimal string
        """
        formatted = f"{float(value):.{decimal_places}f}"
        
        if thousands_separator:
            # Add thousands separator
            parts = formatted.split(".")
            integer_part = parts[0]
            decimal_part = parts[1] if len(parts) > 1 else ""
            
            # Add commas to integer part
            integer_with_commas = "{:,}".format(int(integer_part))
            
            if decimal_part:
                formatted = f"{integer_with_commas}.{decimal_part}"
            else:
                formatted = integer_with_commas
        
        return formatted
    
    @staticmethod
    def format_score(score: int, display_format: str = "decimal") -> str:
        """
        Format a score (0-1000) in different display formats.
        
        Args:
            score: Score value (0-1000)
            display_format: Format type ('decimal', 'fraction', 'percentage')
            
        Returns:
            Formatted score string
        """
        if display_format == "decimal":
            return f"{score / 100:.2f}/10"
        elif display_format == "fraction":
            return f"{score}/1000"
        elif display_format == "percentage":
            return f"{score / 10:.1f}%"
        else:
            raise ValueError(f"Unknown display format: {display_format}")
    
    @staticmethod
    def format_large_number(value: Union[int, float]) -> str:
        """
        Format large numbers with appropriate suffixes (K, M, B).
        
        Args:
            value: Numeric value to format
            
        Returns:
            Formatted string with suffix
        """
        abs_value = abs(value)
        
        if abs_value >= 1_000_000_000:
            formatted = f"{value / 1_000_000_000:.1f}B"
        elif abs_value >= 1_000_000:
            formatted = f"{value / 1_000_000:.1f}M"
        elif abs_value >= 1_000:
            formatted = f"{value / 1_000:.1f}K"
        else:
            formatted = f"{value:.0f}"
        
        return formatted


class DataProcessingUtils:
    """Data processing and manipulation utilities."""
    
    @staticmethod
    def aggregate_by_key(
        data: List[Dict[str, Any]], 
        group_key: str, 
        agg_functions: Dict[str, str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Aggregate data by a grouping key with specified aggregation functions.
        
        Args:
            data: List of dictionaries to aggregate
            group_key: Key to group by
            agg_functions: Dictionary mapping field names to aggregation functions
                          ('sum', 'mean', 'count', 'min', 'max')
        
        Returns:
            Dictionary with grouped and aggregated data
        """
        groups = {}
        
        # Group data
        for item in data:
            key = item[group_key]
            if key not in groups:
                groups[key] = []
            groups[key].append(item)
        
        # Aggregate each group
        results = {}
        for key, group_data in groups.items():
            aggregated = {}
            
            for field, func in agg_functions.items():
                values = [item.get(field, 0) for item in group_data if field in item]
                
                if not values:
                    aggregated[field] = 0
                    continue
                
                if func == "sum":
                    aggregated[field] = sum(values)
                elif func == "mean":
                    aggregated[field] = sum(values) / len(values)
                elif func == "count":
                    aggregated[field] = len(values)
                elif func == "min":
                    aggregated[field] = min(values)
                elif func == "max":
                    aggregated[field] = max(values)
                else:
                    logger.warning(f"Unknown aggregation function: {func}")
                    aggregated[field] = 0
            
            results[key] = aggregated
        
        return results
    
    @staticmethod
    def filter_outliers(
        values: List[float], 
        method: str = "iqr", 
        threshold: float = 1.5
    ) -> List[float]:
        """
        Filter outliers from a dataset using specified method.
        
        Args:
            values: List of numeric values
            method: Method to use ('iqr', 'zscore')
            threshold: Threshold for outlier detection
            
        Returns:
            List with outliers removed
        """
        if len(values) < 4:
            return values
        
        if method == "iqr":
            # Interquartile Range method
            quartiles = StatisticalUtils.calculate_quartiles(values)
            q1, q3 = quartiles["q1"], quartiles["q3"]
            iqr = q3 - q1
            
            lower_bound = q1 - threshold * iqr
            upper_bound = q3 + threshold * iqr
            
            return [v for v in values if lower_bound <= v <= upper_bound]
        
        elif method == "zscore":
            # Z-score method
            mean_val = statistics.mean(values)
            std_val = statistics.stdev(values) if len(values) > 1 else 0
            
            if std_val == 0:
                return values
            
            return [
                v for v in values 
                if abs((v - mean_val) / std_val) <= threshold
            ]
        
        else:
            raise ValueError(f"Unknown outlier detection method: {method}")
    
    @staticmethod
    def normalize_values(
        values: List[float], 
        method: str = "minmax", 
        target_range: Tuple[float, float] = (0.0, 1.0)
    ) -> List[float]:
        """
        Normalize values using specified method.
        
        Args:
            values: List of values to normalize
            method: Normalization method ('minmax', 'zscore')
            target_range: Target range for minmax normalization
            
        Returns:
            List of normalized values
        """
        if not values:
            return values
        
        if method == "minmax":
            min_val = min(values)
            max_val = max(values)
            
            if min_val == max_val:
                # All values are the same, return middle of target range
                mid_point = (target_range[0] + target_range[1]) / 2
                return [mid_point] * len(values)
            
            range_val = max_val - min_val
            target_min, target_max = target_range
            target_range_val = target_max - target_min
            
            return [
                target_min + ((v - min_val) / range_val) * target_range_val
                for v in values
            ]
        
        elif method == "zscore":
            mean_val = statistics.mean(values)
            std_val = statistics.stdev(values) if len(values) > 1 else 1
            
            return [(v - mean_val) / std_val for v in values]
        
        else:
            raise ValueError(f"Unknown normalization method: {method}")


# Convenience functions for common operations
def safe_percentage(numerator: Union[int, float, Decimal], denominator: Union[int, float, Decimal]) -> Decimal:
    """Quick function for safe percentage calculation."""
    return MathUtils.calculate_percentage(numerator, denominator)


def format_pnl(value: Union[int, float, Decimal], include_sign: bool = True) -> str:
    """Quick function for formatting PNL values."""
    return FormattingUtils.format_percentage(value, decimal_places=2, include_sign=include_sign)


def format_rate(value: Union[int, float, Decimal]) -> str:
    """Quick function for formatting rates (hit rates, etc.)."""
    return FormattingUtils.format_percentage(value * 100, decimal_places=1)


def calculate_win_rate(wins: int, total: int) -> Decimal:
    """Quick function for calculating win rates."""
    return MathUtils.calculate_percentage(wins, total)