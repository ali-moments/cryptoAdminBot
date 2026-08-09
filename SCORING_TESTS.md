# Scoring System Tests

This document describes the comprehensive test suite for the signal scoring system.

## Test Structure

```
tests/
├── services/
│   ├── test_statistics.py          # StatisticsService tests
│   ├── test_scoring.py             # ScoringService tests  
│   ├── test_validation.py          # ScoringValidator tests
│   └── test_scoring_integration.py # ScoringIntegrationService tests
├── analytics/
│   └── test_ranking.py             # AnalyticsRanking tests
└── cli/
    └── test_scoring_commands.py    # CLI command tests
```

## Test Coverage

### StatisticsService (`test_statistics.py`)
- ✅ Valid source statistics calculation
- ✅ Zero signals edge case
- ✅ Zero completed signals edge case
- ✅ Time window filtering
- ✅ Confidence score calculation (sample size)
- ✅ Profit percentiles for normalization
- ✅ Input validation (invalid source IDs, time windows)
- ✅ Data quality warning detection
- ✅ Concurrent statistics requests
- ✅ Database error handling
- ✅ Rate calculation edge cases
- ✅ All sources statistics aggregation

### ScoringService (`test_scoring.py`)
- ✅ Normal score calculation with good data
- ✅ Zero signals source scoring
- ✅ New source conservative scoring (few signals)
- ✅ Perfect performance scoring
- ✅ Poor performance scoring
- ✅ Percentile score calculation edge cases
- ✅ Percentile rank calculation
- ✅ All scores calculation
- ✅ Score format conversion (0-1000 ↔ 0.00-10.00)
- ✅ Score explanation generation
- ✅ Score validation and error handling
- ✅ Component weight verification (sum to 1.0)
- ✅ Time window parameter passing

### ScoringValidator (`test_validation.py`)
- ✅ Valid statistics validation
- ✅ Negative count sanitization
- ✅ Invalid rate sanitization (outside 0-1 range)
- ✅ Extreme profit value sanitization
- ✅ Logical consistency error detection
- ✅ Score breakdown validation and clamping
- ✅ New source scoring (zero and few signals)
- ✅ Division by zero handling
- ✅ Empty profit population handling
- ✅ Time window validation
- ✅ Source ID validation
- ✅ Score value validation
- ✅ Data quality warning detection
- ✅ Integration of all sanitization methods

### ScoringIntegrationService (`test_scoring_integration.py`)
- ✅ Successful batch score updates
- ✅ No sources edge case
- ✅ Partial failure handling in batch updates
- ✅ Single source score update success
- ✅ Database failure handling
- ✅ Bulk score update operations
- ✅ Score validation in bulk updates
- ✅ Score recalculation for time windows
- ✅ Update recommendations based on activity
- ✅ Score consistency validation
- ✅ Emergency score reset functionality
- ✅ Concurrent batch processing
- ✅ Error handling in batch processing

### AnalyticsRanking (`test_ranking.py`)
- ✅ Default score leaderboard
- ✅ Leaderboard with result limits
- ✅ Minimum signal count filtering
- ✅ Ranking by different metrics (TP rate, profit, etc.)
- ✅ Performance tier classification
- ✅ Rising stars detection (improvement analysis)
- ✅ Consistency ranking calculation
- ✅ Metric leaders identification
- ✅ Direct source comparison
- ✅ Empty leaderboard handling
- ✅ Sort by metric edge cases

### CLI Commands (`test_scoring_commands.py`)
- ✅ Update all scores (dry-run mode)
- ✅ Update all scores (actual update)
- ✅ Update all scores with failures
- ✅ Single source score update
- ✅ Single source update with explanation
- ✅ Score consistency validation (clean data)
- ✅ Score consistency validation (with issues)
- ✅ Leaderboard display
- ✅ Command error handling
- ✅ Time window parameter parsing

## Running Tests

### Run All Tests
```bash
pytest
```

### Run Specific Test Categories
```bash
# Service layer tests
pytest tests/services/

# Analytics tests  
pytest tests/analytics/

# CLI tests
pytest tests/cli/

# Specific service
pytest tests/services/test_scoring.py

# Specific test
pytest tests/services/test_scoring.py::TestScoringService::test_calculate_source_score_normal_case
```

