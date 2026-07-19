"""Scrape https://www.ufc.com/events (upcoming cards) into events + fights.

ufcstats.com's upcoming-events feed is currently behind a JavaScript anti-bot
wall (see ufcstats.py / base.ChallengeError), so the DB has no upcoming events
for the odds client to attach lines to. ufc.com is open (robots.txt asks for
crawl-delay 15, honoured by base.HOST_DELAYS), and /events is not disallowed, so
this module is the compliant upcoming-card path.

Pipeline
--------
  /events  ->  the result cards give us each event's ufc.com slug
  /event/<slug>  ->  canonical name (prefix + main-event matchup), date, venue,
                     location, and the full announced bout list.

The /events page also lists a handful of RECENT COMPLETED cards (results); we
fetch each event page, parse its date, and only persist events dated today or
later as status 'upcoming'. Fight-night slugs embed their date
(ufc-fight-night-july-18-2026), so past ones are skipped WITHOUT a fetch to keep
request volume (and the 15s crawl delay) down; numbered/named events
(ufc-330, ufc-freedom-250) carry no date in the slug and are fetched to inspect.

Reconciliation (per the project RECONCILIATION RULE, via base.py):
  * events: matched by ufc_slug first, else by (normalized name, date); updated
    in place, never duplicated. ufc_slug / venue / location / status reconciled
    through apply_field (keep-existing-on-conflict + provenance discrepancy log).
  * fighters: matched by normalized name against the ufcstats-derived roster; a
    NEW fighter row is created only for a genuine unknown (e.g. a UFC newcomer),
    with provenance source 'ufc.com'. Cutout image_url and ufc_slug (hotlink
    only -- images are never downloaded) reconciled via apply_field.
  * fights: matched by (event_id, both fighters) in either corner order; ufc.com
    supplies no ufcstats fight id, so that natural key is the idempotency key.
    result_original = result_current = 'upcoming'.

DISAPPEARING-BOUT POLICY: if a bout is pulled from a card on a later sync we do
NOT delete its fights row -- history is append-only and a rating recompute may
still reference it. Such a row simply stops being refreshed (its result stays
'upcoming' until a completed-event source overwrites it).

Watermark: sync_state source 'ufc.com-events' = latest upcoming event date seen.

    python data/scrapers/ufc_events.py
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import db  # noqa: E402  (shared helper; base already put data/ on sys.path)

SOURCE = "ufc.com"
WATERMARK_SOURCE = "ufc.com-events"
EVENTS_URL = "https://www.ufc.com/events"
EVENT_URL = "https://www.ufc.com/event/{slug}"

_MONTHS = {m[:3].lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# Fight-night slugs embed the date: ufc-fight-night-july-18-2026
_SLUG_DATE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)-(\d{1,2})-(\d{4})", re.I)
# Server-rendered hero suffix: "Sat, Jul 18 / 7:00 PM CDT"
_SUFFIX_DATE = re.compile(r"([A-Za-z]{3,})\s+(\d{1,2})")


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #

def parse_slug_date(slug: str) -> str | None:
    """'ufc-fight-night-july-18-2026' -> '2026-07-18'. None if no date in slug."""
    m = _SLUG_DATE.search(slug)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def parse_suffix_date(text: str | None, today: date) -> str | None:
    """'Sat, Jul 18 / 7:00 PM CDT' -> ISO date. The suffix carries no year, so we
    pick the year (today-1 / today / today+1) whose date is closest to today --
    correct for any card shown on /events, and stable across the Dec/Jan boundary.
    The rendered time is UFC's default US-Central presentation, matching the US
    calendar date ufcstats uses."""
    if not text:
        return None
    m = _SUFFIX_DATE.search(text)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    day = int(m.group(2))
    best = None
    for yr in (today.year - 1, today.year, today.year + 1):
        try:
            cand = date(yr, mon, day)
        except ValueError:
            continue
        if best is None or abs((cand - today).days) < abs((best - today).days):
            best = cand
    return best.isoformat() if best else None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_events_list(html: str) -> list[dict]:
    """Return ordered {slug, venue, location} for each /events result card. Venue
    and location are cleanly split on the listing (the event page only carries a
    combined 'Venue, City Country' string), so we source them here."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()
    for card in soup.select(".c-card-event--result"):
        a = card.select_one(".c-card-event--result__headline a")
        href = a.get("href") if a else None
        if not href or "/event/" not in href:
            continue
        slug = href.rstrip("/").split("/event/")[-1].split("?")[0]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        ven = card.select_one(".field--name-taxonomy-term-title")
        loc = card.select_one(".field--name-location")
        location = " ".join(loc.get_text(" ").split()).replace(" ,", ",") if loc else None
        out.append({
            "slug": slug,
            "venue": " ".join(ven.get_text(" ").split()) if ven else None,
            "location": location,
        })
    return out


