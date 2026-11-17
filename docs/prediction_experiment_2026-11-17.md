# UFC prediction experiment — November 17, 2026

Recent-performance features improved retrospective 2026 matchup accuracy from
61.5% to 63.7% on the same 501 fights, with lower log loss and Brier score.
This is promising evidence, not a statistically established accuracy gain or a
prospective performance guarantee. Production remains unchanged.

## Refreshed existing backtest

Annual walk-forward evaluation, 2013 through November 14, 2026; 6,718 decisive
fights. The classifier is refit using only earlier years; rating states update
chronologically before each prediction.

| Reference | Accuracy | Log loss | Brier |
| --- | ---: | ---: | ---: |
| Standard Elo, K=32 | 55.75% | 0.68465 | 0.24579 |
| Existing corner-aware prediction recipe | 60.27% | 0.65919 | 0.23310 |

Always selecting the red corner scores 57.49% on this historical sample. This
extra reference matters because corner assignment is not statistically neutral.
The existing model exceeds that simple rule as well as standard Elo.
These are recipe backtests, not tests of today's persisted coefficients against
old bouts. Reusing today's coefficients on their training history would be
in-sample evaluation.

## Experiment design

Compared three predefined recipes: a baseline logistic model, logistic regression
with 11 recent-performance differences, and shallow histogram gradient boosting
with those same extra features. Candidate selection used pooled matchup log loss
from annual 2022–2025 folds only (2,038 fights). Recent logistic won selection:
0.64043 versus 0.64897 for the baseline and 0.64816 for boosting. Its development
accuracy was 63.10% versus 62.56% for baseline.

The selected recipe was then evaluated on 501 decisive 2026 fights across 40
event dates, ending November 14. Classifier training stops before January 2026;
pre-fight ratings and rolling statistics can incorporate earlier 2026 bouts.
No feature or hyperparameter was changed based on the 2026 results. A second
execution added reporting references and a data fingerprint, reproducing the
same model scores without changing candidate selection or model parameters.

Added features use each fighter's last five fights: significant-strike
differential per minute, striking accuracy and defence, takedown rate and defence,
control share, win and finish rates, prior opponent strength, and stats coverage.
Prior career fight count supplies an experience feature. All are expressed as
fighter differences with fixed smoothing priors. No fight on the same date can
contribute statistics to another fight on that date.

## 2026 results: matchup tool, no corner advantage

| Recipe | Correct picks | Accuracy | Log loss | Brier |
| --- | ---: | ---: | ---: | ---: |
| Experimental baseline | 308 / 501 | 61.48% | 0.65065 | 0.22898 |
| Selected recent-performance logistic | 319 / 501 | 63.67% | 0.64019 | 0.22441 |
| Legacy features, refit before 2026 (reference) | 309 / 501 | 61.68% | 0.65183 | 0.22957 |

The candidate makes 11 more correct picks than the experimental baseline:
+2.20 percentage points, 1.61% lower log loss and 2.00% lower Brier score.
Against the legacy-feature reference it makes 10 more correct picks.
Matchup probabilities use the existing order-invariant approach: half the
forward-minus-reversed logits, which cancels the corner intercept.

For context, corner-aware versions score 62.08% (experimental baseline) and
66.87% (candidate). Those scores use a different prediction definition and must
not be substituted for the matchup tool's 63.67%. The candidate's 2026 score
must also not be presented as accuracy across all 6,718 historical fights.

## Uncertainty and data limitations

A paired bootstrap with 2,000 resamples of event dates estimates a 95% interval
of **-0.80 to +5.27 percentage points** for the matchup accuracy gain. It includes
zero: the sample does not establish an accuracy improvement at that confidence
level. Both probability scores nevertheless improved on development and 2026.

The existing backtest reads current career style tags and full-roster imputation
averages. They are not guaranteed to have existed at the time of older fights.
Both experimental arms therefore disable style features and use fixed physical
imputation constants. This improves the comparison but is not a full leakage
audit: shared current physical-attribute snapshots, fitted division offsets,
and previously selected rating settings still remain. Also, this is a
retrospective temporal holdout; the project's earlier reports already examined
2026 data. A genuinely prospective test requires locking the recipe before
predicting future events.

No betting odds were used. No production database, weights, or API behavior was
changed. Six new-feature and existing rating tests passed, including checks that
current, same-day and future outcomes cannot affect rolling inputs.

## Reproduction

From the repository root, run:

```powershell
python -m elo.experiment_recent --output-dir work/prediction-experiment
pytest tests/test_recent_experiment.py elo/tests -q
```

The experiment module and its tests are saved in the local repository. A copy of
the module accompanies this report and can be placed at `elo/experiment_recent.py`.
`metrics.json` contains exact scores and the database SHA-256; `predictions.csv`
contains per-fight development and selected-candidate holdout predictions.

Recommended next step: complete point-in-time feature auditing and run the
candidate in shadow mode on future cards before replacing the deployed model.
