"""GET /api/matchup?a={id}&b={id} -- head-to-head for any two fighters.

Same comparison shape as an events-card fight (stat tabs, prediction, rating
win/loss previews) with the **full** factor list, plus the cross-division /
speculative flags and the rating gap surfaced at the top level for the matchup
page's cross-division note.  Fighter ``a`` is the red corner (``prob_red`` =
P(a wins)); the projection is order-invariant, so swapping a and b just mirrors
the numbers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import db
from api.serialize import build_comparison

router = APIRouter()


def _require_fighter(conn, fighter_id: int) -> None:
    row = conn.execute("SELECT 1 FROM fighters WHERE id = ?", (fighter_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"fighter {fighter_id} not found")


@router.get("/matchup")
def matchup(
    a: int = Query(..., description="fighter id (red corner / fighter A)"),
    b: int = Query(..., description="fighter id (blue corner / fighter B)"),
    conn=Depends(db),
) -> dict[str, Any]:
    if a == b:
        raise HTTPException(status_code=422, detail="a and b must be different fighters")
    _require_fighter(conn, a)
    _require_fighter(conn, b)

    comparison = build_comparison(conn, a, b, factor_limit=None)
    prediction = comparison["prediction"]
    return {
        **comparison,
        "cross_division": prediction["cross_division"] if prediction else None,
        "speculative": prediction["speculative"] if prediction else None,
    }
