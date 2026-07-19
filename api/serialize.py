"""JSON serialization helpers shared across routers.

Conventions (PLAN section 7 / task contract):

* snake_case keys everywhere.
* nulls are **present, not omitted** -- a missing rating is ``null``, a missing
  image is ``null``; the frontend can rely on the key existing.
* every rating is reported as ``{"mu": float|null, "rd": float|null}``.

The comparison builder (:func:`build_comparison`) produces the one payload shape
shared by an events-card fight and a free-form matchup: fighter cards, the
per-fighter stat tabs, the prediction (with factor breakdown) and the rating
win/loss previews.  Fighters without a ``ratings_current`` row (debutants on an
upcoming card) degrade gracefully: their rating is ``null`` and prediction /
preview come back ``null`` rather than raising.
"""
from __future__ import annotations

from typing import Any

from elo import config
from elo.engine import preview_update
from elo.glicko2 import Rating
from elo.predict import predict_fight

from api.state import state


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _ratio(landed: int, attempted: int) -> float | None:
    """Accuracy as a 0-1 fraction, or ``None`` when nothing was attempted."""
    return None if not attempted else round(landed / attempted, 4)


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------
def record_of(fighter_row) -> dict[str, Any]:
    """The bio W-L-D record from a ``fighters`` row (null-safe)."""
    w = fighter_row["wins"] or 0
    losses = fighter_row["losses"] or 0
    d = fighter_row["draws"] or 0
    return {"wins": w, "losses": losses, "draws": d, "display": f"{w}-{losses}-{d}"}


def best_rating_row(conn, fighter_id: int):
    """The fighter's primary ``ratings_current`` row (most rated fights)."""
    return conn.execute(
        "SELECT division, mu, rd, sigma, pfp_mu, n_fights, last_fight "
        "FROM ratings_current WHERE fighter_id = ? "
        "ORDER BY n_fights DESC LIMIT 1",
        (fighter_id,),
    ).fetchone()


def rating_in_division(conn, fighter_id: int, division: str):
    """The fighter's ``ratings_current`` row for a specific division, or None."""
    return conn.execute(
        "SELECT division, mu, rd, sigma, pfp_mu, n_fights, last_fight "
        "FROM ratings_current WHERE fighter_id = ? AND division = ?",
        (fighter_id, division),
    ).fetchone()


def rating_pair(row) -> dict[str, Any]:
    """``{mu, rd}`` from a ratings row, or both null when the row is absent."""
    if row is None:
        return {"mu": None, "rd": None}
    return {"mu": _round(row["mu"], 1), "rd": _round(row["rd"], 1)}


def stat_block(fighter_id: int) -> dict[str, Any]:
    """Career comparison stats for the events / matchup stat tabs.

    Win-by breakdown, sig-strike totals + accuracy + per-minute rate, takedown
    totals + accuracy, and control-time (seconds + share of fight time).  All
    derived from the pre-built career cache -- no per-request aggregation.
    """
    c = state.career_for(fighter_id)
    minutes = c["total_fight_seconds"] / 60.0 if c["total_fight_seconds"] else 0.0
    return {
        "wins_by_ko": c["wins_by_ko"],
        "wins_by_sub": c["wins_by_sub"],
        "wins_by_decision": c["wins_by_decision"],
        "wins_by_dq": c["wins_by_dq"],
        "first_round_finishes": c["first_round_finishes"],
        "knockdowns": c["knockdowns"],
        "sub_attempts": c["sub_attempts"],
        "sig_strikes_landed": c["sig_strikes_landed"],
        "sig_strikes_attempted": c["sig_strikes_attempted"],
        "sig_strike_accuracy": _ratio(c["sig_strikes_landed"], c["sig_strikes_attempted"]),
        "sig_strikes_per_min": round(c["sig_strikes_landed"] / minutes, 2) if minutes else None,
        "takedowns_landed": c["takedowns_landed"],
        "takedowns_attempted": c["takedowns_attempted"],
        "takedown_accuracy": _ratio(c["takedowns_landed"], c["takedowns_attempted"]),
        "control_time_seconds": c["control_time_seconds"],
        "control_time_share": (
            round(c["control_time_seconds"] / c["total_fight_seconds"], 4)
            if c["total_fight_seconds"] else None
        ),
        "total_fight_seconds": c["total_fight_seconds"],
        "fights_with_stats": c["fights_with_stats"],
    }


def fighter_card(conn, fighter_id: int) -> dict[str, Any]:
    """Compact fighter object used on cards (rankings rows, matchup corners)."""
    row = conn.execute(
        "SELECT id, name, nickname, image_url, wins, losses, draws "
        "FROM fighters WHERE id = ?",
        (fighter_id,),
    ).fetchone()
    if row is None:
        # Referenced but unknown fighter (a TBD placeholder on a partial card).
        return {
            "id": fighter_id, "name": None, "nickname": None, "image_url": None,
            "record": None, "mu": None, "rd": None, "division": None,
        }
    rating = best_rating_row(conn, fighter_id)
    return {
        "id": row["id"],
        "name": row["name"],
        "nickname": row["nickname"],
        "image_url": row["image_url"],
        "record": record_of(row),
        "division": rating["division"] if rating else None,
        "mu": _round(rating["mu"], 1) if rating else None,
        "rd": _round(rating["rd"], 1) if rating else None,
    }


