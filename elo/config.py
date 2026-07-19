"""All tunable constants for the UFC Elo rating engine.

Every value here is either mandated by ``docs/PLAN.md`` section 3 or a
documented, tunable extension of it.  Nothing in the engine hard-codes a
magic number: if a knob matters, it lives here with an explanation.

Terminology:
  * ``mu``      -- a fighter's rating on the display scale (mean 1500).
  * ``rd``      -- rating deviation on the display scale (uncertainty).
  * ``sigma``   -- Glicko-2 volatility (dimensionless, internal scale).
  * ``D_w``     -- fitted cross-division offset (see calibrate_offsets.py).

Deviations from textbook Glicko-2 (all per PLAN section 3):
  1. MOV (margin of victory) and stakes are a post-multiplier applied to the
     *rating delta only*, never to the RD/sigma update.  See MOV_* / STAKES_*.
  2. RD inactivity growth uses an explicit, tunable variance-per-year constant
     rather than pure volatility accrual, so layoffs inflate RD at a
     controllable, interpretable rate.  See RD_INACTIVITY_C_PER_YEAR.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Glicko-2 core parameters
# ---------------------------------------------------------------------------

#: System constant tau.  Constrains how much volatility can change per period.
#: Glickman recommends 0.3-1.2; smaller = steadier ratings.  PLAN uses the
#: canonical example value.
GLICKO_TAU: float = 0.5

#: Initial rating for a fighter with no history and no usable pre-UFC prior.
INITIAL_MU: float = 1500.0

#: Initial rating deviation for a brand-new fighter (maximum uncertainty).
INITIAL_RD: float = 350.0

#: Initial Glicko-2 volatility (Glickman's default).
INITIAL_SIGMA: float = 0.06

#: RD is never allowed above this (both for newcomers and for inactivity
#: inflation).  Equals INITIAL_RD by construction.
RD_MAX: float = 350.0

#: A well-established, very active fighter's RD floor.  Prevents the deviation
#: from collapsing so far that a single upset can barely move the rating.
RD_MIN: float = 40.0

# ---------------------------------------------------------------------------
# RD inactivity growth (documented extension)
# ---------------------------------------------------------------------------

#: Rating-point standard deviation of "skill drift" accrued per sqrt-year of
#: inactivity.  Applied in quadrature before each update and again when
#: reporting current ratings:
#:     rd <- min(sqrt(rd**2 + (C)**2 * years_since_last_fight), RD_MAX)
#: Chosen so a fighter with an active RD (~70) inflates toward the ~300s after
#: roughly 3 idle years, matching how quickly UFC layoffs erode certainty.
#: Tunable; larger = layoffs punish certainty faster.
RD_INACTIVITY_C_PER_YEAR: float = 80.0

#: Days in one nominal "rating period" (used only to express elapsed time in
#: years for the growth formula; the engine does one Glicko update per bout).
DAYS_PER_YEAR: float = 365.25

# ---------------------------------------------------------------------------
# Margin-of-victory base multipliers  M_base(method, round)
# ---------------------------------------------------------------------------
# A decisive early finish carries more signal than a split decision.  These
# scale the *rating delta*; they never touch RD/sigma.  Values per PLAN.

#: Finish (KO/TKO or SUB) base multiplier, keyed by the round it happened in.
M_BASE_FINISH_BY_ROUND: dict[int, float] = {
    1: 1.50,   # first-round finish -- maximal signal
    2: 1.35,
    3: 1.20,
    4: 1.10,   # championship-round finishes
    5: 1.10,
}

#: Unanimous decision -- the neutral reference point.
M_BASE_UNANIMOUS_DECISION: float = 1.00

#: Split or majority decision -- close, noisy result -> dampened.
M_BASE_SPLIT_MAJORITY_DECISION: float = 0.85

#: Disqualification win -- weak signal about actual skill.
M_BASE_DQ: float = 0.60

#: Draw -- both fighters score 0.5, small update.
M_BASE_DRAW: float = 0.75

# ---------------------------------------------------------------------------
# 538-style autocorrelation MOV correction (applied on the PRE-fight gap)
# ---------------------------------------------------------------------------
# M_final = M_base * MOV_CORRECTION_K / ((mu_winner - mu_loser)*MOV_GAP_SCALE
#                                        + MOV_CORRECTION_K)
# For a heavy favourite (positive gap) the factor < 1 (their win was expected,
# so a big finish is discounted).  For an upset (negative gap) the factor > 1,
# amplifying the underdog's decisive win.  This counters the autocorrelation
# that would otherwise let dominant favourites inflate without bound.

#: Numerator/curvature constant (538's value).
MOV_CORRECTION_K: float = 2.2

#: Scales the pre-fight rating gap into the correction.  0.001 => a 1000-point
#: gap contributes 1.0 to the denominator.
MOV_GAP_SCALE: float = 0.001

# ---------------------------------------------------------------------------
# Stakes multipliers  S
# ---------------------------------------------------------------------------
# Higher-stakes bouts are treated as slightly more informative (better camps,
# peak performances, sterner tests).  Applied to the rating delta alongside
# MOV.  Precedence is highest-applicable wins (title > interim > main > co-main)
STAKES_UNDISPUTED_TITLE: float = 1.25
STAKES_INTERIM_TITLE: float = 1.15
STAKES_MAIN_EVENT: float = 1.10       # non-title main event
STAKES_CO_MAIN: float = 1.05
STAKES_DEFAULT: float = 1.00

#: card_position values that denote main event / co-main (1 = main, 2 = co).
CARD_POSITION_MAIN_EVENT: int = 1
CARD_POSITION_CO_MAIN: int = 2

# ---------------------------------------------------------------------------
# Hard cap on the compounded rating delta
# ---------------------------------------------------------------------------
#: After MOV*stakes are applied, the absolute per-bout rating change (in
#: display points) is clamped to this.  Stops a single high-RD upset with a
#: 1.5*1.25 multiplier from producing an absurd swing.  Tunable.
MAX_RATING_DELTA: float = 250.0

# ---------------------------------------------------------------------------
# Division-change handling
# ---------------------------------------------------------------------------
#: Fraction of the carried-over rating regressed toward the pool mean when a
#: fighter changes divisions.  mu_seed = mean + (1 - frac)*(mu_translated-mean)
DIVISION_CHANGE_REGRESSION_FRACTION: float = 0.25

#: Extra RD variance (display points) added in quadrature when seeding a new
#: division, reflecting fresh uncertainty about the fighter at the new weight.
DIVISION_CHANGE_RD_BUMP: float = 100.0

# ---------------------------------------------------------------------------
# Pre-UFC seeding (hook only -- fighters.pre_ufc_record is EMPTY in the current
# DB, so this NO-OPS to the default 1500/350 for every fighter today).
# ---------------------------------------------------------------------------
#: Range within which a parsed pre-UFC prior may seed a debut rating.  A strong
#: record from a top promotion seeds near the top of the range; an unknown
#: record seeds at INITIAL_MU.  Wired but dormant until a source populates
#: pre_ufc_record.
SEED_MU_RANGE: tuple[float, float] = (1450.0, 1620.0)

#: Promotion-tier bumps (display points above INITIAL_MU) the seeding hook
#: would apply once pre_ufc_record exists.  Documented target behaviour.
SEED_PROMOTION_TIER_BUMP: dict[str, float] = {
    "bellator": 100.0,
    "pfl": 100.0,
    "one": 100.0,
    "major_regional": 60.0,
    "other": 0.0,
}

# ---------------------------------------------------------------------------
# Divisions & offsets
# ---------------------------------------------------------------------------
#: Division that anchors the fitted offset table at D_w = 0 (see PLAN).
OFFSET_ANCHOR_DIVISION: str = "Lightweight"

#: L2 regularization strength for the offset fit (shrinks sparse divisions'
#: offsets toward 0).  Tunable in calibrate_offsets.py.
#:
#: Raised from 1.0 to 6.0 when the isotonic constraint (below) was added: under
#: monotonicity the ~76 confounded crossover fights identify essentially one
#: thing -- that Heavyweight rates above the rest -- and collapse every other
#: division to a common level.  At lambda=1 that loads the lone surviving
#: Heavyweight offset to ~+185; lambda=6 holds it near the ~+105 the prior
#: unconstrained fit and domain knowledge support.  The physical cross-division
#: ladder used in *predictions* now comes from the explicit ``weight_gap``
#: feature, not from these (deliberately conservative) rating offsets.
OFFSET_L2_LAMBDA: float = 6.0

#: Enforce a monotonic (non-decreasing with weight) offset table.  With only ~76
#: heavily red-corner-confounded crossover fights, an unconstrained fit produced
#: physically impossible inversions (Middleweight rated *below* Lightweight,
#: Featherweight *above* it).  The fit is therefore constrained to the canonical
#: light-to-heavy order (see divisions.CANONICAL_WEIGHT_ORDER): a heavier
#: division can never carry a lower pound-for-pound offset than a lighter one.
OFFSET_MONOTONIC: bool = True

# ---------------------------------------------------------------------------
# Cross-division weight-gap penalty (prediction layer)
# ---------------------------------------------------------------------------
# The single biggest prediction defect (see docs/optimization_report.md) was
# that extreme cross-division mismatches -- a 145-lb featherweight vs a 250-lb
# heavyweight -- returned ~32% for the smaller fighter.  The cause: the only
# channel a weight gap had into the prediction was the fitted division offset,
# which (a) is tiny and unreliable off ~76 confounded crossover fights and
# (b) enters through ``glicko_logit`` and is then multiplied by the calibration
# temperature (~0.37), shrinking it further.
#
# The fix adds an EXPLICIT ``weight_gap`` feature to the prediction layer with a
# coefficient PINNED to the constant below (never fitted by the logistic
# regression) -- because the crossover data cannot identify it: the red corner
# wins ~93% of historical crossover bouts whether heavier or lighter, so a free
# fit collapses to ~0.  The pinned value is a physically-motivated prior,
# extrapolated LINEARLY per pound for gaps beyond the ~1 division the data can
# speak to.  Predictions riding on it are flagged ``speculative`` and are
# documented as model-extrapolated, not data-validated.

#: Logit penalty applied per 10 lbs of representative-weight advantage to the
#: heavier fighter (equivalently, per 10 lbs of deficit to the lighter one).
#: Antisymmetric, so it survives ``predict_fight``'s corner symmetrization and
#: is the dominant driver of extreme cross-division probabilities.  Calibrated
#: so a champion-vs-champion two-division jump is a hard underdog and a
#: flyweight-vs-heavyweight gap lands in the low single digits, matching how
#: lopsided such fights are understood to be.  Set to 0.0 to disable.
WEIGHT_GAP_LOGIT_PER_10LB: float = 0.185

#: Divisor (lbs) that scales the raw weight difference into the feature's units;
#: 10 lbs so the pinned coefficient above reads "per 10 lbs".
WEIGHT_GAP_SCALE_LB: float = 10.0

#: Path (relative to this package) of the fitted offset table recompute reads.
OFFSETS_FILENAME: str = "division_offsets.json"

#: SQLite busy timeout (ms) for the short write transactions in recompute.
SQLITE_BUSY_TIMEOUT_MS: int = 30000
