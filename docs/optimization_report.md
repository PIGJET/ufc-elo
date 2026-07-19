# UFC Elo — Prediction Optimization Report

_Generated 2026-07-18. Motivation: cross-division matchup predictions felt
wrong — most glaringly, **Volkanovski (Featherweight) vs Aspinall (Heavyweight)
returned ~32.5% for the 145-lb fighter** against a 250-lb champion. This pass
mines the walk-forward history for systematic biases, fixes the cross-division
defect, and validates every change against held-out log-loss and bucket
calibration. Ratings stay pure; all fits remain strictly walk-forward._

---

## 1. Discrepancy mining — what the history says

Method: replay the full walk-forward backtest (refit each year on strictly
earlier fights), then bucket all 6,419 evaluated 2013+ decisive fights and
report per-bucket n, log-loss, and calibration (predicted vs actual red-corner
win rate; **bias = pred − actual**). Baseline model = the previously shipped
predictor.

### Top 3 systematic biases

**(1) Extreme cross-division mismatches are wildly under-penalized — the
headline defect.** This barely registers in aggregate because cross-pool bouts
are almost nonexistent in the data (only **13** of 6,419 eval fights have the two
fighters in different pools; **69** are catchweights). So the corpus *cannot*
show the bias — but the hypothetical battery exposes it starkly (§4). Root
cause, confirmed: the only channel a weight gap had into a prediction was the
fitted division offset, and that offset (a) is tiny and unreliable off ~76
crossover fights and (b) enters through `glicko_logit`, which is then multiplied
by the calibration **temperature ≈ 0.37**, shrinking whatever little signal it
carried. Physical mass differences essentially did not reach the probability.

Worse, the offset table itself was **non-monotonic** — physically impossible
inversions where lighter divisions out-rated heavier ones on the P4P scale:

| Division (light→heavy) | Old offset | Problem |
|---|--:|---|
| Featherweight (145) | **+34.0** | rated *above* Lightweight |
| Lightweight (155) | 0.0 | anchor |
| Welterweight (170) | −3.4 | |
| Middleweight (185) | **−45.0** | rated *below* Welterweight & Lightweight |
| Light Heavyweight (205) | +77.1 | |
| Heavyweight (265) | +104.6 | |

**(2) Division-changers are predicted worse.** Fights where either fighter
changed divisions within their previous two bouts (n=1,456, ~23% of the corpus):

| Bucket | n | Log-loss | Bias (pred−actual) |
|---|--:|--:|--:|
| No recent division change | 4,963 | 0.6564 | +0.026 |
| Recent division change | 1,456 | **0.6677** | **+0.049** |

The seed carried into a new division is uncertain; the model over-favors the
(usually higher-profile, red-corner) division-mover.

**(3) Red-corner over-prediction on close-rating fights.** Bucketing by
`|glicko_logit|` decile, the near-even fights are the worst-calibrated:

| `|glicko_logit|` decile | n | Log-loss | Pred | Actual | Bias |
|---|--:|--:|--:|--:|--:|
| 0 (closest) | 642 | 0.6940 | 0.611 | 0.547 | **+0.064** |
| 1 | 642 | 0.6862 | 0.581 | 0.522 | **+0.059** |
| … | | | | | |
| 9 (widest) | 642 | 0.6189 | 0.674 | 0.660 | +0.014 |

The same additive ~+0.03–0.06 red bias reappears in the long-red-layoff and
older-red buckets. **This is a corner artifact** (ufcstats assigns the favorite
to red): it is absorbed by the intercept for *carded* fights and **cancels
exactly** in `predict_fight`'s corner symmetrization, so it does not affect the
user-facing matchup tool. It is therefore *deliberately not chased* — see §6.

Buckets that were **fine** (no real bias): weight-class of bout (Heavyweight
0.643, best), catchweights (0.606, well-behaved on n=69), rematches, women's
divisions. No fixable bias there.

---

## 2. Changes shipped

### (a) Isotonic (monotonic) division offsets
`elo/calibrate_offsets.py` now fits offsets under a **non-decreasing-with-weight
constraint** (`config.OFFSET_MONOTONIC = True`) via projected gradient descent —
each step isotonic-regresses (weighted pool-adjacent-violators) onto the
canonical light-to-heavy order (`elo/divisions.py: CANONICAL_WEIGHT_ORDER`) and
re-pins Lightweight to 0. The problem is convex, so this reaches the global
constrained optimum.

