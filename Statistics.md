Yep. Let's make it concrete instead of hand-wavy.

### Formula

I would make the internal score **0–1000**, then display `score / 100` as `/10`.

Use these normalized components:

* **TP Hit Rate:** 30%
* **Profitability:** 25%
* **Average Profit per Signal:** 15%
* **Best Single-Signal Profit:** 10%
* **Stop-Loss Rate:** 10%
* **Sample Size / Signal Count:** 10%

For each component, normalize to `0–1`.

For sample size, don't reward unlimited volume. Use a confidence curve:

[
Confidence = \min(1,\sqrt{N/100})
]

Then:

[
RawScore =
0.30(TP\ Rate)
+0.25(ProfitScore)
+0.15(AvgProfitScore)
+0.10(BestProfitScore)
+0.10(1-SL\ Rate)
+0.10(Confidence)
]

And:

[
Score = round(1000 \times RawScore)
]

Display:

[
DisplayScore = Score / 100
]

So `891` becomes **8.91/10**.

For profit normalization, don't let one absurd outlier completely dominate. Use percentile/capped normalization rather than simply `profit / max_profit`.

Now give Kiro this:

Implement the complete signal scoring and statistics system.

IMPORTANT:

* First inspect the existing codebase, database models, repositories, `services/statistics.py`, `services/scoring.py`, `analytics/*`, and existing signal/tracking/TP-hit data structures.
* Do NOT redesign unrelated parts.
* Reuse the existing database schema and repository/UoW patterns wherever possible.
* Do not invent duplicate models if the existing models already contain the required data.
* The scoring system must be deterministic and explainable.

## 1. SIGNAL SCORE

Every signal/source performance score is an integer from 0 to 1000 internally.

The UI/display value is:

```python
display_score = score / 100
```

Examples:

```text
891 -> 8.91/10
742 -> 7.42/10
1000 -> 10.00/10
```

### Components

Calculate these six normalized components, each in the range `[0, 1]`:

1. TP Hit Rate — weight 30%
2. Profitability — weight 25%
3. Average Profit per Signal — weight 15%
4. Best Single-Signal Profit — weight 10%
5. Stop-Loss Rate — weight 10%
6. Signal Count / Sample Confidence — weight 10%

Formula:

```text
raw_score =
    0.30 * tp_hit_rate_score
  + 0.25 * profitability_score
  + 0.15 * average_profit_score
  + 0.10 * best_profit_score
  + 0.10 * (1 - stop_loss_rate)
  + 0.10 * confidence_score
```

Then:

```text
score = round(1000 * raw_score)
```

Clamp the final value:

```text
0 <= score <= 1000
```

## 2. SAMPLE SIZE / CONFIDENCE

Signal count must improve confidence, but simply having thousands of signals must NOT automatically make a mediocre source better.

Use:

```text
confidence_score = min(1, sqrt(signal_count / 100))
```

Therefore:

```text
1 signal   -> 0.10
25 signals -> 0.50
100 signals -> 1.00
500 signals -> 1.00
```

This gives diminishing returns.

## 3. TP HIT RATE

Use completed/decided signals for the calculation.

```text
tp_hit_rate = signals_with_at_least_one_tp / decided_signals
```

Normalize directly:

```text
tp_hit_rate_score = clamp(tp_hit_rate, 0, 1)
```

Do not count currently waiting/open signals as successful or unsuccessful.

## 4. STOP LOSS RATE

```text
stop_loss_rate =
    stop_loss_signals / decided_signals
```

The score contribution is:

```text
1 - stop_loss_rate
```

Clamp to `[0, 1]`.

Signals that are still active must not be treated as stop losses.

## 5. PROFITABILITY SCORE

Use the actual realized PnL/profit data already produced by the engine/statistics system.

Do NOT simply use:

```text
profit / max_profit
```

because one extreme outlier would distort the entire ranking.

Use a robust normalization based on the population of comparable sources/signals.

Prefer percentile-based normalization:

```text
profitability_score = percentile_rank(total_profit)
```

where the lowest observed performance approaches `0` and the highest approaches `1`.

If there is insufficient historical data for percentile calculation, use a safe deterministic fallback.

## 6. AVERAGE PROFIT SCORE

Calculate:

```text
average_profit = total_realized_profit / completed_signals
```

Normalize it using the same robust/percentile approach:

```text
average_profit_score = percentile_rank(average_profit)
```

