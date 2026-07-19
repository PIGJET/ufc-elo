"""GET /api/rankings -- the Rankings landing-page payload (PLAN section 8).

Divisions from the latest ``rankings_snapshot`` (rank 0 = champion, 1..N =
contenders in order), each fighter carrying their ``ratings_current`` rating for
that division, plus the two pound-for-pound lists.  Ratings are pulled per
division so a champion who also holds a rating in another pool shows the right
number here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import db
from api.serialize import best_rating_row, rating_in_division, rating_pair
from elo.divisions import STANDARD_DIVISIONS

router = APIRouter()

#: Elo-board tuning: minimum rated fights to appear, recency window, board size.
_ELO_MIN_FIGHTS = 5
_ELO_RECENCY = "-3 years"
_ELO_TOP_N = 15

#: Preferred display order for the standard divisions (men light->heavy, then
#: women).  Any snapshot division not listed falls to the end, alphabetically.
_DIVISION_ORDER = [
    "Flyweight", "Bantamweight", "Featherweight", "Lightweight", "Welterweight",
    "Middleweight", "Light Heavyweight", "Heavyweight",
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight",
]
_MENS_P4P = "Men's Pound-for-Pound"
_WOMENS_P4P = "Women's Pound-for-Pound"


def _entry(conn, row, *, pfp: bool) -> dict[str, Any]:
    """One ranked fighter.  Rating is division-specific, or (for P4P) the
    fighter's primary rating since a P4P board has no single division."""
    fid = row["fighter_id"]
    if fid is None:
        # Snapshot row whose scraped name never resolved to a fighter id.
        return {
            "fighter_id": None, "name": row["fighter_name"], "nickname": None,
            "image_url": None, "rank": row["rank"], "movement": row["movement"],
            "rating": {"mu": None, "rd": None},
        }
    f = conn.execute(
        "SELECT name, nickname, image_url FROM fighters WHERE id = ?", (fid,)
    ).fetchone()
    rating_row = (best_rating_row(conn, fid) if pfp
                  else rating_in_division(conn, fid, row["division"]))
    entry = {
        "fighter_id": fid,
        "name": f["name"] if f else row["fighter_name"],
        "nickname": f["nickname"] if f else None,
        "image_url": f["image_url"] if f else None,
        "rank": row["rank"],
        "movement": row["movement"],
        "rating": rating_pair(rating_row),
    }
    if pfp:
        # The pound-for-pound board ranks by the cross-division-adjusted rating;
        # expose it so the P4P columns can show the P4P number, not the raw
        # division mu.  Falls back to null when the fighter has no rating row.
        entry["pfp_mu"] = (round(rating_row["pfp_mu"], 1)
                           if rating_row is not None and rating_row["pfp_mu"] is not None
                           else None)
    return entry


@router.get("/rankings")
def get_rankings(conn=Depends(db)) -> dict[str, Any]:
    snapshot_date = conn.execute(
        "SELECT MAX(snapshot_date) FROM rankings_snapshot"
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT division, rank, fighter_id, fighter_name, movement "
        "FROM rankings_snapshot WHERE snapshot_date = ? ORDER BY division, rank",
        (snapshot_date,),
    ).fetchall()

    by_division: dict[str, list] = {}
    for r in rows:
        by_division.setdefault(r["division"], []).append(r)

    def order_key(name: str) -> tuple[int, str]:
        return (_DIVISION_ORDER.index(name) if name in _DIVISION_ORDER
                else len(_DIVISION_ORDER), name)

    divisions = []
    for div in sorted(
        (d for d in by_division if d not in (_MENS_P4P, _WOMENS_P4P)), key=order_key
    ):
        entries = by_division[div]
        champion = next((r for r in entries if r["rank"] == 0), None)
        contenders = [r for r in entries if r["rank"] != 0]
        divisions.append({
            "division": div,
            "champion": _entry(conn, champion, pfp=False) if champion else None,
            "contenders": [_entry(conn, r, pfp=False) for r in contenders],
        })

    def p4p(name: str) -> list[dict[str, Any]]:
        return [_entry(conn, r, pfp=True) for r in by_division.get(name, [])]

    return {
        "snapshot_date": snapshot_date,
        "divisions": divisions,
        "pound_for_pound": {"mens": p4p(_MENS_P4P), "womens": p4p(_WOMENS_P4P)},
    }


