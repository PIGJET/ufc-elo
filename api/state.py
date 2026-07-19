"""Shared, process-wide state for the API layer.

Everything here is built **once** (at startup, or on demand via ``/api/refresh``)
and then read by the request handlers.  The database is read-only from the API's
perspective, so caching is safe: the caches only change when the underlying
``ufc.db`` is rebuilt by an offline recompute, at which point a restart (or a
call to ``/api/refresh``) reloads them.

Three caches live here:

* ``model`` / ``offsets`` -- the persisted prediction coefficients
  (:func:`elo.predict.load_model`) and fitted cross-division offsets
  (:func:`elo.recompute.load_offsets`), loaded once so per-fight prediction and
  rating previews never touch disk.
* ``career`` -- per-fighter career stat aggregates (win-by-method breakdown,
  first-round finishes, sig-strike + takedown + control-time totals) computed in
  two passes over the completed fights.  Feeds the profile stat row and the
  events / matchup comparison tabs without a per-request aggregation query.
* ``search_index`` -- accent/case-folded (name, nickname) tuples for the
  typeahead endpoint, so substring search is accent-insensitive.
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path
from typing import Any

# Make the sibling ``data`` / ``elo`` packages importable when uvicorn is
# launched from the project root (matches how elo.predict bootstraps itself).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.db import get_conn  # noqa: E402
from elo.predict import load_model  # noqa: E402
from elo.recompute import load_offsets  # noqa: E402

#: UFC rounds are five minutes; used to reconstruct fight duration from the
#: ``fights.round`` + ``fights.time_seconds`` (elapsed in the final round) pair.
ROUND_SECONDS: int = 300

#: Methods that count as a decision win in the win-by breakdown.
_DECISION_METHODS = frozenset({"U-DEC", "S-DEC", "M-DEC"})
_FINISH_METHODS = frozenset({"KO/TKO", "SUB"})


def _fold(text: str | None) -> str:
    """Case- and accent-insensitive fold for search (José -> jose)."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return stripped.casefold()


class AppState:
    """Container for the process-wide caches (see module docstring)."""

    def __init__(self) -> None:
        self.model: dict[str, Any] = {}
        self.offsets: dict[str, float] = {}
        #: fighter_id -> career aggregate dict (see :meth:`_build_career`).
        self.career: dict[int, dict[str, Any]] = {}
        #: list of (fighter_id, folded_name, folded_nick) for typeahead.
        self.search_index: list[tuple[int, str, str]] = []
        self.loaded: bool = False

    # -- lifecycle ----------------------------------------------------------
    def load(self) -> None:
        """(Re)build every cache.  Idempotent; called at startup and /refresh."""
        self.model = load_model()
        self.offsets = load_offsets()
        conn = get_conn()
        try:
            self._build_career(conn)
            self._build_search_index(conn)
        finally:
            conn.close()
        self.loaded = True

    # -- career aggregates --------------------------------------------------
    def _blank_career(self) -> dict[str, Any]:
        return {
            "wins_by_ko": 0, "wins_by_sub": 0, "wins_by_decision": 0,
            "wins_by_dq": 0, "first_round_finishes": 0,
            "sig_strikes_landed": 0, "sig_strikes_attempted": 0,
            "takedowns_landed": 0, "takedowns_attempted": 0,
            "knockdowns": 0, "sub_attempts": 0,
            "control_time_seconds": 0, "total_fight_seconds": 0,
            "fights_with_stats": 0,
        }

    def _build_career(self, conn) -> None:
        """Two passes over completed fights -> per-fighter career aggregates."""
        career: dict[int, dict[str, Any]] = {}

        def bucket(fid: int) -> dict[str, Any]:
            c = career.get(fid)
            if c is None:
                c = self._blank_career()
                career[fid] = c
            return c

        # Pass 1: win-by-method + first-round finishes, from decisive results.
        for r in conn.execute(
            "SELECT winner_id, method, round FROM fights "
            "WHERE result_current IN ('win', 'dq') AND winner_id IS NOT NULL"
        ):
            c = bucket(r["winner_id"])
            method = r["method"]
            if method == "KO/TKO":
                c["wins_by_ko"] += 1
            elif method == "SUB":
                c["wins_by_sub"] += 1
            elif method in _DECISION_METHODS:
                c["wins_by_decision"] += 1
            elif method == "DQ":
                c["wins_by_dq"] += 1
            if method in _FINISH_METHODS and r["round"] == 1:
                c["first_round_finishes"] += 1

        # Pass 2: per-fight stat totals + fight duration (for per-minute rates
        # and control-time share).  fight_stats has one row per fighter per
        # fight; duration comes from the parent fight row.
        for r in conn.execute(
            """
            SELECT s.fighter_id, s.sig_strikes_landed, s.sig_strikes_attempted,
                   s.takedowns_landed, s.takedowns_attempted, s.knockdowns,
                   s.sub_attempts, s.control_time_seconds,
                   f.round AS f_round, f.time_seconds AS f_time
            FROM fight_stats s
            JOIN fights f ON s.fight_id = f.id
            WHERE f.result_current != 'upcoming'
            """
        ):
            c = bucket(r["fighter_id"])
            c["sig_strikes_landed"] += r["sig_strikes_landed"] or 0
            c["sig_strikes_attempted"] += r["sig_strikes_attempted"] or 0
            c["takedowns_landed"] += r["takedowns_landed"] or 0
            c["takedowns_attempted"] += r["takedowns_attempted"] or 0
            c["knockdowns"] += r["knockdowns"] or 0
            c["sub_attempts"] += r["sub_attempts"] or 0
            c["control_time_seconds"] += r["control_time_seconds"] or 0
            if r["f_round"] is not None and r["f_time"] is not None:
                c["total_fight_seconds"] += (r["f_round"] - 1) * ROUND_SECONDS + r["f_time"]
            c["fights_with_stats"] += 1

        self.career = career

    def career_for(self, fighter_id: int) -> dict[str, Any]:
        """Career aggregate for a fighter, blank (all-zero) if none on record."""
        return self.career.get(fighter_id) or self._blank_career()

    # -- search index -------------------------------------------------------
    def _build_search_index(self, conn) -> None:
        rows = conn.execute("SELECT id, name, nickname FROM fighters").fetchall()
        self.search_index = [
            (r["id"], _fold(r["name"]), _fold(r["nickname"])) for r in rows
        ]

    def search(self, query: str, limit: int = 10) -> list[int]:
        """Return up to ``limit`` fighter ids matching ``query`` (substring).

        Case- and accent-insensitive.  Ranking (best first): a match at the very
        start of the name/nickname, then a match at the start of any *word*
        (so a surname query like "makhachev" ranks "Islam Makhachev" highly),
        then any interior substring -- alphabetical by folded name to break ties.
        """
        q = _fold(query)
        if not q:
            return []
        hits: list[tuple[int, int, str]] = []  # (rank_bucket, name, id)
        for fid, name, nick in self.search_index:
            if q not in name and q not in nick:
                continue
            if name.startswith(q) or nick.startswith(q):
                bucket = 0
            elif any(w.startswith(q) for w in name.split()) or \
                    any(w.startswith(q) for w in nick.split()):
                bucket = 1
            else:
                bucket = 2
            hits.append((bucket, name, fid))
        hits.sort(key=lambda t: (t[0], t[1], t[2]))
        return [fid for _, _, fid in hits[:limit]]


#: The single process-wide state object shared by all routers.
state = AppState()
