"""Pre-fight feature construction for the win-probability model.

PLAN section 4: the prediction layer adjusts the Glicko expected score in logit
space using features that are computable **strictly from information available
before the fight**.  This module is the single source of truth for those
features; it is consumed both by the walk-forward fit / backtest
(:mod:`elo.backtest`, historical fights via ``elo_history``) and by live
predictions (:mod:`elo.predict`, current ratings).

Every feature is oriented from **fighter A's perspective** (A = the red corner
for historical fights) and is antisymmetric where it should be, so swapping A/B
negates the logit.  The features:

  * ``glicko_logit``      -- RD-aware, cross-pool (D_w-adjusted) Glicko expected
                             score in logit space.  Its fitted coefficient is the
                             calibration *temperature* (PLAN section 5): a value
                             > 1 sharpens the deviation-aware base probability,
                             correcting the conservativeness that the high active
                             RD (mean ~215) would otherwise cause.
  * ``reach_delta``       -- A minus B reach (inches), mean-imputed when missing.
  * ``reach_missing``     -- 1 if either reach was imputed (missing-indicator).
  * ``height_delta``      -- A minus B height (inches), mean-imputed.
  * ``age_delta``         -- A minus B age (years) at the fight date.
  * ``over35_delta``      -- max(0, age_A-35) - max(0, age_B-35): the accelerated
                             decline past 35 the PLAN calls out.
  * ``layoff_delta``      -- A minus B layoff since last bout (years); ring rust.
  * ``form_delta``        -- A minus B recent form (last-3-fight mu trend / 100).
  * ``southpaw_orthodox`` -- +1 A southpaw vs B orthodox, -1 the reverse, else 0.
  * ``style_wr_vs_str``   -- +1 A wrestler vs B striker, -1 the reverse, else 0.
  * ``style_gr_vs_str``   -- +1 A grappler vs B striker, -1 the reverse, else 0.
  * ``style_gr_vs_wr``    -- +1 A grappler vs B wrestler, -1 the reverse, else 0.
  * ``rematch``           -- 1 if the two have met before (symmetric).
  * ``prior_winner``      -- +1 A won the previous meeting, -1 B won, else 0.
  * ``weight_gap``        -- (weight_A - weight_B) / 10 lbs, from the fighters'
                             home-division representative weights.  0 for a
                             same-division bout.  Its coefficient is PINNED (not
                             fitted): the ~76 crossover fights are too few and
                             too red-corner-confounded to identify it, so it
                             carries a physically-motivated per-pound prior,
                             linearly extrapolated for gaps beyond the data.
                             This is the channel that makes extreme
                             cross-division mismatches land where they should.

No feature reads a fighter's *own* future; ``elo_history.mu_pre`` / ``rd_pre``
are by construction the ratings as they stood immediately before each bout.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from elo import config
from elo.divisions import division_weight
from elo.glicko2 import Rating, expected_score
from elo.styles import (
    STYLE_GRAPPLER,
    STYLE_STRIKER,
    STYLE_WRESTLER,
)

# Ordered feature list -- the model's design matrix uses exactly this order.
FEATURE_NAMES: tuple[str, ...] = (
    "glicko_logit",
    "reach_delta",
    "reach_missing",
    "height_delta",
    "age_delta",
    "over35_delta",
    "layoff_delta",
    "form_delta",
    "southpaw_orthodox",
    "style_wr_vs_str",
    "style_gr_vs_str",
    "style_gr_vs_wr",
    "rematch",
    "prior_winner",
    "weight_gap",
)

# Plain-language templates for the API factor breakdown (filled per prediction).
FEATURE_LABELS: dict[str, str] = {
    "glicko_logit": "Glicko rating & deviation",
    "reach_delta": "Reach advantage",
    "reach_missing": "Reach data missing",
    "height_delta": "Height advantage",
    "age_delta": "Age advantage (younger)",
    "over35_delta": "Past-35 decline",
    "layoff_delta": "Layoff / ring rust",
    "form_delta": "Recent form (last 3)",
    "southpaw_orthodox": "Stance matchup",
    "style_wr_vs_str": "Wrestler vs striker",
    "style_gr_vs_str": "Grappler vs striker",
    "style_gr_vs_wr": "Grappler vs wrestler",
    "rematch": "Rematch",
    "prior_winner": "Won the prior meeting",
    "weight_gap": "Weight / size gap",
}

# Feature-scaling / imputation defaults.  The actual impute means are fitted from
# data and stored in the model JSON; these are only fallbacks.
DEFAULT_IMPUTE = {"reach_in": 71.5, "height_in": 70.0, "age": 29.0}

#: Recent-form trend divisor (rating points) so the feature is O(1).
FORM_SCALE: float = 100.0
#: Layoff expressed in years; a debut's layoff is treated as this many days.
DEFAULT_LAYOFF_DAYS: float = 365.0
#: Cap on layoff so a decade-long comeback doesn't dominate the linear term.
MAX_LAYOFF_DAYS: float = 365.0 * 4


def logit(p: float) -> float:
    p = min(1 - 1e-9, max(1e-9, p))
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def glicko_expected(mu_a: float, rd_a: float, off_a: float,
                    mu_b: float, rd_b: float, off_b: float) -> float:
    """RD-aware, cross-pool Glicko expected score of A vs B.

    The fitted division offsets ``off_a``/``off_b`` translate each fighter onto
    the common pound-for-pound scale before the deviation-aware comparison, so
    cross-division matchups use the same machinery as same-division ones.
    """
    a = Rating(mu_a + off_a, rd_a)
    b = Rating(mu_b + off_b, rd_b)
    return expected_score(a, b)


# ---------------------------------------------------------------------------
# Pre-fight state of one fighter (assembled from history or current ratings)
# ---------------------------------------------------------------------------
@dataclass
class FighterState:
    """Everything about one fighter needed to build features, as of a bout."""

    fighter_id: int
    mu: float
    rd: float
    division: str
    offset: float = 0.0
    reach_in: float | None = None
    height_in: float | None = None
    age: float | None = None          # years at the fight date
    stance: str | None = None
    style: str | None = None
    form: float = 0.0                 # last-3-fight mu trend (rating points)
    layoff_days: float | None = None
    n_fights: int = 0


def _style_axis(style_a: str | None, style_b: str | None,
                pos: str, neg: str) -> float:
    """+1 if A is ``pos`` and B is ``neg``; -1 if reversed; else 0."""
    if style_a == pos and style_b == neg:
        return 1.0
    if style_a == neg and style_b == pos:
        return -1.0
    return 0.0


def compute_features(a: FighterState, b: FighterState,
                     impute: dict[str, float] | None = None) -> dict[str, float]:
    """Pure feature vector for the bout A vs B (A's perspective)."""
    imp = {**DEFAULT_IMPUTE, **(impute or {})}

    # --- Glicko base (the offset term the LR sharpens) ---------------------
    exp_a = glicko_expected(a.mu, a.rd, a.offset, b.mu, b.rd, b.offset)
    f: dict[str, float] = {"glicko_logit": logit(exp_a)}

    # --- physical deltas with imputation ----------------------------------
    reach_missing = a.reach_in is None or b.reach_in is None
    ra = a.reach_in if a.reach_in is not None else imp["reach_in"]
    rb = b.reach_in if b.reach_in is not None else imp["reach_in"]
    f["reach_delta"] = ra - rb
    f["reach_missing"] = 1.0 if reach_missing else 0.0

    ha = a.height_in if a.height_in is not None else imp["height_in"]
    hb = b.height_in if b.height_in is not None else imp["height_in"]
    f["height_delta"] = ha - hb

    aa = a.age if a.age is not None else imp["age"]
    ab = b.age if b.age is not None else imp["age"]
    f["age_delta"] = aa - ab
    f["over35_delta"] = max(0.0, aa - 35.0) - max(0.0, ab - 35.0)

    # --- layoff (years, capped) -------------------------------------------
    la = a.layoff_days if a.layoff_days is not None else DEFAULT_LAYOFF_DAYS
    lb = b.layoff_days if b.layoff_days is not None else DEFAULT_LAYOFF_DAYS
    la = min(la, MAX_LAYOFF_DAYS)
    lb = min(lb, MAX_LAYOFF_DAYS)
    f["layoff_delta"] = (la - lb) / 365.0

    # --- recent form ------------------------------------------------------
    f["form_delta"] = (a.form - b.form) / FORM_SCALE

    # --- stance matchup ---------------------------------------------------
    f["southpaw_orthodox"] = _stance_axis(a.stance, b.stance)

    # --- style matchups ---------------------------------------------------
    f["style_wr_vs_str"] = _style_axis(a.style, b.style, STYLE_WRESTLER, STYLE_STRIKER)
    f["style_gr_vs_str"] = _style_axis(a.style, b.style, STYLE_GRAPPLER, STYLE_STRIKER)
    f["style_gr_vs_wr"] = _style_axis(a.style, b.style, STYLE_GRAPPLER, STYLE_WRESTLER)

    # --- cross-division weight gap (pinned-coefficient prior) --------------
    # Signed representative-weight difference between the two fighters' home
    # divisions, in 10-lb units; 0 for a same-division bout.  Antisymmetric.
    wa = division_weight(a.division)
    wb = division_weight(b.division)
    if wa is not None and wb is not None:
        f["weight_gap"] = (wa - wb) / config.WEIGHT_GAP_SCALE_LB
    else:
        f["weight_gap"] = 0.0

    # rematch / prior_winner are filled by the caller (needs pairing history);
    # default to "first meeting" here.
    f.setdefault("rematch", 0.0)
    f.setdefault("prior_winner", 0.0)
    return f


def _stance_axis(stance_a: str | None, stance_b: str | None) -> float:
    """+1 if A southpaw vs B orthodox, -1 if A orthodox vs B southpaw, else 0."""
    if stance_a == "Southpaw" and stance_b == "Orthodox":
        return 1.0
    if stance_a == "Orthodox" and stance_b == "Southpaw":
        return -1.0
    return 0.0


# ---------------------------------------------------------------------------
# Historical training-frame builder (backtest + fit)
# ---------------------------------------------------------------------------
def compute_impute_means(conn: sqlite3.Connection) -> dict[str, float]:
    """Roster means for reach/height (age handled per-fight) used to impute."""
    row = conn.execute(
        "SELECT AVG(reach_in), AVG(height_in) FROM fighters"
    ).fetchone()
    return {
        "reach_in": float(row[0]) if row[0] is not None else DEFAULT_IMPUTE["reach_in"],
        "height_in": float(row[1]) if row[1] is not None else DEFAULT_IMPUTE["height_in"],
        "age": DEFAULT_IMPUTE["age"],
    }


def _fighter_attrs(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT id, dob, height_in, reach_in, stance, style_tag FROM fighters"
    ).fetchall()
    return {r["id"]: dict(dob=r["dob"], height_in=r["height_in"],
                          reach_in=r["reach_in"], stance=r["stance"],
                          style=r["style_tag"]) for r in rows}


def _age_years(dob: str | None, on: date) -> float | None:
    if not dob:
        return None
    try:
        d = date.fromisoformat(dob[:10])
    except ValueError:
        return None
    return (on - d).days / 365.25


def build_training_frame(conn: sqlite3.Connection,
                         offsets: dict[str, float] | None = None,
                         impute: dict[str, float] | None = None) -> pd.DataFrame:
    """One row per decisive historical fight with all features + label ``y``.

    Uses ``elo_history`` for each fighter's pre-fight (mu, rd, division), so
    every feature is strictly as-of the bout.  ``y = 1`` iff the red corner won.
    Draws / no-contests are excluded (no binary label).
    """
    offsets = offsets or {}
    impute = impute or compute_impute_means(conn)
    attrs = _fighter_attrs(conn)

    # Pre-fight ratings per (fight, fighter) from elo_history.
    hist = pd.read_sql_query(
        "SELECT fighter_id, fight_id, date, division, mu_pre, rd_pre, mu_post "
        "FROM elo_history", conn)
    hist = hist.sort_values(["fighter_id", "date", "fight_id"]).reset_index(drop=True)

    # Recent form (last-3 mu trend) and layoff, computed per fighter in sequence.
    hist["prev_date"] = hist.groupby("fighter_id")["date"].shift(1)
    hist["mu_pre_3ago"] = hist.groupby("fighter_id")["mu_pre"].shift(3)
    hist["mu_pre_1st"] = hist.groupby("fighter_id")["mu_pre"].transform("first")
    # form = mu_pre now - mu_pre 3 fights ago (partial window falls back to first).
    hist["form"] = hist["mu_pre"] - hist["mu_pre_3ago"].fillna(hist["mu_pre_1st"])
    hist["layoff_days"] = (pd.to_datetime(hist["date"]) -
                           pd.to_datetime(hist["prev_date"])).dt.days

    hist_by_fight: dict[int, dict[int, pd.Series]] = {}
    for _, r in hist.iterrows():
        hist_by_fight.setdefault(int(r["fight_id"]), {})[int(r["fighter_id"])] = r

    # Fight metadata (corners, winner, date) in chronological order.
    fights = pd.read_sql_query(
        """
        SELECT f.id, e.date, f.fighter_red_id AS red, f.fighter_blue_id AS blue,
               f.winner_id, f.result_current
        FROM fights f JOIN events e ON f.event_id = e.id
        WHERE f.result_current != 'upcoming' AND e.date IS NOT NULL
        ORDER BY e.date ASC, f.id ASC
        """, conn)

    seen: dict[frozenset, tuple[str, int | None]] = {}  # pair -> (date, winner)
    records: list[dict] = []
    for _, fr in fights.iterrows():
        fid = int(fr["id"])
        red, blue = int(fr["red"]), int(fr["blue"])
        pair = frozenset((red, blue))
        prior = seen.get(pair)

        result = fr["result_current"]
        winner = fr["winner_id"]
        # Record the meeting for future rematch lookups regardless of result.
        if result in ("win", "dq") and winner is not None:
            label_ok = True
        else:
            label_ok = False  # draw / nc: no binary label, but still a "meeting".

        rows = hist_by_fight.get(fid)
        if rows and red in rows and blue in rows and label_ok:
            hr, hb = rows[red], rows[blue]
            when = date.fromisoformat(fr["date"][:10])
            sa = _state_from_hist(hr, attrs.get(red, {}), offsets, when)
            sb = _state_from_hist(hb, attrs.get(blue, {}), offsets, when)
            feats = compute_features(sa, sb, impute)
            feats["rematch"] = 1.0 if prior is not None else 0.0
            if prior is not None and prior[1] is not None:
                feats["prior_winner"] = 1.0 if prior[1] == red else -1.0
            else:
                feats["prior_winner"] = 0.0
            rec = {"fight_id": fid, "date": fr["date"],
                   "y": 1 if int(winner) == red else 0,
                   "cross_division": sa.division != sb.division}
            rec.update(feats)
            records.append(rec)

        # Update the rematch memory (append-only, keeps the most recent meeting).
        seen[pair] = (fr["date"], int(winner) if pd.notna(winner) else None)

    return pd.DataFrame.from_records(records)


def _state_from_hist(hr: pd.Series, attr: dict, offsets: dict[str, float],
                     when: date) -> FighterState:
    div = hr["division"]
    return FighterState(
        fighter_id=int(hr["fighter_id"]),
        mu=float(hr["mu_pre"]), rd=float(hr["rd_pre"]),
        division=div, offset=offsets.get(div, 0.0),
        reach_in=attr.get("reach_in"), height_in=attr.get("height_in"),
        age=_age_years(attr.get("dob"), when),
        stance=attr.get("stance"), style=attr.get("style"),
        form=float(hr["form"]) if pd.notna(hr["form"]) else 0.0,
        layoff_days=float(hr["layoff_days"]) if pd.notna(hr["layoff_days"]) else None,
    )


def design_matrix(df: pd.DataFrame) -> np.ndarray:
    """Extract the ordered feature matrix from a training frame."""
    return df[list(FEATURE_NAMES)].to_numpy(dtype=float)
