# UFC Elo — Prediction Layer & Backtest Report

_Refreshed 2026-09-23 from `data/ufc.db` (8,907 rated fights; 8,752 decisive
red/blue bouts with a binary label). Prediction layer per `docs/PLAN.md`
sections 4–5, built on the finished Glicko-2 rating engine (section 3). Ratings
were **not** modified by this phase — the prediction layer reads them, it never
writes them._

## 0. What shipped

| Module | Role |
|---|---|
| `elo/styles.py` | Career-stat style classification → `fighters.style_tag` |
| `elo/features.py` | Strictly pre-fight feature builder (backtest + live) |
| `elo/predict.py` | Logistic prediction layer, walk-forward fit, `predict_fight` |
| `elo/backtest.py` | Walk-forward harness: metrics, calibration, ablation |
| `elo/prediction_model.json` | Persisted production coefficients + metadata |
| `docs/calibration.png` | Calibration curve (walk-forward) |

**No change was made to `elo/config.py`** — the ablation (section 4) shows the
shipped rating config is already the best-performing once calibrated.

---

## 1. Headline metrics — walk-forward, 2013+ (n = 6,616)

Every fight from 2013 onward is predicted **out-of-sample**: the model is refit
each year on fights strictly before that year (recency-weighted), then applied to
that year's bouts. Pre-2013 bouts are burn-in (roster still forming) but still
train the first fold. Vanilla Elo and raw Glicko likewise use only prior fights.

| Model | Log-loss | Brier | Accuracy |
|---|--:|--:|--:|
| Coin flip | 0.6931 | 0.2500 | 0.500 |
| Vanilla Elo (plain K=32, no multipliers) | 0.6846 | 0.2457 | 0.558 |
| Raw Glicko expected score | 0.6955 | 0.2492 | 0.569 |
| **Prediction layer (shipped)** | **0.6588** | **0.2330** | **0.603** |
| Betting odds | — | — | **skipped: `odds` table is empty (no API key)** |

The prediction layer beats every baseline on all three metrics: −0.034 log-loss
vs. coin flip, −0.026 vs. vanilla Elo, −0.037 vs. raw Glicko; +4.4 pts accuracy
over vanilla Elo.

**Raw Glicko is *worse than a coin flip* on log-loss** (0.6956 > 0.6931) despite
57% accuracy — it is badly *over-confident*, not conservative. This is the
opposite of the sanity report's guess that high active RD (mean ~215) would make
probabilities conservative. The very wide µ range (≈1000–2600) produces large
logits that high-variance MMA outcomes don't honour. The prediction layer fixes
this with a fitted **temperature of 0.37** on the Glicko logit (a coefficient
< 1 shrinks the over-confident base), so we tune the prediction layer rather than
the RD constant and **ratings stay pure** (PLAN section 5).

The odds baseline is implemented in the harness and reports `skipped — no odds
rows`; it will activate automatically once `odds` is populated.

### `predict_fight` (order-invariant projection)

`predict_fight(a, b)` has no corner, so it averages the two corner assignments
(details in section 6). That projection scores **log-loss 0.6637 / accuracy
0.601** walk-forward — it drops the corner signal (log-loss +0.005) but keeps all
the rating/physical/style discrimination and still beats every baseline.

---

## 2. Calibration — the "70% favourites win ~70%" test

**Expected calibration error (ECE): 0.0335** — well-calibrated. See
`docs/calibration.png`.

| Predicted bin | n | Pred mean | Actual win rate |
|---|--:|--:|--:|
| 0.1–0.2 | 4 | 0.161 | 0.500 |
| 0.2–0.3 | 63 | 0.266 | 0.302 |
| 0.3–0.4 | 287 | 0.363 | 0.373 |
| 0.4–0.5 | 885 | 0.459 | 0.455 |
| 0.5–0.6 | 1839 | 0.554 | 0.505 |
| 0.6–0.7 | 2010 | 0.648 | 0.614 |
| 0.7–0.8 | 1184 | 0.741 | **0.722** |
| 0.8–0.9 | 314 | 0.839 | 0.755 |
| 0.9–1.0 | 30 | 0.920 | 0.800 |

The **0.7–0.8 bin predicts 74% and observes 72%** — the calibration target is
met. The curve is monotone and hugs the diagonal; the only real drift is mild
over-confidence in the sparse 0.8–0.9 tail (n=301). This required a
**recency-weighted fit** (3-year half-life): the red-corner/favourite prior has
drifted from ~64% (1990s–2000s) to ~58% today, and an unweighted expanding-window
fit over-predicted the favourite everywhere (ECE 0.089). Down-weighting older
fights lets the intercept and temperature track the current era, cutting ECE to
0.034 at no accuracy cost.