def _slug_from_athlete_href(href: str | None) -> str | None:
    if not href or "/athlete/" not in href:
        return None
    return href.rstrip("/").split("/athlete/")[-1].split("?")[0] or None


def _corner(fight, colour: str) -> dict:
    """Extract one corner: full name, ufc_slug, cutout image_url."""
    name_a = fight.select_one(f".c-listing-fight__corner-name--{colour} a") or \
        fight.select_one(f".c-listing-fight__corner-name--{colour}")
    given = fight.select_one(
        f".c-listing-fight__corner-name--{colour} .c-listing-fight__corner-given-name")
    family = fight.select_one(
        f".c-listing-fight__corner-name--{colour} .c-listing-fight__corner-family-name")
    if given or family:
        name = " ".join(
            p.get_text(strip=True) for p in (given, family) if p and p.get_text(strip=True))
    else:
        name = " ".join(name_a.get_text(" ").split()) if name_a else None
    href = name_a.get("href") if name_a else None
    img = fight.select_one(f".c-listing-fight__corner-image--{colour} img")
    return {
        "name": name or None,
        "family": family.get_text(strip=True) if family else None,
        "ufc_slug": _slug_from_athlete_href(href),
        "image_url": (img.get("src") if img else None),
    }


def _weight_and_flags(class_text: str | None) -> tuple[str | None, int, int]:
    """'Welterweight Title Bout' -> ('Welterweight', is_title=1, is_interim=0)."""
    if not class_text:
        return None, 0, 0
    t = " ".join(class_text.split())
    low = t.lower()
    is_title = 1 if "title" in low else 0
    is_interim = 1 if "interim" in low else 0
    wc = re.sub(r"\s*Bout\s*$", "", t, flags=re.I)
    wc = re.sub(r"\s*Interim\s*", " ", wc, flags=re.I)
    wc = re.sub(r"\s*Title\s*", " ", wc, flags=re.I)
    wc = " ".join(wc.split())
    return (wc or None), is_title, is_interim