Do not include unresolved/open signals.

## 7. BEST SINGLE-SIGNAL PROFIT

For every source, find its highest realized profit from a single completed signal.

```text
best_profit = max(signal_realized_profit)
```

Normalize robustly:

```text
best_profit_score = percentile_rank(best_profit)
```

Do not allow one extreme value to completely dominate the final score.

## 8. WHAT THE SCORE REPRESENTS

The score is a measure of overall signal-source quality.

It should reward:

* frequent TP hits
* profitable signals
* strong average performance
* ability to produce high-profit signals
* low stop-loss frequency
* enough historical signals to provide confidence

It should NOT simply reward:

* number of signals
* one lucky huge trade
* currently open trades

## 9. STATISTICS SERVICE

Implement `services/statistics.py` as the central service responsible for calculating reusable performance statistics.

It should provide statistics such as:

```text
total signals
completed signals
active signals
TP hit count
stop-loss count
TP hit rate
stop-loss rate
total profit
average profit
best profit
worst profit
profitable signal count
losing signal count
```

Keep the calculations reusable by both scoring and analytics.

Do not duplicate the same SQL/calculation logic in `scoring.py` and `analytics/*`.

## 10. SCORING SERVICE

Implement `services/scoring.py`.

It should consume statistics and calculate the score.

Keep the formula explicit and easy to audit.

Prefer something conceptually like:

```python
@dataclass(frozen=True)
class ScoreBreakdown:
    score: int
    display_score: float
    tp_hit_rate_score: float
    profitability_score: float
    average_profit_score: float
    best_profit_score: float
    stop_loss_score: float
    confidence_score: float
```

The exact DTO structure should follow the project's existing DTO conventions.

The scoring service should make it possible to inspect WHY a source received a score such as `891`.

## 11. SCORE DISPLAY

Create a helper/property that converts:

```text
0..1000
```

to:

```text
0.00..10.00
```

Examples:

```text
891 -> 8.91
500 -> 5.00
1000 -> 10.00
```

Do not store `8.91` as the canonical score if the existing schema can store the integer `891`.

The canonical score should remain integer `0..1000`.

## 12. TIME WINDOWS

The statistics system must support at least:

```text
all-time
last 48 hours
last 7 days
last 30 days
```

Use the existing timestamp fields and timezone conventions.

The 48-hour statistics are especially important because the system already tracks signals over a 48-hour lifecycle.

Do not mix open/unresolved signals into completed performance calculations.

## 13. ANALYTICS INTEGRATION

Implement the existing:

```text
analytics/statistics.py
analytics/ranking.py
analytics/reports.py
```

around the statistics/scoring services instead of implementing independent calculation logic.

Ranking should be able to rank signal sources by score.

Reports should be able to consume the same statistics and score breakdown.

## 14. EDGE CASES

Handle these correctly:

* zero signals
* zero completed signals
* only open signals
* only losing signals
* only winning signals
* no TP hits
* no stop losses
* zero profit
* negative total profit
* one signal only
* fewer than 100 signals
* insufficient population for percentile normalization
* division by zero
* missing statistics
* newly created signal source with no history

A new source with no meaningful history must NOT accidentally receive `10.00/10`.

## 15. DATABASE / REPOSITORY

Inspect the existing repositories first.

If the existing repositories already provide the required aggregation/query functionality, reuse them.

If functionality is missing, add the smallest appropriate repository methods.

Do not bypass the UnitOfWork/repository architecture.

## 16. TESTS

Add tests for:

* TP hit rate
* stop-loss rate
* total profit
* average profit
* best profit
* confidence calculation
* final weighted score
* score conversion (`891 -> 8.91`)
* zero-signal source
* one-signal source
* all-winning source
* all-losing source
* mixed results
* open signals excluded from completed statistics
* 48-hour window
* percentile normalization fallback
* score clamping to `0..1000`

Use the project's existing async database/testing conventions.

## 17. IMPORTANT ARCHITECTURAL RULE

Do not put scoring logic into:

* engine rules
* TrackingManager
* ActionProcessor
* Telegram parsers
* Telegram sender
* market module

The engine produces trading outcomes.

Statistics observes those outcomes.

Scoring converts statistics into a source-quality score.

Analytics consumes statistics/scoring.

Keep those responsibilities separate.

Before modifying anything, inspect the actual current implementation and adapt this specification to the existing schema and architecture rather than blindly creating new structures.
