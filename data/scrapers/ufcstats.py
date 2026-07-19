"""Incremental sync from ufcstats.com.

Pipeline: completed-events list -> new events since the sync_state watermark ->
event page -> fights -> fight-detail pages -> per-fighter fight_stats + result /
method / round / time / title flags / judge scores. Also upcoming events (status
'upcoming', fights result 'upcoming') and fighter-detail physicals.

COMPLIANCE / BLOCKER: ufcstats.com currently fronts every page with a JavaScript
proof-of-work "Checking your browser" anti-bot wall. base.fetch() detects it and
raises ChallengeError; this scraper does NOT defeat the wall. Set UFCSTATS_COOKIE
(a cookie legitimately obtained by solving the challenge in your own browser) to
let the fetcher through, or ingest this source via the historical dataset path.

The parsers below target ufcstats.com's long-stable DOM. Because the live site is
walled in this environment, they are exercised against structural fixtures in
tests rather than live HTML; treat field-level extraction as needing one live
confirmation once a cookie is available.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "ufcstats"
COMPLETED_URL = "http://ufcstats.com/statistics/events/completed?page=all"
UPCOMING_URL = "http://ufcstats.com/statistics/events/upcoming?page=all"

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


# --------------------------------------------------------------------------- #
# Small parse helpers
# --------------------------------------------------------------------------- #

def parse_date(text: str | None) -> str | None:
    """'April 13, 2024' -> '2024-04-13'."""
    if not text:
        return None
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1))
    if not mon:
        return None
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def parse_of(text: str | None) -> tuple[int | None, int | None]:
    """'12 of 34' -> (12, 34). '---' / '' -> (None, None)."""
    if not text:
        return None, None
    m = re.search(r"(\d+)\s*of\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"\s*(\d+)\s*", text)
    if m:
        return int(m.group(1)), None
    return None, None


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"-?\d+", text)
    return int(m.group()) if m else None


def parse_ctrl(text: str | None) -> int | None:
    """'4:32' -> 272 seconds. '--' -> None."""
    if not text:
        return None
    m = re.match(r"\s*(\d+):(\d{2})\s*", text)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def parse_time_seconds(text: str | None) -> int | None:
    return parse_ctrl(text)


def _id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    return href.rstrip("/").split("/")[-1] or None


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s).strip() if s else ""


def parse_height_to_inches(text: str | None) -> float | None:
    """\"5' 11\\\"\" -> 71.0."""
    if not text:
        return None
    m = re.search(r"(\d+)'\s*(\d+)", text)
    return float(m.group(1)) * 12 + float(m.group(2)) if m else None


