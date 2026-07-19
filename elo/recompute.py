"""Full recompute CLI: rebuild elo_history and ratings_current from history.

Wipes both rating tables and rewrites them from a fresh chronological replay
(:mod:`elo.engine`), reading the fitted offsets from ``division_offsets.json``.
The operation is idempotent: running it twice produces identical tables.

``ratings_current`` is rebuilt in full -- one row per (fighter, division) pool,
with RD grown to ``as_of`` (default: today) for fighters who have been inactive
in that pool.  ``elo_history`` gets one row per fighter per rated fight.

Run:  python -m elo.recompute            # as_of = today
      python -m elo.recompute --as-of 2026-07-17
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.db import get_conn  # noqa: E402
from elo import config  # noqa: E402
from elo.engine import RatingEngine, load_fights  # noqa: E402

OFFSETS_PATH = Path(__file__).resolve().parent / config.OFFSETS_FILENAME


def load_offsets(path: Path = OFFSETS_PATH) -> dict[str, float]:
    """Read the fitted D_w table; empty (all-zero) if the file is absent."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data.get("offsets", {}).items()}


def load_pre_ufc(conn: sqlite3.Connection) -> dict[int, str]:
    """fighter_id -> pre_ufc_record (empty in the current DB; hook stays warm)."""
    rows = conn.execute(
        "SELECT id, pre_ufc_record FROM fighters WHERE pre_ufc_record IS NOT NULL "
        "AND pre_ufc_record != ''"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def recompute(db_path: Path | str | None = None,
              as_of: date | None = None) -> dict:
    """Run the full recompute; return summary stats for logging/tests."""
    as_of = as_of or date.today()
    offsets = load_offsets()

    conn = get_conn(db_path) if db_path else get_conn()
    conn.execute(f"PRAGMA busy_timeout = {config.SQLITE_BUSY_TIMEOUT_MS}")
    try:
        fights = load_fights(conn)
        pre_ufc = load_pre_ufc(conn)

        eng = RatingEngine(offsets=offsets, pre_ufc=pre_ufc)
        eng.run(fights)

        history_rows = [
            (h.fighter_id, h.fight_id, h.date, h.division,
             h.mu_pre, h.rd_pre, h.mu_post, h.rd_post, h.sigma_post,
             h.pfp_mu_post, h.update_note)
            for h in eng.history
        ]
        current_rows = [
            (r["fighter_id"], r["division"], r["mu"], r["rd"], r["sigma"],
             r["pfp_mu"], r["n_fights"], r["last_fight"], r["as_of"])
            for r in eng.current_ratings(as_of)
        ]

        # Single atomic wipe-and-rewrite (fast: << 1 min for 8.7k fights).
        conn.execute("BEGIN")
        conn.execute("DELETE FROM elo_history")
        conn.execute("DELETE FROM ratings_current")
        conn.executemany(
            "INSERT INTO elo_history (fighter_id, fight_id, date, division, "
            "mu_pre, rd_pre, mu_post, rd_post, sigma_post, pfp_mu_post, "
            "update_note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            history_rows,
        )
        conn.executemany(
            "INSERT INTO ratings_current (fighter_id, division, mu, rd, sigma, "
            "pfp_mu, n_fights, last_fight, as_of) VALUES (?,?,?,?,?,?,?,?,?)",
            current_rows,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "fights": len(fights),
        "history_rows": len(history_rows),
        "current_rows": len(current_rows),
        "offsets_loaded": len(offsets),
        "as_of": as_of.isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Recompute UFC Elo ratings.")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None,
                    help="RD-growth reference date (ISO); default today.")
    args = ap.parse_args()

    t = time.perf_counter()
    stats = recompute(as_of=args.as_of)
    dt = time.perf_counter() - t
    print(f"Recompute complete in {dt:.2f}s")
    print(f"  fights replayed : {stats['fights']}")
    print(f"  elo_history rows: {stats['history_rows']}")
    print(f"  ratings_current : {stats['current_rows']}")
    print(f"  offsets loaded  : {stats['offsets_loaded']}")
    print(f"  as_of           : {stats['as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
