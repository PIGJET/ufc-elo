"""CLI orchestrator for the UFC Elo live-sync layer.

    python data/sync.py --all
    python data/sync.py --events            # ufcstats incremental + ufc.com upcoming cards
    python data/sync.py --rankings          # ufc.com rankings snapshot for today
    python data/sync.py --odds              # The Odds API -> odds table
    python data/sync.py --athletes 5        # enrich N ranked athletes by ufc_slug
    python data/sync.py --historical        # refresh completed-events base (GitHub, daily)
    python data/sync.py --events --max-completed 2

Each step reconciles into data/ufc.db per the project reconciliation rule and
prints a summary. Steps degrade gracefully: a blocked source (e.g. the ufcstats
anti-bot wall) or a missing odds key is reported, never fatal.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))
import base  # noqa: E402
import ufcstats  # noqa: E402
import ufc_events  # noqa: E402
import ufc_rankings  # noqa: E402
import ufc_athletes  # noqa: E402
import odds_client  # noqa: E402

_INGEST_SCRIPT = Path(__file__).resolve().parent / "ingest" / "ingest_historical.py"


def run_historical() -> dict:
    """Re-run the historical base ingestion (data/ingest/ingest_historical.py) as a
    subprocess. Its GitHub CSV source refreshes daily and the load is idempotent,
    so this is the compliant completed-events refresh path while ufcstats.com's
    live pages sit behind the anti-bot wall. We do not modify the ingest code; we
    just invoke it."""
    proc = subprocess.run(
        [sys.executable, str(_INGEST_SCRIPT)],
        capture_output=True, text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return {"source": "ingest_historical", "returncode": proc.returncode,
            "ok": proc.returncode == 0, "output_tail": tail,
            "stderr_tail": (proc.stderr or "").strip().splitlines()[-3:]}


def _athlete_slugs(conn, limit: int) -> list[str]:
    """Slugs to enrich: prefer today's ranked athletes with known ufc_slug, else
    any fighters carrying a ufc_slug."""
    rows = conn.execute(
        "SELECT DISTINCT fi.ufc_slug FROM fighters fi "
        "JOIN rankings_snapshot rs ON rs.fighter_id = fi.id "
        "WHERE fi.ufc_slug IS NOT NULL "
        "ORDER BY rs.rank LIMIT ?", (limit,)
    ).fetchall()
    slugs = [r["ufc_slug"] for r in rows]
    if len(slugs) < limit:
        extra = conn.execute(
            "SELECT ufc_slug FROM fighters WHERE ufc_slug IS NOT NULL LIMIT ?",
            (limit,)
        ).fetchall()
        for r in extra:
            if r["ufc_slug"] not in slugs:
                slugs.append(r["ufc_slug"])
            if len(slugs) >= limit:
                break
    return slugs[:limit]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="UFC Elo live-sync orchestrator")
    p.add_argument("--events", action="store_true",
                   help="ufcstats incremental sync + ufc.com upcoming cards")
    p.add_argument("--rankings", action="store_true", help="ufc.com rankings snapshot")
    p.add_argument("--odds", action="store_true", help="The Odds API odds sync")
    p.add_argument("--athletes", type=int, metavar="N", default=0,
                   help="enrich N ranked athletes via ufc.com athlete pages")
    p.add_argument("--historical", action="store_true",
                   help="re-run the historical base ingestion (GitHub, idempotent)")
    p.add_argument("--all", action="store_true", help="run every step")
    p.add_argument("--max-completed", type=int, default=2,
                   help="max newest completed events to pull (events step)")
    args = p.parse_args(argv)

    run_events = args.events or args.all
    run_rankings = args.rankings or args.all
    run_odds = args.odds or args.all
    run_athletes = args.athletes if args.athletes else (5 if args.all else 0)
    run_historical_step = args.historical or args.all

    if not (run_events or run_rankings or run_odds or run_athletes
            or run_historical_step):
        p.print_help()
        return 0

    results: dict[str, dict] = {}

    # Historical refresh runs first as an isolated subprocess (own DB connection),
    # so we don't hold our connection open across it.
    if run_historical_step:
        print("== historical base (ingest_historical.py) ==")
        results["historical"] = run_historical()
        print("  ", results["historical"])

    conn = base.connect()

    if run_events:
        print("== ufcstats events ==")
        results["events"] = ufcstats.sync(conn, max_completed=args.max_completed)
        print("  ", results["events"])
        # ufcstats upcoming is behind the anti-bot wall, so also pull upcoming
        # cards from ufc.com (open; crawl-delay 15 honoured by base.HOST_DELAYS).
        print("== ufc.com upcoming events ==")
        results["events_upcoming"] = ufc_events.sync(conn)
        print("  ", results["events_upcoming"])

    if run_rankings:
        print("== ufc.com rankings ==")
        results["rankings"] = ufc_rankings.sync(conn)
        print("  ", results["rankings"])

    if run_athletes:
        print(f"== ufc.com athletes (N={run_athletes}) ==")
        slugs = _athlete_slugs(conn, run_athletes)
        if not slugs:
            print("   no ufc_slug values available yet; run --rankings/--athletes with"
                  " explicit slugs first")
            results["athletes"] = {"enriched": 0, "slugs": []}
        else:
            res = ufc_athletes.sync_many(slugs, conn=conn)
            results["athletes"] = {"enriched": sum(1 for r in res if not r["error"]),
                                   "slugs": slugs,
                                   "errors": [r for r in res if r["error"]]}
            print("  ", results["athletes"])

    if run_odds:
        print("== odds (The Odds API) ==")
        results["odds"] = odds_client.sync(conn)
        print("  ", results["odds"])

    conn.close()

    print("\n===== SYNC SUMMARY =====")
    for step, r in results.items():
        print(f"[{step}] {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