def parse_event_page(html: str) -> dict:
    """Return {name, date_suffix, venue, location, fights:[...]}. `name` is the
    canonical event name (prefix + main-event matchup). `date_suffix` is the raw
    hero suffix text (year resolved by the caller)."""
    soup = BeautifulSoup(html, "lxml")

    prefix_el = soup.select_one(".c-hero__headline-prefix")
    prefix = prefix_el.get_text(" ", strip=True) if prefix_el else None
    suffix_el = soup.select_one(".c-hero__headline-suffix")
    date_suffix = suffix_el.get_text(" ", strip=True) if suffix_el else None

    # Matchup from the hero divider spans (top vs bottom) -> the official form,
    # e.g. "Makhachev vs Machado Garry".
    top = soup.select_one(".c-hero__headline .e-divider__top")
    bottom = soup.select_one(".c-hero__headline .e-divider__bottom")
    matchup = None
    if top and bottom:
        a = top.get_text(" ", strip=True)
        b = bottom.get_text(" ", strip=True)
        if a and b:
            matchup = f"{a} vs {b}"

    # Fallback venue/location from the event page's combined field.
    venue = location = None
    ven_el = soup.select_one(".field--name-venue")
    if ven_el:
        raw = " ".join(ven_el.get_text(" ").split())
        if "," in raw:
            venue, location = (p.strip() for p in raw.split(",", 1))
        else:
            venue = raw or None

    fights: list[dict] = []
    for pos, fc in enumerate(soup.select(".c-listing-fight"), start=1):
        red = _corner(fc, "red")
        blue = _corner(fc, "blue")
        if not (red["name"] and blue["name"]):
            continue
        class_el = fc.select_one(".c-listing-fight__class-text")
        wc, is_title, is_interim = _weight_and_flags(
            class_el.get_text(" ", strip=True) if class_el else None)
        fights.append({
            "red": red, "blue": blue,
            "weight_class": wc,
            "is_title": is_title, "is_interim_title": is_interim,
            "card_position": pos,
            "is_main_event": 1 if pos == 1 else 0,
        })

    # Canonical name: prefix + hero matchup ("UFC 330: Makhachev vs Machado Garry").
    # Fall back to the top bout's family names if the hero divider is absent.
    name = prefix
    if prefix:
        mu = matchup
        if not mu and fights:
            r = fights[0]["red"].get("family") or fights[0]["red"]["name"]
            b = fights[0]["blue"].get("family") or fights[0]["blue"]["name"]
            if r and b:
                mu = f"{r} vs {b}"
        if mu:
            name = f"{prefix}: {mu}"
    return {"name": name, "prefix": prefix, "date_suffix": date_suffix,
            "venue": venue, "location": location, "fights": fights}


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def _existing_fighter_id(name: str, ufc_slug: str | None,
                         norm_index: dict, slug_index: dict) -> int | None:
    """Resolve a card fighter to an existing row, or None for a genuine newcomer.

    Keys, in order: (1) normalized display name; (2) exact ufc_slug already stored
    on a fighter; (3) the ufc_slug read as a name ('michael-page' -> 'michael
    page'). Key (3) catches ufc.com display names that embed a nickname
    (e.g. 'Michael Venom Page', whose slug is still 'michael-page') so a
    long-tenured fighter is reconciled to their existing history instead of
    spawning a duplicate row."""
    fid = norm_index.get(base.normalize_name(name))
    if fid:
        return fid
    if ufc_slug:
        if ufc_slug in slug_index:
            return slug_index[ufc_slug]
        fid = norm_index.get(base.normalize_name(ufc_slug.replace("-", " ")))
        if fid:
            return fid
    return None


def _reconcile_event(conn, slug: str, page: dict, iso_date: str, venue: str | None,
                     location: str | None, norm_index: dict, slug_index: dict,
                     summary: dict) -> None:
    # Prefer the cleanly-split listing venue/location; fall back to the event page.
    venue = venue or page.get("venue")
    location = location or page.get("location")

    # Reconcile the event by ufc_slug first, else (normalized name, date).
    row = conn.execute("SELECT * FROM events WHERE ufc_slug = ?", (slug,)).fetchone()
    if row is not None:
        eid = row["id"]
        existing = dict(row)
        if iso_date:
            base.apply_field(conn, "events", eid, "date", iso_date, SOURCE,
                             existing=existing)
    else:
        eid = base.get_or_create_event(
            conn, page["name"], iso_date, SOURCE,
            fields={"venue": venue, "location": location, "status": "upcoming"})
        existing = dict(conn.execute("SELECT * FROM events WHERE id=?",
                                     (eid,)).fetchone())
    base.apply_field(conn, "events", eid, "ufc_slug", slug, SOURCE, existing=existing)
    base.apply_field(conn, "events", eid, "venue", venue, SOURCE, existing=existing)
    base.apply_field(conn, "events", eid, "location", location, SOURCE,
                     existing=existing)
    base.apply_field(conn, "events", eid, "status", "upcoming", SOURCE,
                     existing=existing)
    summary["events"] += 1

    for f in page["fights"]:
        red_id = _persist_fighter(conn, f["red"], norm_index, slug_index, summary)
        blue_id = _persist_fighter(conn, f["blue"], norm_index, slug_index, summary)
        fight_fields = {
            "weight_class": f.get("weight_class"),
            "is_title": f.get("is_title"),
            "is_interim_title": f.get("is_interim_title"),
            "card_position": f.get("card_position"),
            "is_main_event": f.get("is_main_event"),
            "result_original": "upcoming",
            "result_current": "upcoming",
        }
        base.get_or_create_fight(conn, eid, red_id, blue_id, SOURCE,
                                 fields=fight_fields)
        summary["fights"] += 1
    base.commit_with_retry(conn)


