"""On-demand ufc.com athlete-page scraper.

Given a fighter's ufc.com slug, fetch https://www.ufc.com/athlete/<slug> and
reconcile nickname, hero cutout image_url (hotlink only), leg_reach_in, country,
division and status into the fighters row. Rate-limited and cached via base.fetch.
Function + CLI, not a bulk crawl.

    python data/scrapers/ufc_athletes.py islam-makhachev israel-adesanya
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "ufc.com"


def _bio_fields(soup) -> dict:
    fields = {}
    for f in soup.select("div.c-bio__field"):
        lab = f.select_one(".c-bio__label")
        val = f.select_one(".c-bio__text")
        if lab and val:
            fields[lab.get_text(strip=True).lower()] = " ".join(val.get_text(" ").split())
    return fields


def _num(text: str | None):
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def parse_athlete(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    name = soup.select_one("h1.hero-profile__name")
    out["name"] = name.get_text(strip=True) if name else None
    nick = soup.select_one("p.hero-profile__nickname")
    out["nickname"] = nick.get_text(strip=True).strip('"') if nick else None
    img = soup.select_one("img.hero-profile__image, .hero-profile__image img")
    out["image_url"] = img.get("src") if img else None
    div = soup.select_one("p.hero-profile__division-title")
    if div:
        out["division"] = re.sub(r"\s*Division$", "", div.get_text(strip=True))

    bio = _bio_fields(soup)
    out["status"] = bio.get("status")
    hometown = bio.get("hometown")
    if hometown:
        out["country"] = hometown.split(",")[-1].strip()
    out["leg_reach_in"] = _num(bio.get("leg reach"))
    out["height_in"] = _num(bio.get("height"))
    out["reach_in"] = _num(bio.get("reach"))
    return out


def sync_athlete(slug: str, conn=None) -> dict:
    """Fetch + reconcile one athlete. Returns {slug, fighter_id, name, fields, error}."""
    owns = conn is None
    if conn is None:
        conn = base.connect()
    result = {"slug": slug, "fighter_id": None, "name": None, "error": None}
    url = f"https://www.ufc.com/athlete/{slug}"
    try:
        html = base.fetch(url)
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        if owns:
            conn.close()
        return result

    data = parse_athlete(html)
    result["name"] = data.get("name")
    if not data.get("name"):
        result["error"] = "no athlete name parsed (page layout changed?)"
        if owns:
            conn.close()
        return result

    # Match by ufc_slug first, then by name; create if absent (this scraper is
    # explicitly an enrichment entry point, so a new fighter row is allowed here).
    row = conn.execute("SELECT * FROM fighters WHERE ufc_slug = ?", (slug,)).fetchone()
    if row is None:
        fid = base.get_or_create_fighter(conn, data["name"], SOURCE)
    else:
        fid = row["id"]
    result["fighter_id"] = fid

    existing = dict(conn.execute("SELECT * FROM fighters WHERE id=?", (fid,)).fetchone())
    base.apply_field(conn, "fighters", fid, "ufc_slug", slug, SOURCE, existing=existing)
    for field in ("nickname", "image_url", "leg_reach_in", "country", "division",
                  "status", "height_in", "reach_in"):
        base.apply_field(conn, "fighters", fid, field, data.get(field), SOURCE,
                         existing=existing)
    base.commit_with_retry(conn)
    result["fields"] = {k: data.get(k) for k in
                        ("nickname", "image_url", "leg_reach_in", "country",
                         "division", "status")}
    if owns:
        conn.close()
    return result


def sync_many(slugs: list[str], conn=None) -> list[dict]:
    owns = conn is None
    if conn is None:
        conn = base.connect()
    out = [sync_athlete(s, conn=conn) for s in slugs]
    if owns:
        conn.close()
    return out


if __name__ == "__main__":
    import json
    args = sys.argv[1:] or ["islam-makhachev", "israel-adesanya", "jon-jones"]
    print(json.dumps(sync_many(args), indent=2))
