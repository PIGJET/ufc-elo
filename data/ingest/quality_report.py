"""Generate docs/data_quality_report.md from data/ufc.db.

Reports row counts, coverage by year, missing-field rates for key columns,
duplicate-name fighters, orphan references, and any method/result values that
failed to map.  Read-only; safe to run any time after ingestion.

Run:  python data/ingest/quality_report.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DATA_DIR.parent))
from data.db import get_conn  # noqa: E402

OUT = DATA_DIR.parent / "docs" / "data_quality_report.md"

VALID_METHODS = {"KO/TKO", "SUB", "U-DEC", "S-DEC", "M-DEC", "DQ", "NC"}
VALID_RESULTS = {"win", "draw", "nc", "dq", "upcoming"}


def main() -> None:
    conn = get_conn()
    conn.execute("PRAGMA busy_timeout = 30000")
    c = conn.cursor()

    def scalar(sql, *a):
        return c.execute(sql, a).fetchone()[0]

    L = []
    w = L.append
    w("# UFC Elo — Data Quality Report")
    w("")
    w(f"_Generated {datetime.now().isoformat(timespec='seconds')} from `data/ufc.db`._")
    w("")
    w("Source dataset: **Greco1899/scrape_ufc_stats** (complete ufcstats.com export). "
      "Raw CSVs archived in `data/raw/`.")
    w("")

    # ---- row counts ----
    w("## Row counts")
    w("")
    w("| Table | Rows |")
    w("|---|---:|")
    for t in ("fighters", "events", "fights", "fight_stats", "odds",
              "rankings_snapshot", "elo_history", "provenance"):
        w(f"| {t} | {scalar(f'SELECT COUNT(*) FROM {t}'):,} |")
    w("")

    # ---- fights per year ----
    w("## Fights per year (coverage gaps)")
    w("")
    w("| Year | Events | Fights |")
    w("|---|---:|---:|")
    rows = c.execute("""
        SELECT substr(e.date,1,4) AS yr,
               COUNT(DISTINCT e.id) AS events,
               COUNT(f.id) AS fights
        FROM events e LEFT JOIN fights f ON f.event_id = e.id
        WHERE e.date IS NOT NULL
        GROUP BY yr ORDER BY yr""").fetchall()
    for r in rows:
        w(f"| {r['yr']} | {r['events']} | {r['fights']} |")
    w("")

    # ---- missing-field rates ----
    w("## Missing / null rates for key fields")
    w("")
    w("| Entity | Field | Missing | Total | % missing |")
    w("|---|---|---:|---:|---:|")

    def missing(entity, field, total_sql, miss_sql):
        total = scalar(total_sql)
        miss = scalar(miss_sql)
        pct = (100.0 * miss / total) if total else 0.0
        w(f"| {entity} | {field} | {miss:,} | {total:,} | {pct:.1f}% |")

    nf = "SELECT COUNT(*) FROM fighters"
    missing("fighters", "reach_in", nf, "SELECT COUNT(*) FROM fighters WHERE reach_in IS NULL")
    missing("fighters", "height_in", nf, "SELECT COUNT(*) FROM fighters WHERE height_in IS NULL")
    missing("fighters", "dob", nf, "SELECT COUNT(*) FROM fighters WHERE dob IS NULL")
    missing("fighters", "stance", nf, "SELECT COUNT(*) FROM fighters WHERE stance IS NULL")
    missing("fighters", "ufcstats_id", nf, "SELECT COUNT(*) FROM fighters WHERE ufcstats_id IS NULL")

    ng = "SELECT COUNT(*) FROM fights"
    missing("fights", "method", ng, "SELECT COUNT(*) FROM fights WHERE method IS NULL")
    missing("fights", "round", ng, "SELECT COUNT(*) FROM fights WHERE round IS NULL")
    missing("fights", "time_seconds", ng, "SELECT COUNT(*) FROM fights WHERE time_seconds IS NULL")
    missing("fights", "weight_class", ng, "SELECT COUNT(*) FROM fights WHERE weight_class IS NULL")
    # winner missing is expected for draws/NCs; report only among decisive fights
    missing("fights", "winner_id (decisive only)",
            "SELECT COUNT(*) FROM fights WHERE result_current IN ('win','dq')",
            "SELECT COUNT(*) FROM fights WHERE result_current IN ('win','dq') AND winner_id IS NULL")
    missing("fights", "scheduled_rounds", ng, "SELECT COUNT(*) FROM fights WHERE scheduled_rounds IS NULL")
    w("")

    # ---- fight_stats coverage ----
    w("## Fight-stats coverage")
    w("")
    fights_total = scalar("SELECT COUNT(*) FROM fights")
    fights_with_stats = scalar("SELECT COUNT(DISTINCT fight_id) FROM fight_stats")
    w(f"- Fights with at least one stat row: **{fights_with_stats:,} / {fights_total:,}** "
      f"({100.0*fights_with_stats/fights_total:.1f}%).")
    w(f"- Fights with stats for **both** fighters: "
      f"**{scalar('SELECT COUNT(*) FROM (SELECT fight_id FROM fight_stats GROUP BY fight_id HAVING COUNT(*)=2)'):,}**.")
    ctrl_null = scalar("SELECT COUNT(*) FROM fight_stats WHERE control_time_seconds IS NULL")
    w(f"- Stat rows missing control time (pre-2010ish, not tracked): "
      f"**{ctrl_null:,} / {scalar('SELECT COUNT(*) FROM fight_stats'):,}**.")
    w("")

    # ---- duplicate-name fighters ----
    w("## Duplicate-name fighters")
    w("")
    dups = c.execute("""
        SELECT name, COUNT(*) n, GROUP_CONCAT(ufcstats_id, ', ') ids
        FROM fighters GROUP BY name HAVING n > 1 ORDER BY n DESC, name""").fetchall()
    if dups:
        w("Distinct fighters sharing a display name (bout-string name matching resolves "
          "these deterministically to the lowest ufcstats id — flagged for the scraper to reconcile):")
        w("")
        w("| Name | Count | ufcstats ids |")
        w("|---|---:|---|")
        for r in dups:
            w(f"| {r['name']} | {r['n']} | {r['ids'] or '(name-only)'} |")
    else:
        w("None.")
    w("")

    # ---- orphan references ----
    w("## Orphan references (referential integrity)")
    w("")
    w("| Check | Count |")
    w("|---|---:|")
    checks = [
        ("fights -> missing event", "SELECT COUNT(*) FROM fights f LEFT JOIN events e ON e.id=f.event_id WHERE e.id IS NULL"),
        ("fights -> missing red fighter", "SELECT COUNT(*) FROM fights f LEFT JOIN fighters x ON x.id=f.fighter_red_id WHERE x.id IS NULL"),
        ("fights -> missing blue fighter", "SELECT COUNT(*) FROM fights f LEFT JOIN fighters x ON x.id=f.fighter_blue_id WHERE x.id IS NULL"),
        ("fights -> winner not a participant",
         "SELECT COUNT(*) FROM fights WHERE winner_id IS NOT NULL AND winner_id NOT IN (fighter_red_id, fighter_blue_id)"),
        ("fight_stats -> missing fight", "SELECT COUNT(*) FROM fight_stats s LEFT JOIN fights f ON f.id=s.fight_id WHERE f.id IS NULL"),
        ("fight_stats -> missing fighter", "SELECT COUNT(*) FROM fight_stats s LEFT JOIN fighters x ON x.id=s.fighter_id WHERE x.id IS NULL"),
        ("fight_stats -> fighter not in its fight",
         "SELECT COUNT(*) FROM fight_stats s JOIN fights f ON f.id=s.fight_id WHERE s.fighter_id NOT IN (f.fighter_red_id, f.fighter_blue_id)"),
    ]
    for label, sql in checks:
        w(f"| {label} | {scalar(sql)} |")
    w("")

    # ---- unmapped method / result values ----
    w("## Method / result vocabulary")
    w("")
    w("Fights method values present (should all be in the schema vocab "
      "KO/TKO, SUB, U-DEC, S-DEC, M-DEC, DQ, NC):")
    w("")
    w("| method | count | valid? |")
    w("|---|---:|---|")
    for r in c.execute("SELECT method, COUNT(*) n FROM fights GROUP BY method ORDER BY n DESC"):
        m = r["method"]
        ok = "yes" if m in VALID_METHODS else ("NULL" if m is None else "**NO**")
        w(f"| {m} | {r['n']} | {ok} |")
    w("")
    w("| result_current | count | valid? |")
    w("|---|---:|---|")
    for r in c.execute("SELECT result_current, COUNT(*) n FROM fights GROUP BY result_current ORDER BY n DESC"):
        rc = r["result_current"]
        ok = "yes" if rc in VALID_RESULTS else ("NULL" if rc is None else "**NO**")
        w(f"| {rc} | {r['n']} | {ok} |")
    w("")

    # ---- ground-truth spot checks ----
    w("## Ground-truth spot checks")
    w("")
    total_events = scalar("SELECT COUNT(*) FROM events")
    dr = c.execute("SELECT MIN(date) mn, MAX(date) mx FROM events WHERE date IS NOT NULL").fetchone()
    w(f"- Total events: **{total_events}** (expected 700+). Date range **{dr['mn']} -> {dr['mx']}**.")
    spot = c.execute("""
        SELECT e.name ev, e.date, f.method, f.round, w.name winner
        FROM fights f JOIN events e ON e.id=f.event_id
        JOIN fighters r ON r.id=f.fighter_red_id JOIN fighters b ON b.id=f.fighter_blue_id
        LEFT JOIN fighters w ON w.id=f.winner_id
        WHERE (r.name LIKE '%Ngannou%' AND b.name LIKE '%Miocic%')
           OR (r.name LIKE '%Miocic%' AND b.name LIKE '%Ngannou%')
        ORDER BY e.date""").fetchall()
    for r in spot:
        w(f"- Miocic/Ngannou @ {r['ev']} ({r['date']}): {r['winner']} by {r['method']} R{r['round']}.")
    rr = c.execute("""
        SELECT e.name ev, e.date, f.method, w.name winner
        FROM fights f JOIN events e ON e.id=f.event_id
        JOIN fighters r ON r.id=f.fighter_red_id JOIN fighters b ON b.id=f.fighter_blue_id
        LEFT JOIN fighters w ON w.id=f.winner_id
        WHERE (r.name LIKE '%Nunes%' AND b.name LIKE '%Rousey%')
           OR (r.name LIKE '%Rousey%' AND b.name LIKE '%Nunes%')""").fetchall()
    for r in rr:
        w(f"- Nunes/Rousey @ {r['ev']} ({r['date']}): {r['winner']} by {r['method']}.")
    w("")

    # ---- known gaps ----
    w("## Known gaps & caveats")
    w("")
    w("- **UFC 1 (Nov 1993) is absent from the source export** — ufcstats.com's event "
      "listing that this scrape mirrors starts at UFC 2. Earliest event here is UFC 2 "
      "(1994-03-11). The live ufcstats scraper should backfill UFC 1.")
    w("- Round-by-round **control time is not recorded for older fights** (ufcstats only "
      "began tracking it ~2010); such stat rows have `control_time_seconds` NULL.")
    w("- Some earliest events have **no per-fight stats** at all in the source.")
    w("- `catchweight_lbs` is NULL: the source records the bout as 'Catch Weight' but not "
      "the contracted poundage.")
    w("- `country`, `division`, `image_url`, `ufc_slug`, `style_tag`, `pre_ufc_record`, "
      "`leg_reach_in` are not in this source and are left for ufc.com / derived stages.")
    w("")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    conn.close()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