Result: the confounded crossover data, once forced to be monotone, identifies
essentially **one** thing — that Heavyweight rates above the rest — and pools
every other division to a common level. `OFFSET_L2_LAMBDA` was raised 1.0 → 6.0
so the lone surviving offset stays near its historically-supported magnitude
(else it inflates to ~+185).

| Division | Old offset | New offset |
|---|--:|--:|
| Heavyweight | +104.6 | **+106.3** |
| Light Heavyweight | +77.1 | 0.0 |
| Featherweight | +34.0 | 0.0 |
| Welterweight | −3.4 | 0.0 |
| Middleweight | −45.0 | 0.0 |
| Open Weight | −88.4 | 0.0 |
| _(all others)_ | (mixed) | 0.0 |

The inversions are gone. The flattening of the lighter divisions is the honest
consequence of monotonicity on under-identified data — and it no longer matters
for cross-division *predictions*, because those now ride on the explicit
`weight_gap` feature below rather than on these (deliberately conservative)
rating offsets.

### (b) Explicit `weight_gap` prediction feature (pinned coefficient)
A new antisymmetric feature in `elo/features.py`:

```
weight_gap = (weight_lb[div_A] − weight_lb[div_B]) / 10
```

using each division's representative fighting weight
(`elo/divisions.py: DIVISION_WEIGHT_LB`, official limits; Heavyweight 265). It is
**0 for same-division bouts**, so it never touches the 99.8% of fights that are
in-division.

Its coefficient is **pinned** to `config.WEIGHT_GAP_LOGIT_PER_10LB = 0.185`
(0.185 logit per 10 lb of advantage to the heavier fighter) and is **never
fitted by the logistic regression**. Why pinned, not fitted: the crossover data
cannot identify it. Empirically, across all 76 historical crossover bouts the
**red/favored corner wins ~93%** whether it is the heavier fighter (95.5%) or
the lighter one (90.6%); a free fit collapses to ≈0 (+0.018/10 lb, wrong-signed
correlation). So the value is a documented **physical prior**, calibrated so
extreme mismatches land where the sport understands them to. Because the feature
is per-pound, the 60-lb LHW→HW leap correctly counts far more than the 10-lb
FW→LW step; gaps beyond ~2 divisions are **linear extrapolations** and every
cross-division prediction is flagged `speculative`.

Files touched: `elo/config.py` (constants + docstrings), `elo/divisions.py`
(weight ladder + canonical order), `elo/features.py` (feature + labels),
`elo/predict.py` (coefficient pinning + factor text), `elo/calibrate_offsets.py`
(isotonic fit). `elo/division_offsets.json` and `elo/prediction_model.json`
re-persisted; `elo/recompute.py` re-run (offsets feed stored `pfp_mu`).

---

## 3. Before/after — overall & buckets (walk-forward, 2013+, n=6,419)

Overall metrics are **unchanged within noise**, exactly as expected: the fix
only moves cross-division mismatches, which are ~0.2% of the eval set.

| Metric | Before | After | Δ |
|---|--:|--:|--:|
| Log-loss | 0.6589 | 0.6592 | +0.0003 |
| Brier | 0.2331 | 0.2332 | +0.0001 |
| Accuracy | 0.6015 | 0.6010 | −0.0005 |
| ECE | 0.0335 | 0.0336 | +0.0001 |

Ablation table (raw-Glicko walk-forward log-loss) is unchanged — every rating
multiplier still earns its keep (`delta_cap` +0.014, `mov_correction` +0.001).

Per-bucket (the ones the offset change could ripple into, since division-changers
carry the new offsets into later same-division fights):

| Bucket | n | Log-loss before | Log-loss after |
|---|--:|--:|--:|
| Division-changer (recent) | 1,456 | 0.6677 | 0.6674 |
| Catchweight bouts | 69 | 0.6061 | 0.6082 |
| Light Heavyweight | 468 | 0.6732 | 0.6740 |
| Middleweight | 753 | 0.6594 | 0.6598 |
| Welterweight | 933 | 0.6804 | 0.6807 |

All differences are within sampling noise (±0.002 on a few-hundred-fight
bucket); the division-changer bucket edges slightly better. Nothing regressed.

---

