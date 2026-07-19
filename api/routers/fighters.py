"""Fighter endpoints.

* GET /api/fighters?search=  -- typeahead (accent/case-insensitive, top 10)
* GET /api/fighters/{id}     -- full profile page payload
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import db
from api.serialize import (
    best_rating_row,
    record_of,
    stat_block,
)
from api.state import state

router = APIRouter()

#: Method -> fighter-perspective result label helper inputs.
_DECISION_METHODS = {"U-DEC", "S-DEC", "M-DEC"}


def _result_for(fighter_id: int, fight_row) -> str:
    """A fight's result from ``fighter_id``'s perspective."""
    rc = fight_row["result_current"]
    if rc == "draw":
        return "draw"
    if rc == "nc":
        return "nc"
    if rc in ("win", "dq"):
        return "win" if fight_row["winner_id"] == fighter_id else "loss"
    return rc


# ---------------------------------------------------------------------------
# GET /api/fighters?search=
# ---------------------------------------------------------------------------
@router.get("/fighters")
def search_fighters(
    search: str = Query(..., min_length=1, description="name/nickname substring"),
    conn=Depends(db),
) -> dict[str, Any]:
    ids = state.search(search, limit=10)
    results = []
    for fid in ids:
        row = conn.execute(
            "SELECT id, name, nickname, division, image_url, wins, losses, draws "
            "FROM fighters WHERE id = ?", (fid,)
        ).fetchone()
        if row is None:
            continue
        rating = best_rating_row(conn, fid)
        results.append({
            "id": row["id"],
            "name": row["name"],
            "nickname": row["nickname"],
            "division": row["division"],
            "record": record_of(row)["display"],
            "image_url": row["image_url"],
            "mu": round(rating["mu"], 1) if rating else None,
        })
    return {"query": search, "results": results}


# ---------------------------------------------------------------------------
# GET /api/fighters/{id}
# ---------------------------------------------------------------------------
@router.get("/fighters/{fighter_id}")
def get_fighter(fighter_id: int, conn=Depends(db)) -> dict[str, Any]:
    bio = conn.execute(
        "SELECT id, name, nickname, dob, height_in, reach_in, leg_reach_in, "
        "stance, country, division, status, image_url, style_tag, pre_ufc_record, "
        "wins, losses, draws FROM fighters WHERE id = ?", (fighter_id,)
    ).fetchone()
    if bio is None:
        raise HTTPException(status_code=404, detail=f"fighter {fighter_id} not found")

    # Per-division ratings (+ pfp_mu, RD, n_fights).
    ratings = [
        {
            "division": r["division"],
            "mu": round(r["mu"], 1),
            "rd": round(r["rd"], 1),
            "sigma": r["sigma"],
            "pfp_mu": round(r["pfp_mu"], 1) if r["pfp_mu"] is not None else None,
            "n_fights": r["n_fights"],
            "last_fight": r["last_fight"],
        }
        for r in conn.execute(
            "SELECT division, mu, rd, sigma, pfp_mu, n_fights, last_fight "
            "FROM ratings_current WHERE fighter_id = ? ORDER BY n_fights DESC",
            (fighter_id,),
        )
    ]

    # Rank/division chips from the latest snapshot (may be ranked in several).
    snapshot_date = conn.execute(
        "SELECT MAX(snapshot_date) FROM rankings_snapshot"
    ).fetchone()[0]
    rankings = [
        {"division": r["division"], "rank": r["rank"], "movement": r["movement"]}
        for r in conn.execute(
            "SELECT division, rank, movement FROM rankings_snapshot "
            "WHERE snapshot_date = ? AND fighter_id = ? ORDER BY rank",
            (snapshot_date, fighter_id),
        )
    ]

    # Career stat aggregates for the profile stat row.
    stats = stat_block(fighter_id)
    career_stats = {
        "wins_by_ko": stats["wins_by_ko"],
        "wins_by_sub": stats["wins_by_sub"],
        "wins_by_decision": stats["wins_by_decision"],
        "first_round_finishes": stats["first_round_finishes"],
        "sig_strikes_landed": stats["sig_strikes_landed"],
        "sig_strikes_attempted": stats["sig_strikes_attempted"],
        "sig_strike_accuracy": stats["sig_strike_accuracy"],
        "sig_strikes_per_min": stats["sig_strikes_per_min"],
        "takedowns_landed": stats["takedowns_landed"],
        "takedowns_attempted": stats["takedowns_attempted"],
        "takedown_accuracy": stats["takedown_accuracy"],
        "control_time_seconds": stats["control_time_seconds"],
        "control_time_share": stats["control_time_share"],
    }

    # Elo timeline: elo_history joined to fights for opponent + result.
    timeline = []
    for h in conn.execute(
        """
        SELECT h.date, h.division, h.mu_post, h.rd_post, h.fight_id,
               f.fighter_red_id, f.fighter_blue_id, f.winner_id, f.result_current
        FROM elo_history h JOIN fights f ON h.fight_id = f.id
        WHERE h.fighter_id = ? ORDER BY h.date ASC, h.fight_id ASC
        """, (fighter_id,)
    ):
        opp_id = (h["fighter_blue_id"] if h["fighter_red_id"] == fighter_id
                  else h["fighter_red_id"])
        opp = conn.execute("SELECT name FROM fighters WHERE id = ?", (opp_id,)).fetchone()
        timeline.append({
            "date": h["date"],
            "division": h["division"],
            "mu": round(h["mu_post"], 1),
            "rd": round(h["rd_post"], 1),
            "opponent_id": opp_id,
            "opponent_name": opp["name"] if opp else None,
            "result": _result_for(fighter_id, h),
        })

    # Full fight history (most recent first).
    history_rows = conn.execute(
        """
        SELECT f.id, f.fighter_red_id, f.fighter_blue_id, f.winner_id,
               f.result_current, f.method, f.method_detail, f.round,
               f.time_seconds, f.weight_class, f.is_title,
               e.name AS event_name, e.date AS event_date
        FROM fights f JOIN events e ON f.event_id = e.id
        WHERE (f.fighter_red_id = ? OR f.fighter_blue_id = ?)
          AND f.result_current != 'upcoming'
        ORDER BY e.date DESC, f.id DESC
        """, (fighter_id, fighter_id)
    ).fetchall()

    def _history_entry(fr, *, with_images: bool) -> dict[str, Any]:
        opp_id = (fr["fighter_blue_id"] if fr["fighter_red_id"] == fighter_id
                  else fr["fighter_red_id"])
        opp = conn.execute(
            "SELECT name, image_url FROM fighters WHERE id = ?", (opp_id,)
        ).fetchone()
        entry = {
            "fight_id": fr["id"],
            "opponent_id": opp_id,
            "opponent_name": opp["name"] if opp else None,
            "event_name": fr["event_name"],
            "date": fr["event_date"],
            "result": _result_for(fighter_id, fr),
            "method": fr["method"],
            "method_detail": fr["method_detail"],
            "round": fr["round"],
            "time_seconds": fr["time_seconds"],
            "weight_class": fr["weight_class"],
            "is_title": bool(fr["is_title"]),
        }
        if with_images:
            entry["opponent_image_url"] = opp["image_url"] if opp else None
            entry["fighter_image_url"] = bio["image_url"]
        return entry

    fight_history = [_history_entry(fr, with_images=False) for fr in history_rows]
    last_fight = _history_entry(history_rows[0], with_images=True) if history_rows else None

    return {
        "fighter": {
            "id": bio["id"], "name": bio["name"], "nickname": bio["nickname"],
            "dob": bio["dob"], "height_in": bio["height_in"], "reach_in": bio["reach_in"],
            "leg_reach_in": bio["leg_reach_in"], "stance": bio["stance"],
            "country": bio["country"], "division": bio["division"],
            "status": bio["status"], "image_url": bio["image_url"],
            "style_tag": bio["style_tag"], "pre_ufc_record": bio["pre_ufc_record"],
        },
        "record": record_of(bio),
        "ratings": ratings,
        "rankings": rankings,
        "career_stats": career_stats,
        "elo_timeline": timeline,
        "fight_history": fight_history,
        "last_fight": last_fight,
    }
