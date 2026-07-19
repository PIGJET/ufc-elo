"""Betting-odds client for upcoming UFC fights.

PRIMARY source: The Odds API (https://the-odds-api.com), sport key
`mma_mixed_martial_arts`, `h2h` market, American odds. API key from the
ODDS_API_KEY env var (loaded from a .env via python-dotenv if present).

Returned bouts are matched onto our `fights` rows (status upcoming) by
normalized fighter-name PAIR matching, and appended to the `odds` table
(append-only; line movement preserved) with source like
'the-odds-api:draftkings'.

If no API key is configured the module prints a clear pointer to the free-key
signup and exits 0 -- the pipeline must never crash on a missing key.

DraftKings direct scraping is intentionally NOT implemented (see the stub at the
bottom): it violates DraftKings' Terms of Service. It exists only as a documented,
disabled-by-default fallback.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    load_dotenv()  # also honour a .env in CWD
except Exception:  # noqa: BLE001
    pass

SOURCE_PREFIX = "the-odds-api"
API_URL = "https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds"
SIGNUP_URL = "https://the-odds-api.com"


def _api_key() -> str | None:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    return key or None


def fetch_odds(api_key: str, regions: str = "us", markets: str = "h2h") -> list[dict]:
    """Call The Odds API. Returns the raw list of events (bouts)."""
    resp = requests.get(
        API_URL,
        params={"apiKey": api_key, "regions": regions, "markets": markets,
                "oddsFormat": "american"},
        headers={"User-Agent": base.USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _upcoming_fights(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT f.id, f.fighter_red_id, f.fighter_blue_id, "
        "       fr.name AS red_name, fb.name AS blue_name "
        "FROM fights f "
        "JOIN fighters fr ON fr.id = f.fighter_red_id "
        "JOIN fighters fb ON fb.id = f.fighter_blue_id "
        "WHERE f.result_current = 'upcoming' OR f.result_original = 'upcoming'"
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "fight_id": r["id"],
            "red_id": r["fighter_red_id"], "blue_id": r["fighter_blue_id"],
            "red_norm": base.normalize_name(r["red_name"]),
            "blue_norm": base.normalize_name(r["blue_name"]),
        })
    return out


def _match_fight(bout_names: tuple[str, str], upcoming: list[dict]):
    """Return (fight, swapped) where swapped indicates the bout's first name maps
    to our blue corner. None if no pair match."""
    a, b = base.normalize_name(bout_names[0]), base.normalize_name(bout_names[1])
    pair = {a, b}
    for f in upcoming:
        if {f["red_norm"], f["blue_norm"]} == pair and "" not in pair:
            swapped = (a == f["blue_norm"])
            return f, swapped
    return None, False


def sync(conn=None) -> dict:
    owns = conn is None
    if conn is None:
        conn = base.connect()
    summary = {"source": SOURCE_PREFIX, "bouts_returned": 0, "matched": 0,
               "odds_rows": 0, "unmatched": 0, "errors": [], "no_key": False}

    key = _api_key()
    if not key:
        summary["no_key"] = True
        print("[odds_client] ODDS_API_KEY is not set. No odds were fetched.")
        print(f"[odds_client] Get a free key at {SIGNUP_URL} and set ODDS_API_KEY "
              "in your environment or a .env file.")
        if owns:
            conn.close()
        return summary

    try:
        events = fetch_odds(key)
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(str(e))
        print(f"[odds_client] The Odds API request failed: {e}")
        if owns:
            conn.close()
        return summary

    summary["bouts_returned"] = len(events)
    upcoming = _upcoming_fights(conn)

    for ev in events:
        home, away = ev.get("home_team"), ev.get("away_team")
        if not home or not away:
            continue
        fight, swapped = _match_fight((home, away), upcoming)
        if not fight:
            summary["unmatched"] += 1
            continue
        summary["matched"] += 1
        for bm in ev.get("bookmakers", []):
            book = bm.get("key", "book")
            price = {}
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for oc in market.get("outcomes", []):
                    price[base.normalize_name(oc.get("name"))] = oc.get("price")
            # Map home/away to red/blue.
            red_norm, blue_norm = fight["red_norm"], fight["blue_norm"]
            red_ml = price.get(red_norm)
            blue_ml = price.get(blue_norm)
            if red_ml is None and blue_ml is None:
                continue
            conn.execute(
                "INSERT INTO odds (fight_id, source, red_moneyline, blue_moneyline) "
                "VALUES (?, ?, ?, ?)",
                (fight["fight_id"], f"{SOURCE_PREFIX}:{book}",
                 int(red_ml) if red_ml is not None else None,
                 int(blue_ml) if blue_ml is not None else None),
            )
            summary["odds_rows"] += 1
        base.commit_with_retry(conn)

    if owns:
        conn.close()
    return summary


# --------------------------------------------------------------------------- #
# DISABLED-BY-DEFAULT fallback. DO NOT ENABLE without understanding the ToS risk.
# --------------------------------------------------------------------------- #
def scrape_draftkings_direct(*args, **kwargs):
    """Documented, intentionally-unimplemented DraftKings direct-scrape fallback.

    This exists ONLY as a placeholder for a last-resort path if The Odds API is
    unavailable. It is deliberately not implemented because:

      * DraftKings' Terms of Service prohibit automated scraping of their site.
      * The compliant, supported path is The Odds API (which itself aggregates
        DraftKings / FanDuel lines) -- use ODDS_API_KEY above.

    Enabling real DraftKings scraping would be at-your-own-risk, must run at a
    very low request rate against only public pages, and is outside this project's
    compliance posture. No scraping logic is provided here on purpose.
    """
    raise NotImplementedError(
        "DraftKings direct scraping is disabled: it violates DraftKings' ToS. "
        "Use The Odds API via ODDS_API_KEY instead (see module docstring)."
    )


if __name__ == "__main__":
    import json
    print(json.dumps(sync(), indent=2))