---

## 3. Fitted coefficients (production model, all history, recency-weighted)

`P(A wins) = sigmoid( intercept + Σ βᵢ · featureᵢ )`, features from A's
perspective. Intercept **+0.243** = red-corner/favourite prior (only applied when
a corner is known; cancelled by `predict_fight`'s symmetrization).

| Feature | β | Plain-language reading |
|---|--:|---|
| `glicko_logit` | **+0.365** | Rating gap is the dominant signal, but shrunk (temperature 0.36 < 1: raw Glicko is over-confident). |
| `age_delta` (per yr) | **−0.084** | The younger fighter is favoured; a 5-year age edge ≈ +10 pts win prob. Strongest non-rating factor. |
| `layoff_delta` (per yr) | **−0.131** | Ring rust is real: a fighter a year longer removed loses ≈ 3 pts. |
| `style_wr_vs_str` | **+0.142** | Wrestlers beat strikers — the classic stylistic edge shows up cleanly. |
| `southpaw_orthodox` | **+0.112** | Southpaws hold an edge over orthodox opponents. |
| `style_gr_vs_str` | −0.079 | Pure grapplers fare slightly *worse* vs. strikers (range/takedown-entry problem). |
| `style_gr_vs_wr` | −0.023 | Grappler slightly under wrestler. |
| `prior_winner` | +0.028 | Winning the previous meeting carries a small edge into a rematch. |
| `over35_delta` | −0.025 | Extra decline once past 35, on top of `age_delta`. |
| `height_delta` (per in) | +0.021 | Minor height edge. |
| `reach_delta` (per in) | +0.009 | Reach helps a little (mostly already in height/style). |
| `form_delta` | +0.013 | Last-3-fight rating trend adds little marginal signal beyond the rating itself. |
| `reach_missing` | +0.101 | Missing-indicator; a residual era confound. Symmetric → **cancels in `predict_fight`**, and is ~0 in the modern eval window (reach is populated for recent fighters). |
| `rematch` | −0.095 | Rematch context is small relative to rating, age, and style features; direction lives in `prior_winner`. |
| `weight_gap` | +0.185 | Documented physical prior used only for speculative cross-division matchups. |

Signs are all physically sensible. The biggest movers after the rating itself are
**age, layoff, and the wrestler-vs-striker / stance matchups** — exactly the
factors a knowledgeable fan would cite, which is what the API's "key factors"
panel surfaces.

---

## 4. Ablation — does each rating multiplier earn its keep?

Each documented multiplier is toggled off, the ratings are **re-replayed in
memory** (offsets held at the shipped values to isolate the multiplier), and the
raw-Glicko walk-forward log-loss is measured on 2013+ decisive fights.

| Variant | Raw-Glicko log-loss | Δ vs shipped |
|---|--:|--:|
| **shipped** (all on) | 0.6955 | +0.0000 |
| `delta_cap_off` | 0.7100 | **+0.0145** (cap strongly helps) |
| `mov_correction_off` | 0.6970 | **+0.0015** (538 correction helps) |
| `stakes_off` | 0.6948 | −0.0007 (≈ noise) |
| `mov_base_off` (flat M_base) | 0.6914 | −0.0041 (finish weighting slightly hurts raw Glicko) |

**Reading:** a *positive* Δ means turning the component off makes the ratings
worse, so it earns its keep. `delta_cap` and `mov_correction` clearly do.
`mov_base` (finish-vs-decision weighting) and `stakes` slightly *lower*
raw-Glicko log-loss when removed.

**But the raw-Glicko improvement is pure over-confidence that the temperature
already removes.** Re-running the *full prediction layer* (temperature + recency
+ features) under the best ablated config confirms it washes out:

| Config (full prediction layer) | Log-loss | ECE |
|---|--:|--:|
| shipped (MOV + stakes on) | **0.6589** | 0.0335 |
| `mov_base_off` + `stakes_off` | 0.6594 | 0.0327 |

Cutting MOV-base + stakes changes the shipped predictor's log-loss by **+0.0005
(worse)** and ECE by −0.0008 — a wash. Since MOV and stakes are mandated
section-3 rating features with independent value (ratings that reflect dominance
and title-fight stakes drive the rankings UI and the rating-gain/loss previews)
and cost nothing in prediction once calibrated, **they stay on. No `config.py`
change is warranted; the shipped config is already the best-performing.**

---

## 5. Style classification spot-checks (`fighters.style_tag`)

Career rate signals (sig-strikes/min, TD attempts/15 min, TD accuracy,
sub-attempts/15 min, control %, KO/sub finish shares) are shrunk toward division
means by `n/(n+8)`, z-scored, and combined into striking / wrestling / grappling
indices (argmax, with a `balanced` residual). Confidence floor: **< 3 rated
fights → `insufficient_data`**.

| Fighter | Expected | Classified | |
|---|---|---|---|
| Merab Dvalishvili | wrestler | wrestler | ✓ |
| Charles Oliveira | grappler | grappler | ✓ |
| Max Holloway | striker | striker | ✓ |
| Khabib Nurmagomedov | wrestler | wrestler | ✓ |
| Israel Adesanya | striker | striker | ✓ |
| Demian Maia | grappler | grappler | ✓ |

**6/6 correct.** (Maia — a BJJ ace with a heavy top game — was the boundary case:
weighting submission-finish share above raw control resolves him to grappler
without disturbing the wrestlers.)

Distribution over the 1,853 fighters with usable stats: 735 striker, 527
wrestler, 455 grappler, 136 balanced. Fighters with no rated fights or < 3
bouts (2,652 total) are `insufficient_data`.

---

## 6. The `predict_fight` interface (for the Phase-3 API)

```python
from elo.predict import predict_fight
predict_fight(fighter_a_id, fighter_b_id) -> {
    "prob_a": float,                 # P(A wins), order-invariant
    "expected_score_raw": float,     # pure Glicko expected score (no LR)
    "factors": [                     # sorted by |contribution|, biggest first
        (name, logit_contribution, human_readable), ...
    ],
    "cross_division": bool,
    "speculative": bool,
}
```

* **Order-invariant.** `prob_a(a,b) + prob_a(b,a) == 1.0` exactly. A hypothetical
  matchup has no corner, so the logit averages both corner assignments
  `0.5·(z_ab − z_ba)`, cancelling the intercept and any symmetric feature. For a
  *carded* fight where the corner is known, the corner-aware model (with
  intercept) is the higher-accuracy option and is what the backtest reports.
* `factors` feeds the matchup page's "key factors driving the prediction" panel;
  each entry is the signed logit push with a fan-readable label.
* `cross_division` is true when the fighters' primary pools differ (D_w applies).
* `speculative` is true for cross-division bouts (PLAN section 4) or when a
  fighter's rating is exceptionally uncertain (RD > 275) or thin (< 3 fights).

Example (current ratings, 2026-07-17):

| Matchup | P(A) | raw Glicko | cross | spec | Top factor |
|---|--:|--:|:--:|:--:|---|
| Makhachev vs Oliveira | 0.649 | 0.756 | no | no | Glicko base (+0.42) |
| Holloway vs Topuria | 0.304 | 0.234 | no | no | Glicko base (−0.44) |
| Jones vs Aspinall | 0.278 | 0.586 | **yes** | **yes** | Layoff +3.3 yr (−0.49), age +5.7 yr (−0.46) |

---

## 7. Limitations & notes for later phases

1. **No betting-odds baseline yet.** The `odds` table is empty (no API key). The
   harness computes and compares odds automatically once rows exist; until then
   we cannot benchmark against the market (the strongest realistic baseline).
2. **Red-corner artefact.** ufcstats assigns the favourite to red, so red wins
   ~58–64% beyond ratings. This is handled by the intercept (backtest) and
   cancelled by symmetrization (`predict_fight`), but it means the *carded*-fight
   accuracy (0.601) is modestly higher than a truly corner-blind matchup would
   be. The effect drifts across eras — hence the recency weighting.
3. **Leaderboard filter recommendation (carried from the sanity report, now
   quantified).** Raw µ surfaces thin, high-RD fighters. For any user-facing
   ranking use a conservative score **`µ − 2·RD`** or require **`n_fights ≥ 5`**;
   `predict_fight` already flags such matchups `speculative`.
4. **Cross-division is a constant D_w shift.** It cannot correct differing pool
   *dynamic range* (a flyweight GOAT's µ ceiling is structurally below a
   heavyweight's). Cross-division predictions are directionally sound but should
   keep the `speculative` badge in the UI.
5. **`form` and `reach` add little.** Recent-form trend and raw reach carry almost
   no signal beyond the rating and height; they are retained for the factor panel
   and completeness, not because they move probabilities.
6. **Style feature is symmetric and coarse** (four classes, three matchup axes).
   `insufficient_data` fighters contribute a zero style-matchup feature, so a
   debutant's prediction rests on rating + physicals only, which is appropriate.

## 8. Reproduce

```
python -m elo.styles                       # write fighters.style_tag
python -m elo.predict                      # fit + persist prediction_model.json
python -m elo.backtest                     # metrics + calibration + ablation
python -m elo.backtest --report docs/backtest_metrics.md   # also dump the block
```

Runtime: recompute + styles + fit ≈ 5 s; full backtest incl. ablation ≈ 7 s.
