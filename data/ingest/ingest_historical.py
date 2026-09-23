"""Historical base ingestion for the UFC Elo project.

Loads a comprehensive public ufcstats.com scrape into data/ufc.db.

DATA SOURCE
-----------
Greco1899/scrape_ufc_stats  (GitHub, MIT).  A daily-refreshed complete export
of ufcstats.com covering UFC 1 (1993) through the latest cards.  Raw CSVs pulled
from the `main` branch:

  https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_event_details.csv
  https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fight_details.csv
  https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fight_results.csv
  https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fight_stats.csv
  https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_details.csv
  https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_tott.csv

Downloaded copies live in data/raw/.  Because every row carries the ufcstats.com
URL, we extract the ufcstats id for events, fighters and fights -- these are the
join keys the live ufcstats scraper reconciles against.

The load is idempotent: events/fighters/fights/fight_stats upsert on their
natural keys (ufcstats id, or fight+fighter for stats), so re-running never
duplicates rows.  Provenance is logged once, on first insert of a row, to keep
the append-only provenance table from ballooning on re-runs.

Run:  python data/ingest/ingest_historical.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

# --- make `data` package importable so we can reuse the shared DB helper ------
DATA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DATA_DIR.parent))
from data.db import init_db, log_provenance  # noqa: E402

RAW_DIR = DATA_DIR / "raw"
SOURCE = "ufcstats-scrape:Greco1899"
RAW_BASE_URL = (
    "https://raw.githubusercontent.com/Greco1899/"
    "scrape_ufc_stats/main"
)
RAW_FILES = (
    "ufc_event_details.csv",
    "ufc_fight_details.csv",
    "ufc_fight_results.csv",
    "ufc_fight_stats.csv",
    "ufc_fighter_details.csv",
    "ufc_fighter_tott.csv",
)

DIVISIONS = [
    "Light Heavyweight", "Heavyweight", "Welterweight", "Middleweight",
    "Lightweight", "Featherweight", "Bantamweight", "Flyweight",
    "Strawweight", "Catch Weight", "Open Weight",
]

METHOD_MAP = {
    "KO/TKO": "KO/TKO",
    "TKO - Doctor's Stoppage": "KO/TKO",
    "Submission": "SUB",
    "Decision - Unanimous": "U-DEC",
    "Decision - Split": "S-DEC",
    "Decision - Majority": "M-DEC",
    "DQ": "DQ",
    "Overturned": "NC",
    "Could Not Continue": "NC",
    "Other": "NC",
}

# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------

def ufcid(url: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    return url.rstrip("/").split("/")[-1]


def parse_height(s) -> float | None:
    if not isinstance(s, str):
        return None
    m = re.match(r"(\d+)'\s*(\d+)", s)
    if not m:
        return None
    return int(m.group(1)) * 12 + int(m.group(2))


def parse_reach(s) -> float | None:
    if not isinstance(s, str):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def parse_dob(s) -> str | None:
    if not isinstance(s, str) or s.strip() in ("", "--"):
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_event_date(s) -> str | None:
    if not isinstance(s, str):
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_time_seconds(s) -> int | None:
    if not isinstance(s, str) or ":" not in s:
        return None
    try:
        mm, ss = s.split(":")
        return int(mm) * 60 + int(ss)
    except (ValueError, TypeError):
        return None


def parse_x_of_y(s):
    """'23 of 38' -> (23, 38);  '---' / NaN -> (None, None)."""
    if not isinstance(s, str):
        return None, None
    m = re.match(r"\s*(\d+)\s+of\s+(\d+)", s)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def parse_int(s) -> int | None:
    try:
        if s is None or (isinstance(s, float) and pd.isna(s)):
            return None
        return int(float(s))
    except (ValueError, TypeError):
        return None


def parse_scheduled_rounds(fmt) -> int | None:
    if not isinstance(fmt, str):
        return None
    m = re.match(r"\s*(\d+)\s*Rnd", fmt)
    return int(m.group(1)) if m else None


def classify_weightclass(wc: str):
    """Return (division, is_title, is_interim, weight_class_stored)."""
    if not isinstance(wc, str):
        return None, 0, 0, None
    is_title = 1 if ("Title" in wc or "Championship" in wc) else 0
    is_interim = 1 if "Interim" in wc else 0
    womens = "Women's " if "Women" in wc else ""
    if "Catch" in wc:
        return "Catchweight", is_title, is_interim, "Catchweight"
    for d in DIVISIONS:
        if d in wc:
            div = d if d not in ("Catch Weight", "Open Weight") else d
            return womens + div, is_title, is_interim, womens + div
    # no division keyword (e.g. "UFC Superfight Championship Bout")
    stripped = wc.replace(" Bout", "").strip()
    return stripped or None, is_title, is_interim, stripped or None


JUDGE_RE = re.compile(r"(.+?)\s+(\d+)\s*-\s*(\d+)")

def parse_judge_scores(details):
    """'Eric Colon 45 - 50.David Lethaby 45 - 50.' -> JSON list, else None."""
    if not isinstance(details, str):
        return None
    scores = []
    for chunk in details.split("."):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = JUDGE_RE.match(chunk)
        if m:
            scores.append({"judge": m.group(1).strip(),
                           "red": int(m.group(2)), "blue": int(m.group(3))})
    return json.dumps(scores) if scores else None


# ---------------------------------------------------------------------------
# main ingestion
# ---------------------------------------------------------------------------

def refresh_raw_files() -> None:
    """Download a consistent-enough current snapshot of the public CSV export.

    Files are written through a temporary sibling so an interrupted download
    cannot leave a truncated CSV behind. The raw directory is intentionally
    gitignored; the reconciled SQLite snapshot is the release artifact.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "PIGJET/ufc-elo data refresh"

    for filename in RAW_FILES:
        response = session.get(f"{RAW_BASE_URL}/{filename}", timeout=60)
        response.raise_for_status()
        target = RAW_DIR / filename
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(response.content)
        temporary.replace(target)
        print(f"Downloaded {filename} ({len(response.content):,} bytes)")


