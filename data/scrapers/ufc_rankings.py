"""Scrape https://www.ufc.com/rankings into rankings_snapshot rows for today.

For every division (deduped -- the page renders each twice):
  * rank 0 = champion (the caption athlete, only when labelled "Champion";
    Pound-for-Pound groupings have no champion and start at rank 1),
  * ranks 1..N = the table rows, with movement indicators (+up / -down).

Fighter ids are resolved by normalized-name match against the fighters table;
unmatched rows keep fighter_id NULL and store the raw fighter_name (per the task
rule -- rankings never fabricate fighter rows). Where a matched fighter has a
ufc.com athlete slug and/or cutout image on the page, we reconcile those into
fighters.ufc_slug / fighters.image_url (hotlink URL only; images are never
downloaded).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "ufc.com"
RANKINGS_URL = "https://www.ufc.com/rankings"


def _clean_division(text: str) -> str:
    t = " ".join(text.split())
    for suffix in ("Top Rank", "TopRank"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t


def _slug_from_href(href: str | None) -> str | None:
    if not href:
        return None
    return href.rstrip("/").split("/athlete/")[-1].split("/")[0] if "/athlete/" in href else None


def _movement(cell) -> int:
    if cell is None:
        return 0
    span = cell.select_one("span")
    txt = " ".join(cell.get_text(" ").split())
    num = 0
    for tok in txt.replace("Rank increased by", "").replace(
            "Rank decreased by", "").split():
        if tok.lstrip("-").isdigit():
            num = int(tok)
            break
    if span is not None:
        cls = " ".join(span.get("class", []))
        if "decrease" in cls:
            return -abs(num)
        if "increase" in cls:
            return abs(num)
    return num


def parse_rankings(html: str) -> list[dict]:
    """Return deduped divisions:
    [{division, champion:{name,slug,image_url}|None, ranked:[{rank,name,slug,movement}]}]"""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()

    for g in soup.select("div.view-grouping"):
        cap_h4 = g.select_one("caption .info h4")
        header = g.select_one("div.view-grouping-header")
        raw = cap_h4.get_text() if cap_h4 else (header.get_text() if header else "")
        division = _clean_division(raw)
        if not division or division.lower() in seen:
            continue
        seen.add(division.lower())

        champion = None
        h6 = g.select_one("caption .info h6")
        if h6 and "champion" in h6.get_text(strip=True).lower():
            a = g.select_one("caption .info h5 a")
            img = g.select_one("caption img")
            if a:
                champion = {
                    "name": a.get_text(strip=True),
                    "slug": _slug_from_href(a.get("href")),
                    "image_url": (img.get("src") if img else None),
                }

        ranked = []
        for r in g.select("table tbody tr"):
            rank_cell = r.select_one("td.views-field-weight-class-rank")
            title = r.select_one("td.views-field-title a")
            change = r.select_one("td.views-field-weight-class-rank-change")
            if not title:
                continue
            rank_txt = rank_cell.get_text(strip=True) if rank_cell else ""
            if not rank_txt.isdigit():
                continue
            ranked.append({
                "rank": int(rank_txt),
                "name": title.get_text(strip=True),
                "slug": _slug_from_href(title.get("href")),
                "movement": _movement(change),
            })
        out.append({"division": division, "champion": champion, "ranked": ranked})
    return out


def _match_fighter_id(conn, name: str):
    norm = base.normalize_name(name)
    row = conn.execute("SELECT id, name FROM fighters WHERE name = ? COLLATE NOCASE",
                       (name,)).fetchone()
    if row:
        return row["id"]
    for c in conn.execute("SELECT id, name FROM fighters").fetchall():
        if base.normalize_name(c["name"]) == norm and norm:
            return c["id"]
    return None


def _attach_slug_image(conn, fighter_id, slug, image_url):
    if fighter_id is None:
        return
    existing = dict(conn.execute("SELECT * FROM fighters WHERE id=?",
                                 (fighter_id,)).fetchone())
    if slug:
        base.apply_field(conn, "fighters", fighter_id, "ufc_slug", slug, SOURCE,
                         existing=existing)
    if image_url:
        base.apply_field(conn, "fighters", fighter_id, "image_url", image_url, SOURCE,
                         existing=existing)


def sync(conn=None) -> dict:
    owns = conn is None
    if conn is None:
        conn = base.connect()
    summary = {"source": SOURCE + ":rankings", "divisions": 0, "rows": 0,
               "matched": 0, "unmatched": 0, "slug_image_updates": 0, "errors": []}
    try:
        html = base.fetch(RANKINGS_URL)
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(str(e))
        if owns:
            conn.close()
        return summary

    snapshot_date = time.strftime("%Y-%m-%d")
    divisions = parse_rankings(html)

    for d in divisions:
        summary["divisions"] += 1
        entries = []
        if d["champion"]:
            entries.append((0, d["champion"]["name"], d["champion"].get("slug"),
                            d["champion"].get("image_url"), 0))
        for r in d["ranked"]:
            entries.append((r["rank"], r["name"], r.get("slug"), None, r["movement"]))

        for rank, name, slug, image_url, movement in entries:
            fid = _match_fighter_id(conn, name)
            if fid:
                summary["matched"] += 1
                before = conn.total_changes
                _attach_slug_image(conn, fid, slug, image_url)
                if conn.total_changes > before:
                    summary["slug_image_updates"] += 1
            else:
                summary["unmatched"] += 1
            conn.execute(
                "INSERT INTO rankings_snapshot "
                "(snapshot_date, division, rank, fighter_id, fighter_name, movement) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(snapshot_date, division, rank) DO UPDATE SET "
                "fighter_id=excluded.fighter_id, fighter_name=excluded.fighter_name, "
                "movement=excluded.movement",
                (snapshot_date, d["division"], rank, fid, name, movement),
            )
            summary["rows"] += 1
        base.commit_with_retry(conn)

    if owns:
        conn.close()
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(sync(), indent=2))
