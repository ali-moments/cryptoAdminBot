# Scoring System CLI

This document describes the CLI commands available for managing the signal scoring system.

## Installation

The CLI is built with Click and uses the existing application dependencies.

```bash
# Make the CLI executable
chmod +x manage.py

# Install additional CLI dependency if not present
pip install click
```

## Available Commands

### Score Management

#### Update All Scores
```bash
./manage.py scoring update-all-scores [OPTIONS]

Options:
  --time-window [48h|7d|30d|all]  Time window for scoring calculations (default: all)
  --batch-size INTEGER            Number of sources to process concurrently (default: 10)
  --dry-run                       Calculate scores but do not update database
```

Examples:
```bash
# Update all scores with default settings
./manage.py scoring update-all-scores

# Dry run to see what would change
./manage.py scoring update-all-scores --dry-run

# Update using last 7 days data only
./manage.py scoring update-all-scores --time-window 7d

# Update with smaller batch size for production
./manage.py scoring update-all-scores --batch-size 5
```

#### Update Single Source Score
```bash
./manage.py scoring update-source-score SOURCE_ID [OPTIONS]

Options:
  --time-window [48h|7d|30d|all]  Time window for scoring calculations (default: all)
  --explain                       Show detailed score breakdown explanation
```

Examples:
```bash
# Update score for source ID 42
./manage.py scoring update-source-score 42

# Update with detailed explanation
./manage.py scoring update-source-score 42 --explain

# Update using recent data only
./manage.py scoring update-source-score 42 --time-window 48h
```

### Validation and Maintenance

#### Validate Score Consistency
```bash
./manage.py scoring validate-consistency
```

Checks if stored scores match calculated scores and reports any inconsistencies.

#### Get Update Recommendations
```bash
./manage.py scoring update-recommendations [OPTIONS]

Options:
  --priority [high|medium|low|all]  Show sources with specific update priority (default: all)
```

#### Emergency Score Reset
```bash
./manage.py scoring emergency-reset SOURCE_ID
```

Resets a source's score to 0. Requires confirmation.

### Reporting and Analysis

#### Generate Performance Report
```bash
./manage.py scoring generate-report [OPTIONS]

Options:
  --format [text|json]  Output format (default: text)
  --output PATH         Output file path (stdout if not specified)
```

Examples:
```bash
# Generate text report to console
./manage.py scoring generate-report

# Generate JSON report to file
./manage.py scoring generate-report --format json --output report.json

# Generate text report to file
./manage.py scoring generate-report --output report.txt
```

#### Show Leaderboard
```bash
./manage.py scoring leaderboard [OPTIONS]

Options:
  --limit INTEGER  Number of top sources to show (default: 10)
```

## Scheduled Jobs

The scoring system automatically schedules the following jobs:

### Hourly Score Update (High Priority Sources)
- **Schedule**: Every hour at :15 minutes, 8 AM - 11 PM Tehran time
- **Purpose**: Updates scores for sources with 3+ recent completions
- **Job ID**: `hourly_score_update`

### Daily Comprehensive Score Update
- **Schedule**: Daily at 6:00 AM Tehran time
- **Purpose**: Updates all source scores with comprehensive statistics
- **Job ID**: `daily_score_update`

### Weekly Score Validation
- **Schedule**: Sundays at 7:00 AM Tehran time
- **Purpose**: Validates score consistency and reports discrepancies
- **Job ID**: `weekly_score_validation`

## Integration with Existing System

The scoring system is integrated with the existing scheduler in `app/scheduler/scheduler.py`. 

### Enabling/Disabling Scoring Jobs

Scoring jobs are automatically enabled when the application starts if the `Application` instance is passed to the `AppScheduler`.

To disable scoring jobs:
1. Remove the scoring scheduler setup from `app/scheduler/scheduler.py`
2. Or set the app parameter to `None` when creating the scheduler

### Manual Job Management

Jobs can be managed programmatically:

```python
from app.scheduler.scoring_scheduler import setup_scoring_jobs, remove_scoring_jobs

# Add scoring jobs to existing scheduler
setup_scoring_jobs(scheduler, app)

# Remove scoring jobs
remove_scoring_jobs(scheduler)
```

## Troubleshooting

### Common Issues

1. **Database Connection Errors**: Ensure the application database is available and credentials are correct.

2. **Permission Errors**: Make sure the CLI script is executable (`chmod +x manage.py`).

3. **Import Errors**: Ensure all dependencies are installed and the Python path includes the project root.

4. **Scheduler Job Failures**: Check logs for detailed error messages. Jobs are designed to fail gracefully without stopping the scheduler.

### Logging

All CLI commands use the loguru logger configured in the application. Logs will appear in the console and any configured log files.

### Performance Considerations

- Use smaller batch sizes (`--batch-size`) in production environments
- Run comprehensive updates during low-activity periods
- Use dry-run mode to test changes before applying them
- Monitor database performance during bulk updates

## Examples

### Daily Maintenance Workflow

```bash
# 1. Check what needs updating
./manage.py scoring update-recommendations

# 2. Update high-priority sources
./manage.py scoring update-all-scores --time-window 48h --batch-size 5

# 3. Validate consistency
./manage.py scoring validate-consistency

# 4. Generate daily report
./manage.py scoring generate-report --output daily-report.txt
```

### Troubleshooting Workflow

```bash
# 1. Check current leaderboard
./manage.py scoring leaderboard --limit 20

# 2. Validate scores
./manage.py scoring validate-consistency

# 3. Get detailed breakdown for suspicious source
./manage.py scoring update-source-score 123 --explain --dry-run

# 4. Reset problematic source if needed
./manage.py scoring emergency-reset 123
```