def main() -> None:
    refresh_raw_files()
    conn = init_db()
    conn.execute("PRAGMA busy_timeout = 30000")
    cur = conn.cursor()

    ev = pd.read_csv(RAW_DIR / "ufc_event_details.csv")
    det = pd.read_csv(RAW_DIR / "ufc_fighter_details.csv")
    tott = pd.read_csv(RAW_DIR / "ufc_fighter_tott.csv")
    res = pd.read_csv(RAW_DIR / "ufc_fight_results.csv")
    stats = pd.read_csv(RAW_DIR / "ufc_fight_stats.csv")

    # source CSVs carry stray trailing whitespace on EVENT/BOUT strings; the
    # EVENT text is the join key across files, so normalise it everywhere.
    for df in (ev, res, stats):
        if "EVENT" in df.columns:
            df["EVENT"] = df["EVENT"].astype("string").str.strip()
    for df in (res, stats):
        if "BOUT" in df.columns:
            df["BOUT"] = df["BOUT"].astype("string").str.strip()

    # ---------------- fighters -------------------------------------------
    det["name"] = (det["FIRST"].fillna("") + " " + det["LAST"].fillna("")).str.strip()
    det["fid"] = det["URL"].map(ufcid)
    tott["fid"] = tott["URL"].map(ufcid)

    # merge physicals (tott) with nickname/name (details) on ufcstats id
    nick = det.set_index("fid")["NICKNAME"].to_dict()
    detname = det.set_index("fid")["name"].to_dict()

    fighter_rows = {}  # fid -> dict of attributes
    for _, row in tott.iterrows():
        fid = row["fid"]
        if fid is None:
            continue
        name = detname.get(fid) or (row["FIGHTER"] if isinstance(row["FIGHTER"], str) else None)
        fighter_rows[fid] = {
            "name": name,
            "nickname": (nick.get(fid) if isinstance(nick.get(fid), str) else None),
            "height_in": parse_height(row["HEIGHT"]),
            "reach_in": parse_reach(row["REACH"]),
            "stance": (row["STANCE"] if isinstance(row["STANCE"], str) else None),
            "dob": parse_dob(row["DOB"]),
        }
    # fighters present in details but not tott
    for fid, name in detname.items():
        if fid not in fighter_rows:
            fighter_rows[fid] = {"name": name,
                                 "nickname": nick.get(fid) if isinstance(nick.get(fid), str) else None,
                                 "height_in": None, "reach_in": None,
                                 "stance": None, "dob": None}

    prov_fields = ("height_in", "reach_in", "stance", "dob", "nickname")
    for fid, r in fighter_rows.items():
        if not r["name"]:
            continue
        existing = cur.execute("SELECT id FROM fighters WHERE ufcstats_id = ?", (fid,)).fetchone()
        if existing:
            cur.execute(
                "UPDATE fighters SET name=?, nickname=?, dob=?, height_in=?, reach_in=?, "
                "stance=?, updated_at=datetime('now') WHERE ufcstats_id=?",
                (r["name"], r["nickname"], r["dob"], r["height_in"], r["reach_in"],
                 r["stance"], fid))
            fid_db = existing["id"]
        else:
            cur.execute(
                "INSERT INTO fighters (name, nickname, dob, height_in, reach_in, stance, ufcstats_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (r["name"], r["nickname"], r["dob"], r["height_in"], r["reach_in"],
                 r["stance"], fid))
            fid_db = cur.lastrowid
            for f in prov_fields:
                if r[f] is not None:
                    log_provenance(conn, "fighters", fid_db, f, SOURCE, r[f])
            log_provenance(conn, "fighters", fid_db, "ufcstats_id", SOURCE, fid)
    conn.commit()

    # db id maps
    id_by_ufc = {row["ufcstats_id"]: row["id"]
                 for row in cur.execute("SELECT id, ufcstats_id FROM fighters WHERE ufcstats_id IS NOT NULL")}

    # name -> ufcstats fid, for resolving bout participants.  tott names match
    # bout strings best; fall back to details names.  Track ambiguous names.
    name_to_fids = {}
    for _, row in tott.iterrows():
        if isinstance(row["FIGHTER"], str) and row["fid"]:
            name_to_fids.setdefault(row["FIGHTER"], set()).add(row["fid"])
    for fid, name in detname.items():
        if name:
            name_to_fids.setdefault(name, set()).add(fid)
    ambiguous_names = {n for n, s in name_to_fids.items() if len(s) > 1}
    name_to_fid = {n: sorted(s)[0] for n, s in name_to_fids.items()}  # deterministic pick

    nameonly_cache = {}  # name -> db id for fighters with no ufcstats id

    def resolve_fighter(name: str) -> int:
        name = name.strip()
        fid = name_to_fid.get(name)
        if fid is not None:
            return id_by_ufc[fid]
        if name in nameonly_cache:
            return nameonly_cache[name]
        existing = cur.execute(
            "SELECT id FROM fighters WHERE name=? AND ufcstats_id IS NULL", (name,)).fetchone()
        if existing:
            nameonly_cache[name] = existing["id"]
            return existing["id"]
        cur.execute("INSERT INTO fighters (name) VALUES (?)", (name,))
        nameonly_cache[name] = cur.lastrowid
        return cur.lastrowid

    # ---------------- events ---------------------------------------------
    event_dbid = {}   # event name -> db id
    for _, row in ev.iterrows():
        name = row["EVENT"]
        if not isinstance(name, str):
            continue
        uid = ufcid(row["URL"])
        edate = parse_event_date(row["DATE"])
        loc = row["LOCATION"] if isinstance(row["LOCATION"], str) else None
        status = "completed"
        if edate and date.fromisoformat(edate) > date.today():
            status = "upcoming"
        existing = cur.execute("SELECT id FROM events WHERE ufcstats_id=?", (uid,)).fetchone()
        if existing:
            cur.execute("UPDATE events SET name=?, date=?, location=?, status=?, "
                        "updated_at=datetime('now') WHERE ufcstats_id=?",
                        (name, edate, loc, status, uid))
            event_dbid[name] = existing["id"]
        else:
            cur.execute("INSERT INTO events (name, date, location, status, ufcstats_id) "
                        "VALUES (?,?,?,?,?)", (name, edate, loc, status, uid))
            eid = cur.lastrowid
            event_dbid[name] = eid
            for f, v in (("date", edate), ("location", loc), ("ufcstats_id", uid)):
                if v is not None:
                    log_provenance(conn, "events", eid, f, SOURCE, v)
    conn.commit()

    # ---------------- fights ---------------------------------------------
    # card_position: order within event as it appears in the results CSV
    # (ufcstats lists the main event first).
    pos_counter = {}
    prov_fight_fields = ("method", "round", "time_seconds", "weight_class", "ufcstats_id")
    fight_dbid_by_bout = {}   # (event_name, bout) -> fight db id
    fighters_in_fight = {}    # fight db id -> (red_id, blue_id)

    for _, row in res.iterrows():
        ename = row["EVENT"]
        bout = row["BOUT"]
        if not isinstance(ename, str) or not isinstance(bout, str) or " vs. " not in bout:
            continue
        eid = event_dbid.get(ename)
        if eid is None:
            continue
        red_name, blue_name = [p.strip() for p in bout.split(" vs. ", 1)]
        red_id = resolve_fighter(red_name)
        blue_id = resolve_fighter(blue_name)

        outcome = row["OUTCOME"] if isinstance(row["OUTCOME"], str) else ""
        method_raw = row["METHOD"] if isinstance(row["METHOD"], str) else ""
        method = METHOD_MAP.get(method_raw.strip())
        winner_id = None
        if outcome == "W/L":
            result = "win"
            winner_id = red_id
        elif outcome == "L/W":
            result = "win"
            winner_id = blue_id
        elif outcome == "D/D":
            result = "draw"
        elif outcome == "NC/NC":
            result = "nc"
            method = "NC"
        else:
            result = None
        # DQ result refinement (schema vocab includes 'dq')
        if method == "DQ" and result == "win":
            result = "dq"

        division, is_title, is_interim, wc_stored = classify_weightclass(row["WEIGHTCLASS"])
        rnd = parse_int(row["ROUND"])
        tsec = parse_time_seconds(row["TIME"])
        sched = parse_scheduled_rounds(row["TIME FORMAT"])
        details = row["DETAILS"]
        is_decision = isinstance(method_raw, str) and method_raw.startswith("Decision")
        judge_scores = parse_judge_scores(details) if is_decision else None
        method_detail = None if is_decision else (details.strip() if isinstance(details, str) else None)
        uid = ufcid(row["URL"])

        pos_counter[ename] = pos_counter.get(ename, 0) + 1
        card_pos = pos_counter[ename]
        is_main = 1 if card_pos == 1 else 0

        existing = cur.execute("SELECT id FROM fights WHERE ufcstats_id=?", (uid,)).fetchone()
        params = (eid, red_id, blue_id, winner_id, result, result, method, method_detail,
                  rnd, tsec, wc_stored, sched, is_title, is_interim, is_main, card_pos,
                  judge_scores, uid)
        if existing:
            cur.execute(
                "UPDATE fights SET event_id=?, fighter_red_id=?, fighter_blue_id=?, winner_id=?, "
                "result_original=?, result_current=?, method=?, method_detail=?, round=?, "
                "time_seconds=?, weight_class=?, scheduled_rounds=?, is_title=?, is_interim_title=?, "
                "is_main_event=?, card_position=?, judge_scores=?, updated_at=datetime('now') "
                "WHERE ufcstats_id=?",
                params[:-1] + (uid,))
            fdb = existing["id"]
        else:
            cur.execute(
                "INSERT INTO fights (event_id, fighter_red_id, fighter_blue_id, winner_id, "
                "result_original, result_current, method, method_detail, round, time_seconds, "
                "weight_class, scheduled_rounds, is_title, is_interim_title, is_main_event, "
                "card_position, judge_scores, ufcstats_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                params)
            fdb = cur.lastrowid
            for f, v in (("method", method), ("round", rnd), ("time_seconds", tsec),
                         ("weight_class", wc_stored), ("ufcstats_id", uid)):
                if v is not None:
                    log_provenance(conn, "fights", fdb, f, SOURCE, v)
        fight_dbid_by_bout[(ename, bout)] = fdb
        fighters_in_fight[fdb] = (red_id, blue_id, red_name, blue_name)
    conn.commit()

    # ---------------- fight_stats (aggregate rounds per fighter) ----------
    # sum round-level rows into one row per (fight, fighter)
    agg = {}  # (event,bout,fighter) -> accumulator
    for _, row in stats.iterrows():
        key = (row["EVENT"], row["BOUT"], row["FIGHTER"])
        if not all(isinstance(k, str) for k in key):
            continue
        a = agg.setdefault(key, {"kd": 0, "sl": 0, "sa": 0, "tsl": 0, "tsa": 0,
                                 "tdl": 0, "tda": 0, "sub": 0, "rev": 0, "ctrl": 0,
                                 "head": 0, "body": 0, "leg": 0, "dist": 0,
                                 "clinch": 0, "ground": 0, "has_ctrl": False})
        a["kd"] += parse_int(row["KD"]) or 0
        for fld, col in (("s", "SIG.STR."), ("ts", "TOTAL STR."), ("td", "TD")):
            l, at = parse_x_of_y(row[col])
            if l is not None:
                a[fld + "l"] += l
                a[fld + "a"] += at
        for fld, col in (("head", "HEAD"), ("body", "BODY"), ("leg", "LEG"),
                         ("dist", "DISTANCE"), ("clinch", "CLINCH"), ("ground", "GROUND")):
            l, _ = parse_x_of_y(row[col])
            if l is not None:
                a[fld] += l
        a["sub"] += parse_int(row["SUB.ATT"]) or 0
        a["rev"] += parse_int(row["REV."]) or 0
        c = parse_time_seconds(row["CTRL"])
        if c is not None:
            a["ctrl"] += c
            a["has_ctrl"] = True

    stat_rows = 0
    for (ename, bout, fighter), a in agg.items():
        fdb = fight_dbid_by_bout.get((ename, bout))
        if fdb is None:
            continue
        red_id, blue_id, red_name, blue_name = fighters_in_fight[fdb]
        if fighter.strip() == red_name:
            fighter_db = red_id
        elif fighter.strip() == blue_name:
            fighter_db = blue_id
        else:
            fighter_db = resolve_fighter(fighter)
        ctrl = a["ctrl"] if a["has_ctrl"] else None
        vals = (fdb, fighter_db, a["kd"], a["sl"], a["sa"], a["head"], a["body"],
                a["leg"], a["dist"], a["clinch"], a["ground"], a["tsl"], a["tsa"],
                a["tdl"], a["tda"], a["sub"], a["rev"], ctrl)
        cur.execute(
            "INSERT INTO fight_stats (fight_id, fighter_id, knockdowns, sig_strikes_landed, "
            "sig_strikes_attempted, sig_head_landed, sig_body_landed, sig_leg_landed, "
            "sig_distance_landed, sig_clinch_landed, sig_ground_landed, total_strikes_landed, "
            "total_strikes_attempted, takedowns_landed, takedowns_attempted, sub_attempts, "
            "reversals, control_time_seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fight_id, fighter_id) DO UPDATE SET "
            "knockdowns=excluded.knockdowns, sig_strikes_landed=excluded.sig_strikes_landed, "
            "sig_strikes_attempted=excluded.sig_strikes_attempted, sig_head_landed=excluded.sig_head_landed, "
            "sig_body_landed=excluded.sig_body_landed, sig_leg_landed=excluded.sig_leg_landed, "
            "sig_distance_landed=excluded.sig_distance_landed, sig_clinch_landed=excluded.sig_clinch_landed, "
            "sig_ground_landed=excluded.sig_ground_landed, total_strikes_landed=excluded.total_strikes_landed, "
            "total_strikes_attempted=excluded.total_strikes_attempted, takedowns_landed=excluded.takedowns_landed, "
            "takedowns_attempted=excluded.takedowns_attempted, sub_attempts=excluded.sub_attempts, "
            "reversals=excluded.reversals, control_time_seconds=excluded.control_time_seconds",
            vals)
        stat_rows += 1
    conn.commit()

    # ---------------- derive fighter W/L/D records -----------------------
    cur.execute("UPDATE fighters SET wins=0, losses=0, draws=0")
    # wins/losses from decisive fights
    cur.execute("""
        UPDATE fighters SET wins = (
            SELECT COUNT(*) FROM fights f WHERE f.winner_id = fighters.id
            AND f.result_current IN ('win','dq'))""")
    cur.execute("""
        UPDATE fighters SET losses = (
            SELECT COUNT(*) FROM fights f
            WHERE f.result_current IN ('win','dq') AND f.winner_id IS NOT NULL
            AND ((f.fighter_red_id = fighters.id OR f.fighter_blue_id = fighters.id)
                 AND f.winner_id <> fighters.id))""")
    cur.execute("""
        UPDATE fighters SET draws = (
            SELECT COUNT(*) FROM fights f WHERE f.result_current='draw'
            AND (f.fighter_red_id = fighters.id OR f.fighter_blue_id = fighters.id))""")
    conn.commit()

    unmapped = sorted({m for m in res["METHOD"].dropna().unique()
                       if METHOD_MAP.get(str(m).strip()) is None
                       and not str(m).startswith("Decision")})

    # -------- report -----------------------------------------------------
    def n(t):
        return cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print("=== ingestion complete ===")
    for t in ("fighters", "events", "fights", "fight_stats", "provenance"):
        print(f"  {t:12s} {n(t)}")
    print(f"  fight_stats rows written this run: {stat_rows}")
    print(f"  ambiguous (duplicate) fighter names: {len(ambiguous_names)} -> {sorted(ambiguous_names)}")
    print(f"  name-only fighters (no ufcstats id): {len(nameonly_cache)}")
    print(f"  unmapped non-decision methods: {unmapped}")
    conn.close()


if __name__ == "__main__":
    main()