# ---------------------------------------------------------------------------
# GET /api/rankings/elo -- the same payload shape, but every division and P4P
# board is re-ordered purely by our rating system (no UFC snapshot involved).
# ---------------------------------------------------------------------------
def _elo_entry(conn, row, rank: int, *, pfp: bool) -> dict[str, Any]:
    """One Elo-ranked fighter, matching the /rankings entry shape.

    ``row`` is a ``ratings_current`` row joined onto ``fighters`` (so it carries
    name/nickname/image_url plus the rating fields).  ``movement`` is 0 -- an
    Elo board has no prior snapshot to diff against.
    """
    entry = {
        "fighter_id": row["fighter_id"],
        "name": row["name"],
        "nickname": row["nickname"],
        "image_url": row["image_url"],
        "rank": rank,
        "movement": 0,
        "rating": rating_pair(row),
    }
    if pfp:
        entry["pfp_mu"] = (round(row["pfp_mu"], 1)
                           if row["pfp_mu"] is not None else None)
    return entry


#: Shared eligibility filter: enough rated fights, recently active, not retired.
_ELO_FILTER = (
    "rc.n_fights >= ? "
    "AND rc.last_fight IS NOT NULL AND rc.last_fight >= date('now', ?) "
    "AND (f.status IS NULL OR f.status != 'retired')"
)


@router.get("/rankings/elo")
def get_rankings_elo(conn=Depends(db)) -> dict[str, Any]:
    """Each division re-ranked by ``ratings_current.mu`` descending (top 15),
    filtered to active fighters with >= 5 rated fights whose last bout is within
    ~3 years.  P4P boards are ranked by the cross-division ``pfp_mu``."""
    divisions = []
    for div in STANDARD_DIVISIONS:
        rows = conn.execute(
            "SELECT rc.fighter_id, rc.mu, rc.rd, rc.pfp_mu, "
            "       f.name, f.nickname, f.image_url "
            "FROM ratings_current rc JOIN fighters f ON f.id = rc.fighter_id "
            f"WHERE rc.division = ? AND {_ELO_FILTER} "
            "ORDER BY rc.mu DESC LIMIT ?",
            (div, _ELO_MIN_FIGHTS, _ELO_RECENCY, _ELO_TOP_N),
        ).fetchall()
        entries = [_elo_entry(conn, r, i + 1, pfp=False)
                   for i, r in enumerate(rows)]
        divisions.append({
            "division": div,
            "champion": entries[0] if entries else None,
            "contenders": entries[1:],
        })

    # Pound-for-pound: one entry per fighter (their primary/most-rated division
    # pool), ranked by pfp_mu, split by whether that home pool is a women's one.
    p4p_rows = conn.execute(
        "SELECT rc.fighter_id, rc.mu, rc.rd, rc.pfp_mu, rc.division, "
        "       f.name, f.nickname, f.image_url "
        "FROM ratings_current rc JOIN fighters f ON f.id = rc.fighter_id "
        f"WHERE {_ELO_FILTER} AND rc.pfp_mu IS NOT NULL "
        "AND rc.n_fights = (SELECT MAX(r2.n_fights) FROM ratings_current r2 "
        "                   WHERE r2.fighter_id = rc.fighter_id) "
        "GROUP BY rc.fighter_id "
        "ORDER BY rc.pfp_mu DESC",
        (_ELO_MIN_FIGHTS, _ELO_RECENCY),
    ).fetchall()

    mens, womens = [], []
    for r in p4p_rows:
        bucket = womens if str(r["division"]).startswith("Women's") else mens
        if len(bucket) < _ELO_TOP_N:
            bucket.append(_elo_entry(conn, r, len(bucket) + 1, pfp=True))

    return {
        "snapshot_date": None,
        "divisions": divisions,
        "pound_for_pound": {"mens": mens, "womens": womens},
    }
