"""GET /api/events/upcoming -- confirmed upcoming cards (PLAN section 8).

Each upcoming event (soonest first) with its fights in ``card_position`` order.
Every fight carries both fighters, the per-fighter comparison stats for the
module tabs, the latest odds (``null`` until the odds table is populated), the
prediction, and each fighter's rating gain/loss preview.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import db
from api.serialize import build_comparison, stakes_for_fight

router = APIRouter()

#: Factors shown on an events card (the matchup page shows the full list).
_EVENTS_FACTOR_LIMIT = 6


def _latest_odds(conn, fight_id: int) -> dict[str, Any] | None:
    """Most recently fetched odds row for a fight, or None (table may be empty)."""
    row = conn.execute(
        "SELECT source, red_moneyline, blue_moneyline, fetched_at FROM odds "
        "WHERE fight_id = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (fight_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "source": row["source"],
        "red_moneyline": row["red_moneyline"],
        "blue_moneyline": row["blue_moneyline"],
        "fetched_at": row["fetched_at"],
    }


@router.get("/events/upcoming")
def upcoming_events(conn=Depends(db)) -> dict[str, Any]:
    events = conn.execute(
        "SELECT id, name, date, location, venue FROM events "
        "WHERE status = 'upcoming' ORDER BY date ASC, id ASC"
    ).fetchall()

    out_events = []
    for e in events:
        fights = conn.execute(
            "SELECT id, fighter_red_id, fighter_blue_id, weight_class, "
            "card_position, is_title, is_interim_title, is_main_event "
            "FROM fights WHERE event_id = ? "
            "ORDER BY (card_position IS NULL) ASC, card_position ASC, id ASC",
            (e["id"],),
        ).fetchall()

        card = []
        for f in fights:
            comparison = build_comparison(
                conn, f["fighter_red_id"], f["fighter_blue_id"],
                stakes=stakes_for_fight(f), factor_limit=_EVENTS_FACTOR_LIMIT,
            )
            card.append({
                "fight_id": f["id"],
                "card_position": f["card_position"],
                "weight_class": f["weight_class"],
                "is_title": bool(f["is_title"]),
                "is_interim_title": bool(f["is_interim_title"]),
                "is_main_event": bool(f["is_main_event"]),
                "odds": _latest_odds(conn, f["id"]),
                **comparison,
            })

        out_events.append({
            "id": e["id"],
            "name": e["name"],
            "date": e["date"],
            "location": e["location"],
            "venue": e["venue"],
            "fights": card,
        })

    return {"events": out_events}