# ---------------------------------------------------------------------------
# Stakes (for the rating-preview MOV/stakes multiplier)
# ---------------------------------------------------------------------------
def stakes_for_fight(fight_row) -> float:
    """Stakes multiplier for a carded fight (mirrors engine.stakes_multiplier)."""
    if fight_row["is_title"] and not fight_row["is_interim_title"]:
        return config.STAKES_UNDISPUTED_TITLE
    if fight_row["is_interim_title"]:
        return config.STAKES_INTERIM_TITLE
    if fight_row["card_position"] == config.CARD_POSITION_MAIN_EVENT or fight_row["is_main_event"]:
        return config.STAKES_MAIN_EVENT
    if fight_row["card_position"] == config.CARD_POSITION_CO_MAIN:
        return config.STAKES_CO_MAIN
    return config.STAKES_DEFAULT


# ---------------------------------------------------------------------------
# Prediction + rating preview
# ---------------------------------------------------------------------------
def _prediction(conn, red_id: int, blue_id: int, factor_limit: int | None) -> dict | None:
    """Run ``predict_fight`` (red = A) and reshape for the frontend, or None.

    Returns ``None`` if either fighter lacks a current rating.
    """
    try:
        res = predict_fight(red_id, blue_id, conn=conn, model=state.model)
    except ValueError:
        return None  # a fighter without a ratings_current row (debutant)
    factors = [
        {"name": name, "contribution": round(contrib, 4), "description": human}
        for name, contrib, human in res["factors"]
    ]
    if factor_limit is not None:
        factors = factors[:factor_limit]
    return {
        "prob_red": round(res["prob_a"], 4),
        "prob_blue": round(1.0 - res["prob_a"], 4),
        "expected_score_raw": round(res["expected_score_raw"], 4),
        "factors": factors,
        "cross_division": res["cross_division"],
        "speculative": res["speculative"],
    }


def _rating_preview(conn, red_id: int, blue_id: int, stakes: float) -> dict | None:
    """Post-fight mu for each fighter if they win vs. if they lose, or None."""
    r_row = best_rating_row(conn, red_id)
    b_row = best_rating_row(conn, blue_id)
    if r_row is None or b_row is None:
        return None
    pv = preview_update(
        Rating(r_row["mu"], r_row["rd"], r_row["sigma"] or config.INITIAL_SIGMA),
        Rating(b_row["mu"], b_row["rd"], b_row["sigma"] or config.INITIAL_SIGMA),
        pool_a=r_row["division"], pool_b=b_row["division"],
        offsets=state.offsets, stakes=stakes,
    )
    return {
        "red": {
            "division": r_row["division"],
            "current_mu": round(r_row["mu"], 1),
            "if_win_mu": round(pv["a_if_win"].rating, 1),
            "if_loss_mu": round(pv["a_if_loss"].rating, 1),
        },
        "blue": {
            "division": b_row["division"],
            "current_mu": round(b_row["mu"], 1),
            "if_win_mu": round(pv["b_if_win"].rating, 1),
            "if_loss_mu": round(pv["b_if_loss"].rating, 1),
        },
    }


def build_comparison(conn, red_id: int, blue_id: int, *,
                     stakes: float = config.STAKES_DEFAULT,
                     factor_limit: int | None = None) -> dict[str, Any]:
    """The shared events-card / matchup comparison payload.

    ``red`` is fighter A for the prediction (``prob_red`` = P(A wins)).  For a
    carded fight pass the fight's ``stakes``; for a hypothetical matchup leave
    the default (unanimous-decision, no stakes bump).  ``factor_limit`` trims the
    factor list (events cards show the top few; the matchup page shows all).
    """
    red = fighter_card(conn, red_id)
    blue = fighter_card(conn, blue_id)
    prediction = _prediction(conn, red_id, blue_id, factor_limit)
    preview = _rating_preview(conn, red_id, blue_id, stakes)

    # Rating gap on the pound-for-pound (offset-adjusted) scale so it is
    # meaningful across divisions; null if either rating is missing.
    r_row = best_rating_row(conn, red_id)
    b_row = best_rating_row(conn, blue_id)
    if r_row is not None and b_row is not None:
        gap = {
            "red_pfp_mu": round(r_row["pfp_mu"], 1),
            "blue_pfp_mu": round(b_row["pfp_mu"], 1),
            "gap": round(r_row["pfp_mu"] - b_row["pfp_mu"], 1),
        }
    else:
        gap = {"red_pfp_mu": None, "blue_pfp_mu": None, "gap": None}

    return {
        "red": red,
        "blue": blue,
        "stats": {"red": stat_block(red_id), "blue": stat_block(blue_id)},
        "prediction": prediction,
        "rating_preview": preview,
        "rating_gap": gap,
    }