def parse_reach_to_inches(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def parse_dob(text: str | None) -> str | None:
    return parse_date(text)


# --------------------------------------------------------------------------- #
# List pages
# --------------------------------------------------------------------------- #

def parse_event_list(html: str) -> list[dict]:
    """Rows of {ufcstats_id, name, date, location} from a completed/upcoming list."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for row in soup.select("tr.b-statistics__table-row"):
        a = row.select_one("a.b-link[href*='event-details']")
        if not a:
            continue
        date_span = row.select_one("span.b-statistics__date")
        loc_cell = row.select("td.b-statistics__table-col")
        location = _clean(loc_cell[-1].get_text()) if len(loc_cell) > 1 else None
        out.append({
            "ufcstats_id": _id_from_href(a.get("href")),
            "name": _clean(a.get_text()),
            "date": parse_date(date_span.get_text() if date_span else None),
            "location": location,
        })
    return out


# --------------------------------------------------------------------------- #
# Event detail page
# --------------------------------------------------------------------------- #

def parse_event_page(html: str) -> dict:
    """Return {name, date, location, fights:[{ufcstats_id, red, blue, weight_class,
    method, ...}]} from an event-details page."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one("h2.b-content__title span.b-content__title-highlight")
    name = _clean(title.get_text()) if title else None

    date = location = None
    for li in soup.select("li.b-list__box-list-item"):
        label = li.select_one("i.b-list__box-item-title")
        if not label:
            continue
        key = _clean(label.get_text()).rstrip(":").lower()
        val = _clean(li.get_text().replace(label.get_text(), ""))
        if key == "date":
            date = parse_date(val)
        elif key == "location":
            location = val

    fights: list[dict] = []
    for pos, row in enumerate(soup.select("tr.b-fight-details__table-row"), start=0):
        link = row.get("data-link") or row.get("onclick") or ""
        m = re.search(r"fight-details/(\w+)", link)
        if not m:
            a = row.select_one("a[href*='fight-details']")
            if not a:
                continue
            fid = _id_from_href(a.get("href"))
        else:
            fid = m.group(1)

        cols = row.select("td.b-fight-details__table-col")
        # Fighter links (2) live in the second column.
        f_links = row.select("a[href*='fighter-details']")
        if len(f_links) < 2:
            continue
        red = {"ufcstats_id": _id_from_href(f_links[0].get("href")),
               "name": _clean(f_links[0].get_text())}
        blue = {"ufcstats_id": _id_from_href(f_links[1].get("href")),
                "name": _clean(f_links[1].get_text())}

        # win flag: a green flag in the first column means red won.
        flag = row.select_one("i.b-flag_style_green, i.b-flag__inner")
        flag_txt = _clean(flag.get_text()).lower() if flag else ""

        def col_text(idx):
            return _clean(cols[idx].get_text(" ")) if idx < len(cols) else ""

        # Column order: 0 W/L flag, 1 fighters, 2 KD, 3 STR, 4 TD, 5 SUB,
        # 6 weight class, 7 method, 8 round, 9 time.
        weight_class = col_text(6)
        method = col_text(7)
        rnd = parse_int(col_text(8))
        time_s = parse_time_seconds(col_text(9))

        fights.append({
            "ufcstats_id": fid, "red": red, "blue": blue,
            "weight_class": weight_class or None, "method_short": method or None,
            "round": rnd, "time_seconds": time_s, "card_position": pos + 1,
            "red_won_flag": flag_txt in ("win", "w"),
        })
    return {"name": name, "date": date, "location": location, "fights": fights}


# --------------------------------------------------------------------------- #
# Fight detail page
# --------------------------------------------------------------------------- #

def _header_cells(table) -> list[str]:
    head = table.select_one("thead tr") or table.select_one("tr")
    return [_clean(th.get_text()) for th in head.select("th, td")] if head else []


def _stat_table_rows(table) -> list[list[str]]:
    """Return per-fighter values: rows[fighter_idx][col_idx] = cell text.
    Each ufcstats data cell holds two <p> (red then blue)."""
    body_row = None
    for tr in table.select("tbody tr"):
        if tr.select("td"):
            body_row = tr
            break
    if body_row is None:
        return []
    per_fighter = [[], []]
    for td in body_row.select("td"):
        ps = td.select("p")
        v0 = _clean(ps[0].get_text()) if len(ps) > 0 else _clean(td.get_text())
        v1 = _clean(ps[1].get_text()) if len(ps) > 1 else ""
        per_fighter[0].append(v0)
        per_fighter[1].append(v1)
    return per_fighter


def parse_fight_page(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    out: dict = {"fighters": [], "judge_scores": None}

    persons = soup.select("div.b-fight-details__person")
    for p in persons:
        name_a = p.select_one("h3.b-fight-details__person-name a, "
                              "h3.b-fight-details__person-name")
        status = p.select_one("i.b-fight-details__person-status")
        a = p.select_one("a[href*='fighter-details']")
        out["fighters"].append({
            "ufcstats_id": _id_from_href(a.get("href")) if a else None,
            "name": _clean(name_a.get_text()) if name_a else None,
            "status": _clean(status.get_text()).upper() if status else None,
        })

    # Title / bout type.
    title_el = soup.select_one("i.b-fight-details__fight-title")
    bout = _clean(title_el.get_text()) if title_el else ""
    out["bout_title"] = bout or None
    low = bout.lower()
    out["is_title"] = 1 if "title" in low else 0
    out["is_interim_title"] = 1 if "interim" in low else 0

    # Method / round / time / time format from the label paragraphs.
    labels = {}
    for item in soup.select("i.b-fight-details__text-item, "
                            "i.b-fight-details__text-item_first"):
        lab = item.select_one("i.b-fight-details__label")
        if not lab:
            continue
        key = _clean(lab.get_text()).rstrip(":").lower()
        val = _clean(item.get_text().replace(lab.get_text(), ""))
        labels[key] = val
    out["method"] = labels.get("method")
    out["round"] = parse_int(labels.get("round"))
    out["time_seconds"] = parse_time_seconds(labels.get("time"))
    out["time_format"] = labels.get("time format")
    out["referee"] = labels.get("referee")
    sr = None
    if labels.get("time format"):
        m = re.search(r"(\d+)\s*Rnd", labels["time format"])
        if m:
            sr = int(m.group(1))
    out["scheduled_rounds"] = sr

    # Details paragraph -> judge scores when a decision.
    details = None
    for para in soup.select("p.b-fight-details__text"):
        txt = _clean(para.get_text(" "))
        if txt.lower().startswith("details"):
            details = re.sub(r"(?i)^details:?", "", txt).strip()
            break
    if details:
        judges = re.findall(r"([A-Z][A-Za-z.'\- ]+?)\s+(\d+)\s*-\s*(\d+)", details)
        if judges:
            out["judge_scores"] = [
                {"judge": _clean(j), "a": int(a), "b": int(b)} for j, a, b in judges
            ]
        out["method_detail"] = details if not out.get("judge_scores") else None
    else:
        out["method_detail"] = None

    # Stat tables. Identify the overall Totals table and Significant-Strikes table
    # by their headers; take the FIRST of each (per-round tables come after).
    totals_vals = sig_vals = None
    for table in soup.select("table"):
        headers = [h.lower() for h in _header_cells(table)]
        if not headers:
            continue
        if totals_vals is None and any("ctrl" in h for h in headers):
            rows = _stat_table_rows(table)
            if rows and rows[0]:
                totals_vals = (headers, rows)
        if sig_vals is None and any(h == "head" for h in headers) and \
                any("distance" in h for h in headers):
            rows = _stat_table_rows(table)
            if rows and rows[0]:
                sig_vals = (headers, rows)

    out["stats"] = _assemble_stats(totals_vals, sig_vals)
    return out


def _col(headers: list[str], values: list[str], *needles: str) -> str | None:
    for i, h in enumerate(headers):
        if all(n in h for n in needles):
            return values[i] if i < len(values) else None
    return None


def _assemble_stats(totals, sig) -> list[dict]:
    """Return per-fighter stats dicts (index 0 red/first person, 1 blue/second)."""
    result = [dict(), dict()]
    if totals:
        headers, rows = totals
        for fi in range(min(2, len(rows))):
            v = rows[fi]
            s = result[fi]
            s["knockdowns"] = parse_int(_col(headers, v, "kd"))
            sl, sa = parse_of(_col(headers, v, "sig", "str") if
                              _col(headers, v, "sig. str.") is None else
                              _col(headers, v, "sig. str."))
            # More robust: the "sig. str." landed-of-attempted column.
            sl2, sa2 = parse_of(_col(headers, v, "sig", "str"))
            s["sig_strikes_landed"] = sl if sl is not None else sl2
            s["sig_strikes_attempted"] = sa if sa is not None else sa2
            tl, ta = parse_of(_col(headers, v, "total", "str"))
            s["total_strikes_landed"] = tl
            s["total_strikes_attempted"] = ta
            tdl, tda = parse_of(_col(headers, v, "td") if
                                _col(headers, v, "td") and "%" not in
                                (_col(headers, v, "td") or "") else _col(headers, v, "td"))
            s["takedowns_landed"] = tdl
            s["takedowns_attempted"] = tda
            s["sub_attempts"] = parse_int(_col(headers, v, "sub"))
            s["reversals"] = parse_int(_col(headers, v, "rev"))
            s["control_time_seconds"] = parse_ctrl(_col(headers, v, "ctrl"))
    if sig:
        headers, rows = sig
        for fi in range(min(2, len(rows))):
            v = rows[fi]
            s = result[fi]
            s["sig_head_landed"] = parse_of(_col(headers, v, "head"))[0]
            s["sig_body_landed"] = parse_of(_col(headers, v, "body"))[0]
            s["sig_leg_landed"] = parse_of(_col(headers, v, "leg"))[0]
            s["sig_distance_landed"] = parse_of(_col(headers, v, "distance"))[0]
            s["sig_clinch_landed"] = parse_of(_col(headers, v, "clinch"))[0]
            s["sig_ground_landed"] = parse_of(_col(headers, v, "ground"))[0]
    return result


# --------------------------------------------------------------------------- #
# Fighter detail page (physicals)
# --------------------------------------------------------------------------- #

def parse_fighter_page(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    name_el = soup.select_one("span.b-content__title-highlight")
    out["name"] = _clean(name_el.get_text()) if name_el else None
    nick = soup.select_one("p.b-content__Nickname")
    out["nickname"] = _clean(nick.get_text()) if nick else None
    for li in soup.select("li.b-list__box-list-item"):
        title = li.select_one("i.b-list__box-item-title")
        if not title:
            continue
        key = _clean(title.get_text()).rstrip(":").lower()
        val = _clean(li.get_text().replace(title.get_text(), ""))
        if key == "height":
            out["height_in"] = parse_height_to_inches(val)
        elif key == "reach":
            out["reach_in"] = parse_reach_to_inches(val)
        elif key == "stance":
            out["stance"] = val or None
        elif key == "dob":
            out["dob"] = parse_dob(val)
    return out


# --------------------------------------------------------------------------- #
# Result normalization
# --------------------------------------------------------------------------- #

def normalize_method(method: str | None) -> tuple[str | None, str | None]:
    """('Decision - Unanimous') -> ('U-DEC', None); ('KO/TKO', 'Punches') passthrough.
    Returns (method_code, method_detail_hint)."""
    if not method:
        return None, None
    m = method.strip()
    low = m.lower()
    if "decision" in low and "unanim" in low:
        return "U-DEC", None
    if "decision" in low and "split" in low:
        return "S-DEC", None
    if "decision" in low and "major" in low:
        return "M-DEC", None
    if "ko/tko" in low or low.startswith("ko") or "tko" in low:
        return "KO/TKO", None
    if "submission" in low or low == "sub":
        return "SUB", None
    if low.startswith("dq") or "disqualification" in low:
        return "DQ", None
    if low.startswith("nc") or "no contest" in low:
        return "NC", None
    return m, None


# --------------------------------------------------------------------------- #
# Sync driver
# --------------------------------------------------------------------------- #

def _persist_event(conn, ev_meta: dict, page: dict, status: str, source_note: str,
                   stats_limit: int | None = None) -> dict:
    """Insert/reconcile an event + its fights (+ fight_stats for completed)."""
    counts = {"events": 0, "fights": 0, "fight_stats": 0}
    eid = base.get_or_create_event(
        conn, page.get("name") or ev_meta.get("name"),
        page.get("date") or ev_meta.get("date"), SOURCE,
        ufcstats_id=ev_meta.get("ufcstats_id"),
        fields={"location": page.get("location") or ev_meta.get("location"),
                "status": status},
    )
    counts["events"] = 1

    for f in page.get("fights", []):
        red_id = base.get_or_create_fighter(conn, f["red"]["name"], SOURCE,
                                            ufcstats_id=f["red"].get("ufcstats_id"))
        blue_id = base.get_or_create_fighter(conn, f["blue"]["name"], SOURCE,
                                             ufcstats_id=f["blue"].get("ufcstats_id"))
        fight_fields: dict = {
            "weight_class": f.get("weight_class"),
            "card_position": f.get("card_position"),
        }
        detail = None
        if status == "upcoming":
            fight_fields["result_original"] = "upcoming"
            fight_fields["result_current"] = "upcoming"
        else:
            # Fetch fight-detail page for method/stats/flags.
            try:
                fhtml = base.fetch(
                    f"http://ufcstats.com/fight-details/{f['ufcstats_id']}")
                detail = parse_fight_page(fhtml)
            except base.ChallengeError:
                raise
            except Exception as e:  # noqa: BLE001
                detail = None
                fight_fields.setdefault("_note", str(e))

        if detail:
            code, _ = normalize_method(detail.get("method") or f.get("method_short"))
            fight_fields["method"] = code
            fight_fields["method_detail"] = detail.get("method_detail")
            fight_fields["round"] = detail.get("round") or f.get("round")
            fight_fields["time_seconds"] = detail.get("time_seconds") or f.get("time_seconds")
            fight_fields["scheduled_rounds"] = detail.get("scheduled_rounds")
            fight_fields["is_title"] = detail.get("is_title")
            fight_fields["is_interim_title"] = detail.get("is_interim_title")
            if detail.get("judge_scores"):
                import json
                fight_fields["judge_scores"] = json.dumps(detail["judge_scores"])
            # Result from person statuses.
            statuses = [p.get("status") for p in detail.get("fighters", [])]
            res = _result_from_statuses(statuses)
            fight_fields["result_original"] = res
            fight_fields["result_current"] = res

        fid = base.get_or_create_fight(conn, eid, red_id, blue_id, SOURCE,
                                       ufcstats_id=f.get("ufcstats_id"),
                                       fields={k: v for k, v in fight_fields.items()
                                               if not k.startswith("_")})
        counts["fights"] += 1

        # winner_id resolution.
        if detail:
            winner = _winner_from_detail(detail, red_id, blue_id)
            if winner is not None:
                base.apply_field(conn, "fights", fid, "winner_id", winner, SOURCE,
                                 existing=dict(conn.execute(
                                     "SELECT * FROM fights WHERE id=?", (fid,)).fetchone()))
            # Stats: person 0 -> whichever fighter; map by ufcstats_id/name.
            fighter_ids = _map_stat_fighters(detail, red_id, blue_id,
                                             f["red"], f["blue"])
            for idx, fighter_id in enumerate(fighter_ids):
                st = detail["stats"][idx] if idx < len(detail.get("stats", [])) else None
                if fighter_id and st:
                    base.upsert_fight_stats(conn, fid, fighter_id, st)
                    counts["fight_stats"] += 1
        base.commit_with_retry(conn)
    return counts


def _result_from_statuses(statuses: list) -> str:
    s = [x for x in statuses if x]
    if not s:
        return "win"
    if any(x == "W" for x in s):
        return "win"
    if all(x == "D" for x in s):
        return "draw"
    if any("NC" in (x or "") for x in s):
        return "nc"
    return "win"


def _winner_from_detail(detail: dict, red_id: int, blue_id: int):
    fs = detail.get("fighters", [])
    for i, p in enumerate(fs):
        if p.get("status") == "W":
            # person i is winner; map to red/blue by order (person0->red typical)
            return red_id if i == 0 else blue_id
    return None


def _map_stat_fighters(detail, red_id, blue_id, red_meta, blue_meta) -> list:
    """Person order in the stats tables matches the persons block. Map by
    ufcstats_id when available, else assume person0->red, person1->blue."""
    persons = detail.get("fighters", [])
    out = []
    for i in range(2):
        if i < len(persons) and persons[i].get("ufcstats_id"):
            pid = persons[i]["ufcstats_id"]
            if pid == red_meta.get("ufcstats_id"):
                out.append(red_id)
            elif pid == blue_meta.get("ufcstats_id"):
                out.append(blue_id)
            else:
                out.append(red_id if i == 0 else blue_id)
        else:
            out.append(red_id if i == 0 else blue_id)
    return out


def sync(conn=None, *, max_completed: int | None = 2, do_upcoming: bool = True,
         fighter_physicals: bool = True) -> dict:
    """Incremental ufcstats sync. `max_completed` limits how many of the newest
    completed events to pull (None = all new since watermark). Returns a summary."""
    owns = conn is None
    if conn is None:
        conn = base.connect()
    summary = {"source": SOURCE, "completed": 0, "upcoming": 0,
               "fights": 0, "fight_stats": 0, "errors": [], "blocked": False}

    watermark = base.get_watermark(conn, SOURCE)  # last processed event date
    try:
        completed_html = base.fetch(COMPLETED_URL)
    except base.ChallengeError as e:
        summary["blocked"] = True
        summary["errors"].append(str(e))
        if owns:
            conn.close()
        return summary
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(f"completed list: {e}")
        if owns:
            conn.close()
        return summary

    events = parse_event_list(completed_html)
    # Newest first. Drop the top row if it is actually the next/upcoming event
    # (some snapshots include a highlighted upcoming row here).
    new_events = [e for e in events if e.get("date") and
                  (watermark is None or e["date"] > watermark)]
    new_events.sort(key=lambda e: e["date"], reverse=True)
    if max_completed is not None:
        new_events = new_events[:max_completed]

    newest_seen = watermark
    for ev in new_events:
        try:
            html = base.fetch(f"http://ufcstats.com/event-details/{ev['ufcstats_id']}")
            page = parse_event_page(html)
            c = _persist_event(conn, ev, page, "completed", "completed")
            summary["completed"] += 1
            summary["fights"] += c["fights"]
            summary["fight_stats"] += c["fight_stats"]
            if ev["date"] and (newest_seen is None or ev["date"] > newest_seen):
                newest_seen = ev["date"]
        except base.ChallengeError as e:
            summary["blocked"] = True
            summary["errors"].append(str(e))
            break
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"event {ev.get('ufcstats_id')}: {e}")

    if newest_seen and not summary["blocked"]:
        base.set_watermark(conn, SOURCE, newest_seen,
                           note=f"through {newest_seen}")
        base.commit_with_retry(conn)

    if do_upcoming and not summary["blocked"]:
        try:
            up_html = base.fetch(UPCOMING_URL)
            for ev in parse_event_list(up_html):
                try:
                    html = base.fetch(
                        f"http://ufcstats.com/event-details/{ev['ufcstats_id']}")
                    page = parse_event_page(html)
                    c = _persist_event(conn, ev, page, "upcoming", "upcoming")
                    summary["upcoming"] += 1
                    summary["fights"] += c["fights"]
                except base.ChallengeError as e:
                    summary["blocked"] = True
                    summary["errors"].append(str(e))
                    break
                except Exception as e:  # noqa: BLE001
                    summary["errors"].append(f"upcoming {ev.get('ufcstats_id')}: {e}")
        except base.ChallengeError as e:
            summary["blocked"] = True
            summary["errors"].append(str(e))
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"upcoming list: {e}")

    base.commit_with_retry(conn)
    if owns:
        conn.close()
    return summary


def sync_fighter_physicals(conn, ufcstats_id: str) -> bool:
    """Fetch one fighter-detail page and reconcile physicals. Returns success."""
    try:
        html = base.fetch(f"http://ufcstats.com/fighter-details/{ufcstats_id}")
    except Exception:
        return False
    data = parse_fighter_page(html)
    fid = base.get_or_create_fighter(conn, data.get("name"), SOURCE,
                                     ufcstats_id=ufcstats_id,
                                     fields={k: v for k, v in data.items()
                                             if k in ("nickname", "height_in",
                                                      "reach_in", "stance", "dob")})
    base.commit_with_retry(conn)
    return bool(fid)


if __name__ == "__main__":
    print(sync())