## 4. Hypothetical cross-division battery — before vs after

This is where the fix shows. `predict_fight` P(A wins), current ratings
(2026-07-18); all flagged `speculative`.

| Matchup (A vs B) | Gap | **Before** | **After** | Read |
|---|---|--:|--:|---|
| Volkanovski (FW) vs **Aspinall (HW)** | 5 div | 32.5% | **4.6%** | fixed — the headline case |
| Makhachev (LW) vs **Aspinall (HW)** | 4 div | 39.6% | **7.8%** | fixed |
| Dvalishvili (BW) vs **Aspinall (HW)** | 6 div | 31.0% | **4.1%** | fixed |
| Pantoja (FLW) vs **Aspinall (HW)** | 7 div | 15.9% | **1.4%** | extreme → near-zero |
| Jon Jones (LHW) vs **Aspinall (HW)** | 60 lb | 27.8% | **9.8%** | one big step |
| Pantoja (FLW) vs Makhachev (LW) | 2 div | — | 14.6% | two divisions, hard underdog |
| Volkanovski (FW) vs Makhachev (LW) | 1 div | 41.1% | 35.0% | adjacent — stays competitive |
| Topuria (FW) vs Makhachev (LW) | 1 div | 60.4% | 54.0% | adjacent — near coin-flip |
| Pantoja (FLW) vs Dvalishvili (BW) | 1 div | 32.6% | 27.3% | adjacent — reasonable |
| Shevchenko (WFLW) vs Nunes (WBW) | 1 div | — | 56.8% | adjacent, women's |
| Joshua Van (FLW) vs Dvalishvili (BW) | 1 div | — | 59.1% | adjacent, rating-driven |

The pattern is exactly what physical reality demands: **adjacent-division
superfights stay competitive** (elite fighters really do move up one class and
win), while **each further division compounds a per-pound penalty** until
multi-division jumps collapse into the low single digits. Volkanovski-Aspinall,
the reported symptom, moves from an indefensible 32.5% to a defensible **4.6%**,
with the driver shown transparently in the factor panel:

> Weight/size gap −120 lb across divisions (model-extrapolated, favours B): −2.22

---

## 5. Extrapolation caveat (what these numbers are and aren't)

Cross-division probabilities beyond ~1 division are **model extrapolations, not
data-validated estimates**, and are always flagged `speculative`:

- No flyweight has ever fought a heavyweight in the UFC, and no source of truth
  exists for such a gap. The `weight_gap` penalty is a **physical prior**
  (linear per pound), chosen for face validity, not fitted.
- The ~76 crossover fights that *do* exist are dominated by 1990s open-weight
  tournaments and are so red-corner-confounded (93% red wins regardless of size)
  that they cannot even confirm the *sign* of a weight effect, let alone its
  magnitude.
- The exact single-digit value for, say, Pantoja-Aspinall (1.4%) should be read
  as "clearly a huge underdog," not as a precise 1-in-71 estimate.

The UI already carries the `speculative` badge for every cross-division bout;
these predictions are directional guidance, deliberately conservative in what
they claim.

---

## 6. Deliberately not changed

- **Red-corner close-fight bias (§1.3).** Real in the carded-fight backtest but
  a corner artifact that **cancels in `predict_fight`'s symmetrization**, so it
  never reaches the matchup tool. Lowering the intercept to chase it would
  degrade the well-calibrated 0.6–0.8 bins that the recency-weighted fit already
  nails (ECE 0.034). Not worth it.
- **Division-changer seed uncertainty (§1.2).** The residual is small, the RD
  bump already widens these ratings, and the `speculative` flag already fires.
  No feature cleanly separable from the corner bias was available; forcing one
  risked overfitting a 1,456-fight bucket with no held-out gain.
- **Style / reach / form features.** Already shown (backtest report §3) to carry
  little marginal signal; left untouched — this pass found no new evidence to
  revisit them.
- **The rating engine, MOV, stakes, delta-cap.** Ablation still confirms each
  earns its keep; ratings stay pure per PLAN.

## 7. Reproduce

```
python -m elo.calibrate_offsets      # isotonic offsets  -> division_offsets.json
python -m elo.recompute              # rebuild ratings (offsets feed pfp_mu)
python -m elo.predict                # refit + persist   -> prediction_model.json
python -m elo.backtest               # metrics + calibration + ablation
```