def _persist_fighter(conn, corner: dict, norm_index: dict, slug_index: dict,
                     summary: dict) -> int:
    name = corner["name"]
    ufc_slug = corner.get("ufc_slug")
    pre = _existing_fighter_id(name, ufc_slug, norm_index, slug_index)
    if pre is not None:
        fid = pre
        summary["fighters_matched"] += 1
        # If the ufc.com display name differs from our canonical row (e.g. an
        # embedded nickname), keep ours but log the variant as a provenance
        # discrepancy rather than silently dropping it.
        row = conn.execute("SELECT name FROM fighters WHERE id=?", (fid,)).fetchone()
        if row and base.normalize_name(row["name"]) != base.normalize_name(name):
            db.log_provenance(conn, "fighters", fid, "name", SOURCE, name)
    else:
        fid = base.get_or_create_fighter(conn, name, SOURCE)
        summary["fighters_created"] += 1
        summary["created_names"].append(name)
        # Register the new row so a repeat within this run reconciles, not dups.
        norm_index[base.normalize_name(name)] = fid
        if ufc_slug:
            slug_index[ufc_slug] = fid
    existing = dict(conn.execute("SELECT * FROM fighters WHERE id=?", (fid,)).fetchone())
    base.apply_field(conn, "fighters", fid, "ufc_slug", ufc_slug, SOURCE,
                     existing=existing)
    base.apply_field(conn, "fighters", fid, "image_url", corner.get("image_url"),
                     SOURCE, existing=existing)
    return fid


# --------------------------------------------------------------------------- #
# Sync driver
# --------------------------------------------------------------------------- #

def sync(conn=None, *, today: date | None = None) -> dict:
    owns = conn is None
    if conn is None:
        conn = base.connect()
    if today is None:
        today = date.today()
    today_iso = today.isoformat()

    summary = {"source": WATERMARK_SOURCE, "events": 0, "fights": 0,
               "fighters_matched": 0, "fighters_created": 0,
               "created_names": [], "event_names": [], "skipped_past": 0,
               "errors": []}

    try:
        list_html = base.fetch(EVENTS_URL)
    except base.ChallengeError as e:
        summary["errors"].append(str(e))
        if owns:
            conn.close()
        return summary
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(f"events list: {e}")
        if owns:
            conn.close()
        return summary

    listing = parse_events_list(list_html)

    # Indexes for fighter reconciliation + matched/created accounting.
    norm_index: dict[str, int] = {}
    slug_index: dict[str, int] = {}
    for r in conn.execute("SELECT id, name, ufc_slug FROM fighters").fetchall():
        nm = base.normalize_name(r["name"])
        if nm:
            norm_index.setdefault(nm, r["id"])
        if r["ufc_slug"]:
            slug_index.setdefault(r["ufc_slug"], r["id"])

    newest = base.get_watermark(conn, WATERMARK_SOURCE)
    for item in listing:
        slug = item["slug"]
        # Skip clearly-past fight-night cards without spending a fetch on them.
        slug_date = parse_slug_date(slug)
        if slug_date and slug_date < today_iso:
            summary["skipped_past"] += 1
            continue
        try:
            html = base.fetch(EVENT_URL.format(slug=slug))
            page = parse_event_page(html)
        except base.ChallengeError as e:
            summary["errors"].append(str(e))
            break
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"event {slug}: {e}")
            continue

        iso_date = parse_suffix_date(page.get("date_suffix"), today) or slug_date
        if iso_date and iso_date < today_iso:
            summary["skipped_past"] += 1
            continue
        if not page.get("fights"):
            # Card announced but no bouts yet -- still record the event shell.
            summary["errors"].append(f"event {slug}: no bouts parsed (card not set?)")

        _reconcile_event(conn, slug, page, iso_date, item.get("venue"),
                         item.get("location"), norm_index, slug_index, summary)
        summary["event_names"].append(f"{page.get('name')} [{iso_date}]")
        if iso_date and (newest is None or iso_date > newest):
            newest = iso_date

    if newest:
        base.set_watermark(conn, WATERMARK_SOURCE, newest,
                           note=f"upcoming through {newest}")
        base.commit_with_retry(conn)

    if owns:
        conn.close()
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(sync(), indent=2, ensure_ascii=False))
