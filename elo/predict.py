"""Win-probability prediction layer (logistic regression over Glicko + features).

PLAN section 4: win probability is the Glicko expected score adjusted in logit
space by a logistic regression fit on the historical features from
:mod:`elo.features`.  Ratings stay pure -- **this layer never writes ratings**.

Design
------
* The model is ``P(A wins) = sigmoid( intercept + sum_i beta_i * feature_i )``
  over the ordered :data:`elo.features.FEATURE_NAMES`.  The ``glicko_logit``
  feature is the Glicko expected score in logit space; its coefficient is the
  calibration *temperature* (PLAN section 5).  Empirically the temperature is
  **< 1**: the very wide mu range together with high active RD makes the raw
  Glicko expectation *over*-confident, so the layer shrinks it -- the opposite
  of what the sanity report guessed, and the reason we tune a temperature here
  rather than the RD constant (ratings stay pure).

* **The intercept is a corner/favourite prior.**  ufcstats assigns the favourite
  to the red corner, so red wins ~58-64% -- more than ratings alone predict.
  The model is fit on the natural (A = red) orientation *with* an intercept that
  absorbs this; it is a genuine, known signal for any *carded* fight and is what
  the walk-forward backtest evaluates.

* **Order invariance of ``predict_fight``.**  A hypothetical matchup has no
  corner, so ``predict_fight`` averages the two corner assignments
  (``z_ab`` and ``z_ba``): ``prob_a = sigmoid((z_ab - z_ba) / 2)``.  This cancels
  the intercept and any symmetric feature (``reach_missing``, ``rematch``),
  leaving an order-invariant prediction driven by the antisymmetric factors.

* **Walk-forward.**  :func:`walk_forward_predictions` refits the model each year
  on strictly-earlier fights; the persisted production coefficients come from a
  final fit on all history.

Run:  python -m elo.predict          # fit on all history, write the model JSON
      python -m elo.predict --demo   # fit + a couple of example predictions
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.db import get_conn  # noqa: E402
from elo import config  # noqa: E402
from elo.features import (  # noqa: E402
    FEATURE_LABELS,
    FEATURE_NAMES,
    FighterState,
    build_training_frame,
    compute_features,
    compute_impute_means,
    sigmoid,
)

MODEL_PATH = Path(__file__).resolve().parent / "prediction_model.json"

#: Features that are unchanged when the two fighters are swapped (all others
#: negate).  Used to symmetrize ``predict_fight`` for order-invariance.
SYMMETRIC_FEATURES: frozenset[str] = frozenset({"reach_missing", "rematch"})

#: First calendar year evaluated in the walk-forward backtest.  Fights before
#: this are burn-in: the pre-2013 roster is still forming and ratings are noisy,
#: but those bouts still train the first fold.
WALK_FORWARD_START_YEAR: int = 2013

#: Exponential recency half-life (years) for training sample weights.  The
#: red-corner/favourite prior drifts across eras (~64% in the 1990s-2000s vs
#: ~58% today); down-weighting older fights lets the intercept and temperature
#: track the current era and roughly halves the calibration error (ECE 0.089 ->
#: 0.034) with no accuracy cost.  3 years balances calibration against sample
#: size (~500 fights/year, so several effective years of data remain).
RECENCY_HALF_LIFE_YEARS: float = 3.0

#: A live prediction is flagged speculative when the bout crosses divisions
#: (PLAN section 4), or a fighter's rating is exceptionally uncertain (RD) or
#: thin (n_fights).  The RD threshold sits above the active-RD mean (~215; see
#: the sanity report) so it flags only genuinely stale/unproven ratings, not
#: every ordinary matchup.
SPECULATIVE_RD: float = 275.0
SPECULATIVE_MIN_FIGHTS: int = 3


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------
def recency_weights(years: np.ndarray, ref_year: int,
                    half_life: float = RECENCY_HALF_LIFE_YEARS) -> np.ndarray:
    """Exponential-decay sample weights: 0.5 per ``half_life`` years of age."""
    return 0.5 ** ((ref_year - years) / half_life)


def fit_coefficients(X: np.ndarray, y: np.ndarray,
                     sample_weight: np.ndarray | None = None
                     ) -> tuple[np.ndarray, float]:
    """Fit the corner-aware logistic model; return (feature coefs, intercept).

    The ``weight_gap`` coefficient is **pinned** to
    :data:`elo.config.WEIGHT_GAP_LOGIT_PER_10LB` and never estimated from data:
    same-division fights (99.8% of the corpus) carry ``weight_gap == 0`` and the
    handful of crossover bouts are too red-corner-confounded to identify it (a
    free fit collapses to ~0), so it is a documented physical prior instead.
    """
    from sklearn.linear_model import LogisticRegression

    # Mild L2 keeps early (data-poor) walk-forward folds stable; with n >> k it
    # barely moves the full-data fit.  Intercept absorbs the red-corner prior;
    # optional recency weights let it track the current era.
    clf = LogisticRegression(fit_intercept=True, C=4.0, max_iter=2000)
    clf.fit(X, y, sample_weight=sample_weight)
    coef = clf.coef_.ravel().copy()
    if "weight_gap" in FEATURE_NAMES:
        coef[FEATURE_NAMES.index("weight_gap")] = config.WEIGHT_GAP_LOGIT_PER_10LB
    return coef, float(clf.intercept_[0])


def _predict_proba(X: np.ndarray, coef: np.ndarray, intercept: float) -> np.ndarray:
    z = intercept + X @ coef
    return 1.0 / (1.0 + np.exp(-z))


# ---------------------------------------------------------------------------
# Walk-forward evaluation (used by the backtest harness)
# ---------------------------------------------------------------------------
def walk_forward_predictions(df: pd.DataFrame,
                             start_year: int = WALK_FORWARD_START_YEAR
                             ) -> pd.DataFrame:
    """Return ``df`` rows from ``start_year`` on with an out-of-sample ``p_pred``.

    For each evaluation year the model is refit on every fight strictly before
    that year, then applied to that year's fights -- no future information ever
    enters a prediction.
    """
    df = df.copy()
    df["year"] = df["date"].str[:4].astype(int)
    Xall = df[list(FEATURE_NAMES)].to_numpy(float)
    yall = df["y"].to_numpy(float)

    years = df["year"].to_numpy()
    out_idx: list[int] = []
    out_p: list[float] = []
    for yr in range(start_year, df["year"].max() + 1):
        train_mask = (df["year"] < yr).to_numpy()
        eval_mask = (df["year"] == yr).to_numpy()
        if eval_mask.sum() == 0 or train_mask.sum() < 200:
            continue
        w = recency_weights(years[train_mask], yr)
        coef, intercept = fit_coefficients(Xall[train_mask], yall[train_mask], w)
        p = _predict_proba(Xall[eval_mask], coef, intercept)
        out_idx.extend(np.where(eval_mask)[0].tolist())
        out_p.extend(p.tolist())

    res = df.iloc[out_idx].copy()
    res["p_pred"] = out_p
    return res


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------
def fit_and_save(conn: sqlite3.Connection, path: Path = MODEL_PATH,
                 offsets: dict[str, float] | None = None) -> dict:
    """Fit the production model on all history and write ``prediction_model.json``."""
    from elo.recompute import load_offsets

    offsets = offsets if offsets is not None else load_offsets()
    impute = compute_impute_means(conn)
    df = build_training_frame(conn, offsets, impute)
    X = df[list(FEATURE_NAMES)].to_numpy(float)
    y = df["y"].to_numpy(float)
    years = df["date"].str[:4].astype(int).to_numpy()
    w = recency_weights(years, int(years.max()))
    coef, intercept = fit_coefficients(X, y, w)

    payload = {
        "coefficients": {n: float(c) for n, c in zip(FEATURE_NAMES, coef)},
        "intercept": float(intercept),
        "feature_order": list(FEATURE_NAMES),
        "impute": impute,
        "offsets": offsets,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_train_fights": int(len(df)),
            "model": ("logistic regression with intercept (corner-aware); "
                      "predict_fight symmetrizes over corner for order-invariance"),
            "recency_half_life_years": RECENCY_HALF_LIFE_YEARS,
            "glicko_temperature": float(coef[0]),
            "corner_prior_intercept": float(intercept),
            "note": ("glicko_logit coefficient is the calibration temperature "
                     "(<1 => raw Glicko is over-confident); intercept is the "
                     "red-corner/favourite prior; features per "
                     "elo.features.FEATURE_NAMES"),
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load_model(path: Path = MODEL_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found -- run `python -m elo.predict` to fit it first.")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Live prediction  (predict_fight -- the Phase-3 API contract)
# ---------------------------------------------------------------------------
def _current_state(conn: sqlite3.Connection, fighter_id: int,
                   offsets: dict[str, float], as_of: date) -> FighterState | None:
    """Assemble a live :class:`FighterState` from ratings_current + history."""
    r = conn.execute(
        "SELECT division, mu, rd, n_fights, last_fight FROM ratings_current "
        "WHERE fighter_id = ? ORDER BY n_fights DESC LIMIT 1", (fighter_id,)
    ).fetchone()
    if r is None:
        return None
    div = r["division"]
    attr = conn.execute(
        "SELECT dob, height_in, reach_in, stance, style_tag FROM fighters "
        "WHERE id = ?", (fighter_id,)
    ).fetchone()

    # Recent form: last-3-fight mu trend from elo_history.
    hist = conn.execute(
        "SELECT mu_pre, mu_post, date FROM elo_history WHERE fighter_id = ? "
        "ORDER BY date DESC, fight_id DESC LIMIT 4", (fighter_id,)
    ).fetchall()
    form = 0.0
    if hist:
        latest_post = hist[0]["mu_post"]
        base = hist[-1]["mu_pre"]  # up to 4 back -> ~last 3 completed transitions
        form = latest_post - base

    layoff = None
    if r["last_fight"]:
        layoff = (as_of - date.fromisoformat(r["last_fight"][:10])).days

    age = None
    if attr and attr["dob"]:
        try:
            age = (as_of - date.fromisoformat(attr["dob"][:10])).days / 365.25
        except ValueError:
            age = None

    return FighterState(
        fighter_id=fighter_id, mu=r["mu"], rd=r["rd"], division=div,
        offset=offsets.get(div, 0.0),
        reach_in=attr["reach_in"] if attr else None,
        height_in=attr["height_in"] if attr else None,
        age=age, stance=attr["stance"] if attr else None,
        style=attr["style_tag"] if attr else None,
        form=form, layoff_days=layoff, n_fights=r["n_fights"] or 0,
    )


def _prior_meeting(conn: sqlite3.Connection, a_id: int, b_id: int
                   ) -> tuple[bool, int | None]:
    """Return (has_met_before, winner_id_of_last_meeting)."""
    rows = conn.execute(
        """
        SELECT f.winner_id, e.date FROM fights f JOIN events e ON f.event_id = e.id
        WHERE f.result_current != 'upcoming'
          AND ((f.fighter_red_id = ? AND f.fighter_blue_id = ?)
            OR (f.fighter_red_id = ? AND f.fighter_blue_id = ?))
        ORDER BY e.date DESC
        """, (a_id, b_id, b_id, a_id)
    ).fetchall()
    if not rows:
        return False, None
    return True, rows[0]["winner_id"]


def _human(name: str, feat_val: float, contrib: float, a: FighterState,
           b: FighterState) -> str:
    """Plain-language description of one factor's push (from A's side)."""
    who = "favours A" if contrib > 0 else "favours B" if contrib < 0 else "neutral"
    if name == "glicko_logit":
        return f"Glicko rating/deviation base ({who})"
    if name == "reach_delta":
        return f"Reach delta {feat_val:+.1f} in ({who})"
    if name == "height_delta":
        return f"Height delta {feat_val:+.1f} in ({who})"
    if name == "age_delta":
        return f"Age delta {feat_val:+.1f} yr ({who})"
    if name == "over35_delta":
        return f"Past-35 decline ({who})"
    if name == "layoff_delta":
        return f"Layoff delta {feat_val:+.2f} yr ({who})"
    if name == "form_delta":
        return f"Recent form (last 3) ({who})"
    if name == "southpaw_orthodox":
        return f"Southpaw/orthodox stance matchup ({who})"
    if name.startswith("style_"):
        return f"{FEATURE_LABELS[name]} style matchup ({who})"
    if name == "rematch":
        return "Rematch" if feat_val else "First meeting"
    if name == "prior_winner":
        return f"Prior meeting ({who})"
    if name == "weight_gap":
        lbs = feat_val * 10.0
        return (f"Weight/size gap {lbs:+.0f} lb across divisions "
                f"(model-extrapolated, {who})")
    if name == "reach_missing":
        return "Reach data imputed" if feat_val else "Reach data present"
    return FEATURE_LABELS.get(name, name)


def predict_fight(fighter_a_id: int, fighter_b_id: int,
                  conn: sqlite3.Connection | None = None,
                  model: dict | None = None,
                  as_of: date | None = None) -> dict:
    """Predict A vs B.  **The exact interface consumed by the Phase-3 API.**

    Returns::

        {
          "prob_a": float,                 # P(A wins)
          "expected_score_raw": float,     # pure Glicko expected score (no LR)
          "factors": [(name, logit_contribution, human_readable), ...],
          "cross_division": bool,
          "speculative": bool,
        }

    ``factors`` is sorted by absolute logit contribution (biggest driver first)
    and feeds the matchup page's "key factors driving the prediction" panel.
    """
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        model = model or load_model()
        offsets = model.get("offsets", {})
        impute = model.get("impute")
        coef = model["coefficients"]
        as_of = as_of or date.today()

        sa = _current_state(conn, fighter_a_id, offsets, as_of)
        sb = _current_state(conn, fighter_b_id, offsets, as_of)
        if sa is None or sb is None:
            missing = fighter_a_id if sa is None else fighter_b_id
            raise ValueError(f"fighter {missing} has no current rating.")

        met, last_winner = _prior_meeting(conn, fighter_a_id, fighter_b_id)

        def feats_for(x: FighterState, o: FighterState, x_id: int) -> dict:
            fe = compute_features(x, o, impute)
            fe["rematch"] = 1.0 if met else 0.0
            fe["prior_winner"] = (0.0 if not met or last_winner is None
                                  else 1.0 if last_winner == x_id else -1.0)
            return fe

        feats = feats_for(sa, sb, fighter_a_id)     # A in the red corner
        feats_ba = feats_for(sb, sa, fighter_b_id)  # B in the red corner

        # Order-invariant: average over which fighter takes the red corner, which
        # cancels the intercept and any symmetric feature.
        z = 0.5 * (sum(coef[n] * feats[n] for n in FEATURE_NAMES)
                   - sum(coef[n] * feats_ba[n] for n in FEATURE_NAMES))
        prob_a = sigmoid(z)

        factors = []
        for n in FEATURE_NAMES:
            contrib = 0.5 * coef[n] * (feats[n] - feats_ba[n])
            if abs(contrib) < 1e-9 and n != "glicko_logit":
                continue
            factors.append((n, float(contrib), _human(n, feats[n], contrib, sa, sb)))
        factors.sort(key=lambda t: abs(t[1]), reverse=True)

        cross = sa.division != sb.division
        speculative = (cross
                       or sa.rd > SPECULATIVE_RD or sb.rd > SPECULATIVE_RD
                       or sa.n_fights < SPECULATIVE_MIN_FIGHTS
                       or sb.n_fights < SPECULATIVE_MIN_FIGHTS)
        return {
            "prob_a": float(prob_a),
            "expected_score_raw": float(sigmoid(feats["glicko_logit"])),
            "factors": factors,
            "cross_division": bool(cross),
            "speculative": bool(speculative),
        }
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Fit + persist the prediction model.")
    ap.add_argument("--demo", action="store_true",
                    help="After fitting, run a couple of example predictions.")
    args = ap.parse_args()

    conn = get_conn()
    try:
        payload = fit_and_save(conn)
        print(f"Fitted on {payload['metadata']['n_train_fights']} fights -> "
              f"{MODEL_PATH.name}")
        print(f"  glicko temperature: {payload['metadata']['glicko_temperature']:.3f}")
        print(f"  corner prior (intercept): {payload['intercept']:+.3f}")
        print("  coefficients:")
        for n, c in payload["coefficients"].items():
            print(f"    {n:<20} {c:+.4f}")

        if args.demo:
            model = payload
            examples = [("Merab Dvalishvili", "Sean O'Malley"),
                        ("Islam Makhachev", "Charles Oliveira")]
            print("\nDemo predictions:")
            for na, nb in examples:
                ra = conn.execute("SELECT id FROM fighters WHERE name=?", (na,)).fetchone()
                rb = conn.execute("SELECT id FROM fighters WHERE name=?", (nb,)).fetchone()
                if not ra or not rb:
                    continue
                res = predict_fight(ra[0], rb[0], conn=conn, model=model)
                print(f"  {na} vs {nb}: P(A)={res['prob_a']:.3f} "
                      f"raw={res['expected_score_raw']:.3f} "
                      f"cross={res['cross_division']} spec={res['speculative']}")
                for name, contrib, human in res["factors"][:4]:
                    print(f"      {human:<45} {contrib:+.3f}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
