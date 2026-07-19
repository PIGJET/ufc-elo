"""Shared scraping infrastructure for the UFC Elo live-sync layer.

Provides:
  * A compliant, rate-limited, disk-caching HTTP fetcher (identifying User-Agent,
    per-host request spacing, every raw response cached to data/cache/ keyed by a
    hash of the URL and reused on re-runs).
  * Anti-bot challenge detection (ufcstats.com currently fronts every page with a
    JavaScript proof-of-work "Checking your browser" wall). We DO NOT programmatically
    defeat that wall -- bypassing a site's bot-detection is out of scope. Instead we
    detect it and raise ChallengeError so callers can degrade gracefully. A user who
    has legitimately solved the challenge in their own browser may export the granted
    cookie into UFCSTATS_COOKIE and we will replay it.
  * Reconciliation helpers implementing the project's RECONCILIATION RULE: match
    existing rows by source id first, then by (normalized name[, date]); update in
    place; on a conflicting field value keep the existing value and log BOTH via
    provenance (that log is the discrepancy trail); never create duplicates.

This module does NOT modify data/db.py or data/schema.sql; it builds on get_conn /
init_db / log_provenance and adds a SQLite busy timeout for concurrency tolerance.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# Make `import db` work whether run as a module or a script.
_DATA_DIR = Path(__file__).resolve().parent.parent
if str(_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_DATA_DIR))
import db  # noqa: E402  (data/db.py — shared, do not modify)

CACHE_DIR = _DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "ufcelo-portfolio-project/0.1 (personal research)"

# Per-host minimum seconds between live requests. ufc.com robots.txt asks for
# crawl-delay: 15, which is stricter than the project's 3s floor, so we honour it.
HOST_DELAYS = {
    "ufcstats.com": 2.0,
    "www.ufc.com": 15.0,
    "ufc.com": 15.0,
}
DEFAULT_DELAY = 3.0

_last_request_at: dict[str, float] = {}


class ChallengeError(RuntimeError):
    """Raised when a site returns a JS anti-bot / proof-of-work interstitial
    instead of real content, and we have no legitimately-supplied session cookie."""


def _looks_like_challenge(text: str) -> bool:
    low = text.lower()
    return (
        "checking your browser" in low
        or ("this site requires javascript" in low and "sha256" in low)
        or ("var nonce=" in low and "/__c" in low)
    )


def cache_path_for(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.html"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _rate_limit(host: str) -> None:
    delay = HOST_DELAYS.get(host, DEFAULT_DELAY)
    now = time.monotonic()
    last = _last_request_at.get(host)
    if last is not None:
        wait = delay - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def _cookies_for(host: str) -> dict[str, str]:
    """Optional user-supplied session cookie (e.g. a challenge cookie the user
    obtained in their own browser). Format: raw Cookie header string."""
    if "ufcstats" in host:
        raw = os.environ.get("UFCSTATS_COOKIE", "").strip()
        if raw:
            out = {}
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    out[k.strip()] = v.strip()
            return out
    return {}


def fetch(url: str, *, force_refresh: bool = False, timeout: int = 30) -> str:
    """Return the raw HTML for `url`, using the disk cache when present.

    Compliant behaviour: identifying User-Agent, per-host rate limiting, cache
    every raw response and reuse on re-runs. Challenge interstitials are NOT
    cached (so a later run with a valid cookie can retry) and raise ChallengeError.
    """
    cp = cache_path_for(url)
    if cp.exists() and not force_refresh:
        text = cp.read_text(encoding="utf-8", errors="replace")
        if _looks_like_challenge(text):
            # A stale cached challenge page is useless; drop and refetch below.
            cp.unlink(missing_ok=True)
        else:
            return text

    host = _host(url)
    _rate_limit(host)
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        cookies=_cookies_for(host),
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.text

    if _looks_like_challenge(text):
        raise ChallengeError(
            f"{host} returned a JavaScript anti-bot proof-of-work challenge for {url}. "
            "This scraper does not defeat bot-detection walls. To ingest this source "
            "compliantly, solve the challenge in a real browser and export the granted "
            "cookie into the UFCSTATS_COOKIE environment variable, or use the historical "
            "dataset ingestion path."
        )

    cp.write_text(text, encoding="utf-8")
    return text


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def connect() -> sqlite3.Connection:
    """init_db() connection with a busy timeout for concurrent-writer tolerance."""
    conn = db.init_db()
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def commit_with_retry(conn: sqlite3.Connection, attempts: int = 6) -> None:
    for i in range(attempts):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and i < attempts - 1:
                time.sleep(0.4 * (i + 1))
                continue
            raise


# --------------------------------------------------------------------------- #
# Normalization + reconciliation
# --------------------------------------------------------------------------- #

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
# Letters NFKD does not decompose (distinct glyphs, not accented base + combiner).
_SPECIAL_LETTERS = str.maketrans({
    "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d",
    "ð": "d", "Ð": "d", "þ": "th", "ß": "ss", "æ": "ae", "œ": "oe",
})


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    # Fold accents (Benoît -> benoit, Lončar -> loncar) so cross-source names match.
    s = name.translate(_SPECIAL_LETTERS)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = s.replace(".", " ").replace("'", "").replace("`", "")
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def apply_field(conn: sqlite3.Connection, table: str, row_id: int, field: str,
                value, source: str, *, existing: dict) -> bool:
    """Set `table.field = value` under the reconciliation rule.

    - Skip empty/None incoming values.
    - Always log provenance (entity, id, field, source, value_seen).
    - If current value is NULL/empty -> fill it in.
    - If current value differs from incoming -> KEEP existing, do not overwrite
      (provenance now holds both observations = the discrepancy trail).
    Returns True if the stored value changed.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    db.log_provenance(conn, table, row_id, field, source, value)
    cur = existing.get(field)
    is_empty = cur is None or (isinstance(cur, str) and not cur.strip())
    if is_empty:
        conn.execute(
            f"UPDATE {table} SET {field} = ?, updated_at = ? WHERE id = ?",
            (value, _now(), row_id),
        )
        existing[field] = value
        return True
    # Conflict: keep existing; provenance already recorded the discrepancy.
    return False