### Run with Coverage
```bash
pytest --cov=app/services --cov=app/analytics --cov=app/cli
```

### Run with Markers
```bash
# Run only unit tests
pytest -m unit

# Run only scoring tests
pytest -m scoring

# Skip slow tests
pytest -m "not slow"
```

## Test Data Patterns

### Sample Statistics
Tests use realistic sample data that represents common scenarios:
- **High performer**: 20 signals, 67% TP rate, positive profit
- **New source**: 3-5 signals, conservative scoring expected
- **Poor performer**: High stop-loss rate, negative total profit
- **Perfect performer**: 100% TP rate, high profit (edge case)

### Score Breakdown Examples
- **Elite (9.0+/10)**: 900+ score
- **Excellent (8.0-8.9/10)**: 800-899 score  
- **Good (7.0-7.9/10)**: 700-799 score
- **Average (6.0-6.9/10)**: 600-699 score
- **Poor (<5.0/10)**: <500 score

### Edge Cases Covered
1. **Zero signals**: New sources with no data
2. **Division by zero**: Zero completed signals
3. **Extreme values**: Very high/low profits (>10,000%, <-100%)
4. **Invalid data**: Negative counts, rates outside 0-1
5. **Inconsistent data**: TP hits > completed signals
6. **Empty populations**: Percentile calculation with no data
7. **Concurrent operations**: Multiple scoring requests
8. **Database failures**: Connection errors, update failures

## Mocking Strategy

### Database Mocking
- `AsyncSession` mocked with realistic query results
- `UnitOfWork` context manager properly mocked
- Repository methods return appropriate test data

### Service Mocking
- Dependencies injected as mocks
- Async methods properly mocked with `AsyncMock`
- Service layer isolated from database implementation

### CLI Mocking
- `build_application()` mocked to avoid full app initialization
- Service instances mocked for controlled testing
- Click runner used for command-line interaction testing

## Test Assertions

### Score Validation
```python
# Score ranges
assert 0 <= result.score <= 1000
assert 0.0 <= result.display_score <= 10.0

# Component scores
assert 0.0 <= result.tp_hit_rate_score <= 1.0
assert 0.0 <= result.confidence_score <= 1.0

# Score consistency
assert result.score == int(result.display_score * 100)
```

### Statistics Validation
```python
# Non-negative counts
assert result.total_signals >= 0
assert result.completed_signals >= 0

# Rate ranges
assert 0.0 <= float(result.tp_hit_rate) <= 1.0
assert 0.0 <= float(result.stop_loss_rate) <= 1.0

# Logical consistency
assert result.total_signals >= result.completed_signals
```

### Error Handling
```python
# Validation errors
with pytest.raises(ScoringValidationError, match="Invalid source ID"):
    await service.method(-1)

# Database errors
with pytest.raises(ScoringValidationError, match="Failed to calculate"):
    await service.method(1)  # When DB fails
```

## Performance Tests

While not implemented in this basic suite, consider adding:

### Load Testing
- Batch updates with 1000+ sources
- Concurrent scoring requests
- Large dataset percentile calculations

### Memory Testing
- Statistics calculation with large datasets
- Score caching effectiveness
- Memory usage during bulk operations

### Timing Tests
- Score calculation performance targets
- Database query optimization validation
- CLI command response times

## Continuous Integration

### Test Pipeline
1. **Lint**: Code formatting and style checks
2. **Unit Tests**: All service and analytics tests
3. **Integration Tests**: Database integration (if applicable)
4. **Coverage**: Minimum 90% coverage requirement
5. **Performance**: Regression testing for score calculations

### Test Environment
- Isolated test database (or full mocking)
- Consistent test data fixtures
- Deterministic test execution
- Parallel test execution support

## Debugging Tests

### Verbose Output
```bash
pytest -v -s tests/services/test_scoring.py
```

### Debug Specific Test
```bash
pytest --pdb tests/services/test_scoring.py::TestScoringService::test_name
```

### Print Test Data
```bash
pytest -s --capture=no tests/services/
```

This comprehensive test suite ensures the scoring system is robust, handles edge cases gracefully, and maintains data integrity across all operations.