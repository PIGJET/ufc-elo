"""Backfill UFC 1 (Nov 12, 1993) into data/ufc.db.

The historical ufcstats.com scrape used by :mod:`ingest_historical` starts at
UFC 2 (Mar 11, 1994); the very first event, UFC 1, is missing.  This
self-contained, idempotent script inserts UFC 1 with its full eight-bout card
so the rating history starts where the sport actually did.

DATA SOURCE
-----------
All results hardcoded from Wikipedia's UFC 1 event page (bout order, winners,
methods, rounds, exact times, venue), cross-checked against the event's
"Results" table:

  https://en.wikipedia.org/wiki/UFC_1

Provenance for every inserted field is logged to the ``provenance`` table under
source ``manual-backfill:wikipedia``.

THE CARD (McNichols Sports Arena, Denver, Colorado -- an eight-man open-weight
one-night tournament plus one alternate bout, every fight a first-round finish):

  Quarterfinals
    Gerard Gordeau def. Teila Tuli      TKO (head kick)          R1 0:26
    Kevin Rosier   def. Zane Frazier     TKO (corner stoppage)    R1 4:20
    Royce Gracie   def. Art Jimmerson    Submission               R1 2:18
    Ken Shamrock   def. Patrick Smith    Submission (heel hook)   R1 1:49
  Semifinals
    Gerard Gordeau def. Kevin Rosier     TKO (punches)            R1 0:59
    Royce Gracie   def. Ken Shamrock     Submission (RNC)         R1 0:57
  Final
    Royce Gracie   def. Gerard Gordeau   Submission (RNC)         R1 1:44
  Alternate bout
    Jason DeLucia  def. Trent Jenkins    Submission (RNC)         R1 0:52

IDEMPOTENCY
-----------
Re-running never duplicates: the UFC 1 event is matched by name (inserted once),
and its fights are wiped and re-inserted on every run (with any dependent
``elo_history``/``fight_stats`` rows cleared first to respect foreign keys).
Fighters are matched by name -- all UFC 1 competitors also appear on later
cards, so no new fighter rows are normally created; a name-only row (no
ufcstats_id) is created only for a competitor who somehow never fought again.
No ``fight_stats`` are inserted (none exist for 1993).

Run:  python data/ingest/backfill_ufc1.py
      python -m elo.recompute      # then rebuild ratings -> history starts 1993-11-12
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- make `data` package importable so we can reuse the shared DB helper ------
DATA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DATA_DIR.parent))
from data.db import init_db, log_provenance  # noqa: E402

SOURCE = "manual-backfill:wikipedia"

EVENT = {
    "name": "UFC 1: The Beginning",
    "date": "1993-11-12",
    "location": "Denver, Colorado, USA",
    "venue": "McNichols Sports Arena",
}

WEIGHT_CLASS = "Open Weight"
SCHEDULED_ROUNDS = 1

# (card_position, is_main_event, winner, loser, method, method_detail, round, time_seconds)
# card_position 1 = the tournament final (main event).  The rating engine replays
# an event's fights in descending card_position order, so the higher numbers
# (quarterfinals, then semifinals) are rated before the final -- matching the
# real one-night bracket.
BOUTS = [
    (1, 1, "Royce Gracie",   "Gerard Gordeau", "SUB",    "Rear-Naked Choke", 1, 104),  # final
    (2, 0, "Royce Gracie",   "Ken Shamrock",   "SUB",    "Rear-Naked Choke", 1, 57),   # semifinal
    (3, 0, "Gerard Gordeau", "Kevin Rosier",   "KO/TKO", "Punches",          1, 59),   # semifinal
    (4, 0, "Ken Shamrock",   "Patrick Smith",  "SUB",    "Heel Hook",        1, 109),  # quarterfinal
    (5, 0, "Royce Gracie",   "Art Jimmerson",  "SUB",    "Submission",       1, 138),  # quarterfinal
    (6, 0, "Kevin Rosier",   "Zane Frazier",   "KO/TKO", "Corner Stoppage",  1, 260),  # quarterfinal
    (7, 0, "Gerard Gordeau", "Teila Tuli",     "KO/TKO", "Head Kick",        1, 26),   # quarterfinal
    (8, 0, "Jason DeLucia",  "Trent Jenkins",  "SUB",    "Rear-Naked Choke", 1, 52),   # alternate
]


def main() -> None:
    conn = init_db()
    conn.execute("PRAGMA busy_timeout = 30000")
    cur = conn.cursor()

    # ---------------- fighter resolution (match by name) ------------------
    nameonly_created = []

    def resolve_fighter(name: str) -> int:
        name = name.strip()
        # Prefer a real (ufcstats-backed) fighter row; fall back to any match.
        row = cur.execute(
            "SELECT id FROM fighters WHERE name = ? "
            "ORDER BY (ufcstats_id IS NULL) ASC, id ASC LIMIT 1",
            (name,),
        ).fetchone()
        if row is not None:
            return row["id"]
        cur.execute("INSERT INTO fighters (name) VALUES (?)", (name,))
        fid = cur.lastrowid
        nameonly_created.append(name)
        log_provenance(conn, "fighters", fid, "name", SOURCE, name)
        return fid

    # ---------------- event (match by name, insert once) ------------------
    existing = cur.execute(
        "SELECT id FROM events WHERE name = ?", (EVENT["name"],)
    ).fetchone()
    if existing:
        eid = existing["id"]
        cur.execute(
            "UPDATE events SET date=?, location=?, venue=?, status='completed', "
            "updated_at=datetime('now') WHERE id=?",
            (EVENT["date"], EVENT["location"], EVENT["venue"], eid),
        )
    else:
        cur.execute(
            "INSERT INTO events (name, date, location, venue, status) "
            "VALUES (?,?,?,?,'completed')",
            (EVENT["name"], EVENT["date"], EVENT["location"], EVENT["venue"]),
        )
        eid = cur.lastrowid
        for field, value in (("date", EVENT["date"]), ("location", EVENT["location"]),
                             ("venue", EVENT["venue"])):
            log_provenance(conn, "events", eid, field, SOURCE, value)

    # ---------------- fights (idempotent: wipe this event's, re-insert) ---
    old_fight_ids = [r["id"] for r in cur.execute(
        "SELECT id FROM fights WHERE event_id = ?", (eid,)
    )]
    if old_fight_ids:
        marks = ",".join("?" * len(old_fight_ids))
        # Clear dependents first so foreign_keys=ON is satisfied.
        cur.execute(f"DELETE FROM elo_history WHERE fight_id IN ({marks})", old_fight_ids)
        cur.execute(f"DELETE FROM fight_stats WHERE fight_id IN ({marks})", old_fight_ids)
        cur.execute(f"DELETE FROM fights WHERE id IN ({marks})", old_fight_ids)

    involved: set[int] = set()
    for card_pos, is_main, winner, loser, method, detail, rnd, tsec in BOUTS:
        red_id = resolve_fighter(winner)   # red = winner
        blue_id = resolve_fighter(loser)   # blue = loser
        involved.update((red_id, blue_id))
        cur.execute(
            "INSERT INTO fights (event_id, fighter_red_id, fighter_blue_id, winner_id, "
            "result_original, result_current, method, method_detail, round, time_seconds, "
            "weight_class, scheduled_rounds, is_title, is_interim_title, is_main_event, "
            "card_position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)",
            (eid, red_id, blue_id, red_id, "win", "win", method, detail, rnd, tsec,
             WEIGHT_CLASS, SCHEDULED_ROUNDS, is_main, card_pos),
        )
        fdb = cur.lastrowid
        for field, value in (("method", method), ("round", rnd),
                             ("time_seconds", tsec), ("weight_class", WEIGHT_CLASS)):
            log_provenance(conn, "fights", fdb, field, SOURCE, value)

    conn.commit()

    # ---------------- refresh W/L/D for the involved fighters -------------
    # Mirror ingest_historical's global derivation, scoped to UFC 1 competitors.
    for fid in involved:
        wins = cur.execute(
            "SELECT COUNT(*) FROM fights WHERE winner_id=? AND result_current IN ('win','dq')",
            (fid,)).fetchone()[0]
        losses = cur.execute(
            "SELECT COUNT(*) FROM fights WHERE result_current IN ('win','dq') "
            "AND winner_id IS NOT NULL AND (fighter_red_id=? OR fighter_blue_id=?) "
            "AND winner_id<>?", (fid, fid, fid)).fetchone()[0]
        draws = cur.execute(
            "SELECT COUNT(*) FROM fights WHERE result_current='draw' "
            "AND (fighter_red_id=? OR fighter_blue_id=?)", (fid, fid)).fetchone()[0]
        cur.execute("UPDATE fighters SET wins=?, losses=?, draws=?, "
                    "updated_at=datetime('now') WHERE id=?", (wins, losses, draws, fid))
    conn.commit()

    # -------- report -----------------------------------------------------
    n_fights = cur.execute("SELECT COUNT(*) FROM fights WHERE event_id=?", (eid,)).fetchone()[0]
    print("=== UFC 1 backfill complete ===")
    print(f"  event id        : {eid} ({EVENT['name']}, {EVENT['date']})")
    print(f"  fights inserted : {n_fights}")
    print(f"  fighters touched: {len(involved)}")
    print(f"  name-only rows created: {nameonly_created or 'none'}")
    conn.close()


if __name__ == "__main__":
    main()