def _row_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}


def get_or_create_fighter(conn: sqlite3.Connection, name: str, source: str,
                          ufcstats_id: str | None = None,
                          fields: dict | None = None) -> int:
    """Reconcile a fighter: match by ufcstats_id, else by normalized name.
    Update in place (reconciliation rule); never duplicate. Returns fighter id."""
    fields = dict(fields or {})
    row = None
    if ufcstats_id:
        row = conn.execute(
            "SELECT * FROM fighters WHERE ufcstats_id = ?", (ufcstats_id,)
        ).fetchone()
    if row is None:
        norm = normalize_name(name)
        # Exact-name candidates first (indexed), then normalized comparison.
        cands = conn.execute(
            "SELECT * FROM fighters WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchall()
        if not cands:
            cands = conn.execute("SELECT * FROM fighters").fetchall()
        for c in cands:
            if normalize_name(c["name"]) == norm and norm:
                row = c
                break

    if row is None:
        cur = conn.execute(
            "INSERT INTO fighters (name, ufcstats_id) VALUES (?, ?)",
            (name, ufcstats_id),
        )
        fid = cur.lastrowid
        db.log_provenance(conn, "fighters", fid, "name", source, name)
        if ufcstats_id:
            db.log_provenance(conn, "fighters", fid, "ufcstats_id", source, ufcstats_id)
        existing = {"name": name, "ufcstats_id": ufcstats_id}
    else:
        fid = row["id"]
        existing = _row_dict(row)
        if ufcstats_id and not existing.get("ufcstats_id"):
            conn.execute("UPDATE fighters SET ufcstats_id = ? WHERE id = ?",
                         (ufcstats_id, fid))
            existing["ufcstats_id"] = ufcstats_id
            db.log_provenance(conn, "fighters", fid, "ufcstats_id", source, ufcstats_id)

    for f, v in fields.items():
        apply_field(conn, "fighters", fid, f, v, source, existing=existing)
    return fid


def get_or_create_event(conn: sqlite3.Connection, name: str, date: str | None,
                        source: str, ufcstats_id: str | None = None,
                        fields: dict | None = None) -> int:
    """Reconcile an event: match by ufcstats_id, else by (normalized name, date)."""
    fields = dict(fields or {})
    row = None
    if ufcstats_id:
        row = conn.execute(
            "SELECT * FROM events WHERE ufcstats_id = ?", (ufcstats_id,)
        ).fetchone()
    if row is None:
        norm = normalize_name(name)
        for c in conn.execute("SELECT * FROM events").fetchall():
            if normalize_name(c["name"]) == norm and (c["date"] or "") == (date or ""):
                row = c
                break

    if row is None:
        cur = conn.execute(
            "INSERT INTO events (name, date, ufcstats_id) VALUES (?, ?, ?)",
            (name, date, ufcstats_id),
        )
        eid = cur.lastrowid
        db.log_provenance(conn, "events", eid, "name", source, name)
        if date:
            db.log_provenance(conn, "events", eid, "date", source, date)
        if ufcstats_id:
            db.log_provenance(conn, "events", eid, "ufcstats_id", source, ufcstats_id)
        existing = {"name": name, "date": date, "ufcstats_id": ufcstats_id}
    else:
        eid = row["id"]
        existing = _row_dict(row)
        if ufcstats_id and not existing.get("ufcstats_id"):
            conn.execute("UPDATE events SET ufcstats_id = ? WHERE id = ?",
                         (ufcstats_id, eid))
            existing["ufcstats_id"] = ufcstats_id
            db.log_provenance(conn, "events", eid, "ufcstats_id", source, ufcstats_id)

    if date:
        apply_field(conn, "events", eid, "date", date, source, existing=existing)
    for f, v in fields.items():
        apply_field(conn, "events", eid, f, v, source, existing=existing)
    return eid


def get_or_create_fight(conn: sqlite3.Connection, event_id: int, red_id: int,
                        blue_id: int, source: str, ufcstats_id: str | None = None,
                        fields: dict | None = None) -> int:
    """Reconcile a fight: match by ufcstats_id, else by (event_id, both fighters)
    in either corner order. Update in place; never duplicate."""
    fields = dict(fields or {})
    row = None
    if ufcstats_id:
        row = conn.execute(
            "SELECT * FROM fights WHERE ufcstats_id = ?", (ufcstats_id,)
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM fights WHERE event_id = ? AND "
            "((fighter_red_id = ? AND fighter_blue_id = ?) OR "
            " (fighter_red_id = ? AND fighter_blue_id = ?))",
            (event_id, red_id, blue_id, blue_id, red_id),
        ).fetchone()

    if row is None:
        cur = conn.execute(
            "INSERT INTO fights (event_id, fighter_red_id, fighter_blue_id, ufcstats_id)"
            " VALUES (?, ?, ?, ?)",
            (event_id, red_id, blue_id, ufcstats_id),
        )
        fid = cur.lastrowid
        if ufcstats_id:
            db.log_provenance(conn, "fights", fid, "ufcstats_id", source, ufcstats_id)
        existing = {"event_id": event_id, "fighter_red_id": red_id,
                    "fighter_blue_id": blue_id, "ufcstats_id": ufcstats_id}
    else:
        fid = row["id"]
        existing = _row_dict(row)
        if ufcstats_id and not existing.get("ufcstats_id"):
            conn.execute("UPDATE fights SET ufcstats_id = ? WHERE id = ?",
                         (ufcstats_id, fid))
            existing["ufcstats_id"] = ufcstats_id
            db.log_provenance(conn, "fights", fid, "ufcstats_id", source, ufcstats_id)

    # winner_id in fields may be red_id/blue_id already resolved by caller.
    for f, v in fields.items():
        apply_field(conn, "fights", fid, f, v, source, existing=existing)
    return fid


def upsert_fight_stats(conn: sqlite3.Connection, fight_id: int, fighter_id: int,
                       stats: dict) -> None:
    """Insert/replace the single fight_stats row for (fight_id, fighter_id)."""
    cols = [
        "knockdowns", "sig_strikes_landed", "sig_strikes_attempted",
        "sig_head_landed", "sig_body_landed", "sig_leg_landed",
        "sig_distance_landed", "sig_clinch_landed", "sig_ground_landed",
        "total_strikes_landed", "total_strikes_attempted",
        "takedowns_landed", "takedowns_attempted", "sub_attempts",
        "reversals", "control_time_seconds",
    ]
    existing = conn.execute(
        "SELECT id FROM fight_stats WHERE fight_id = ? AND fighter_id = ?",
        (fight_id, fighter_id),
    ).fetchone()
    vals = [stats.get(c) for c in cols]
    if existing:
        assignments = ", ".join(f"{c} = ?" for c in cols)
        conn.execute(
            f"UPDATE fight_stats SET {assignments} WHERE id = ?",
            (*vals, existing["id"]),
        )
    else:
        placeholders = ", ".join(["?"] * (len(cols) + 2))
        conn.execute(
            f"INSERT INTO fight_stats (fight_id, fighter_id, {', '.join(cols)}) "
            f"VALUES ({placeholders})",
            (fight_id, fighter_id, *vals),
        )


def get_watermark(conn: sqlite3.Connection, source: str) -> str | None:
    row = conn.execute("SELECT watermark FROM sync_state WHERE source = ?",
                       (source,)).fetchone()
    return row["watermark"] if row else None


def set_watermark(conn: sqlite3.Connection, source: str, watermark: str,
                  note: str = "") -> None:
    conn.execute(
        "INSERT INTO sync_state (source, watermark, last_run, note) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(source) DO UPDATE SET watermark = excluded.watermark, "
        "last_run = excluded.last_run, note = excluded.note",
        (source, watermark, _now(), note),
    )
