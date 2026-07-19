"""Fighter style classification from career fight statistics.

PLAN section 4: classify every fighter's fighting style from their career
``fight_stats`` and write it to ``fighters.style_tag``.  The prediction layer
(:mod:`elo.features`) turns the resulting labels into a style-matchup feature.

Method
------
For each fighter we aggregate their whole career into six rate signals:

  * ``sig_str_min``   -- significant strikes landed per minute (striking volume)
  * ``td_att_15``     -- takedown *attempts* per 15 minutes (wrestling volume)
  * ``td_acc``        -- takedown accuracy (landed / attempted)
  * ``sub_att_15``    -- submission attempts per 15 minutes (grappling volume)
  * ``control_pct``   -- share of fight time spent in top control
  * ``ko_share`` / ``sub_share`` -- fraction of the fighter's *wins* by KO/TKO
                          resp. submission (finishing identity)

Small samples are noisy, so each rate is **shrunk toward its division mean**
with weight ``n / (n + SHRINK_K)`` (PLAN's ``n/(n+8)``): a fighter with few
fights is pulled toward the division-typical value and therefore lands as
``balanced`` rather than being mislabelled by one wild outing.

The shrunk rates are standardized (z-scored) across the roster and combined into
three archetype indices -- striking, wrestling, grappling -- whose ``argmax``
picks the label, unless no index clears ``STYLE_MARGIN`` above the mean (then
``balanced``).  Fighters below ``MIN_FIGHTS_FOR_STYLE`` rated fights, or with no
usable stats, are ``insufficient_data``.

Run:  python -m elo.styles          # classify everyone and write style_tag
      python -m elo.styles --dry-run # print spot checks, write nothing
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.db import get_conn  # noqa: E402

# ---------------------------------------------------------------------------
# Tunable classification constants (documented; see module docstring)
# ---------------------------------------------------------------------------

#: Shrinkage pseudo-count.  shrink_weight = n / (n + SHRINK_K).  PLAN: n/(n+8).
SHRINK_K: float = 8.0

#: Below this many rated fights a fighter is 'insufficient_data' (the confidence
#: floor).  Two fights is too few to distinguish a style from noise even after
#: shrinkage; three is the documented minimum.
MIN_FIGHTS_FOR_STYLE: int = 3

#: A style index must exceed the fighter's own mean index by at least this many
#: standardized units to claim a specialization; otherwise -> 'balanced'.
STYLE_MARGIN: float = 0.55

#: UFC rounds are five minutes.  Used to convert (round, time) into minutes.
SECONDS_PER_ROUND: int = 300

STYLE_STRIKER = "striker"
STYLE_WRESTLER = "wrestler"
STYLE_GRAPPLER = "grappler"
STYLE_BALANCED = "balanced"
STYLE_INSUFFICIENT = "insufficient_data"


@dataclass
class StyleRates:
    """A fighter's career rate signals (pre-shrinkage) plus fight count."""

    fighter_id: int
    division: str
    n: int
    sig_str_min: float
    td_att_15: float
    td_acc: float
    sub_att_15: float
    control_pct: float
    ko_share: float
    sub_share: float


# ---------------------------------------------------------------------------
# Data loading / aggregation
# ---------------------------------------------------------------------------
_RATE_COLS = ("sig_str_min", "td_att_15", "td_acc",
              "sub_att_15", "control_pct", "ko_share", "sub_share")


def _home_divisions(conn: sqlite3.Connection) -> dict[int, str]:
    """fighter_id -> primary division (the rated pool with the most fights)."""
    rows = conn.execute(
        "SELECT fighter_id, division, n_fights FROM ratings_current"
    ).fetchall()
    best: dict[int, tuple[int, str]] = {}
    for r in rows:
        fid, div, n = r["fighter_id"], r["division"], r["n_fights"] or 0
        if fid not in best or n > best[fid][0]:
            best[fid] = (n, div)
    return {fid: div for fid, (n, div) in best.items()}


def load_rates(conn: sqlite3.Connection) -> list[StyleRates]:
    """Aggregate every fighter's career into raw rate signals."""
    home = _home_divisions(conn)

    # Per-fight stat lines joined with fight length + who won by what method.
    stat_rows = conn.execute(
        """
        SELECT fs.fighter_id, fl.round, fl.time_seconds,
               fs.sig_strikes_landed, fs.takedowns_landed, fs.takedowns_attempted,
               fs.sub_attempts, fs.control_time_seconds,
               fl.winner_id, fl.method
        FROM fight_stats fs JOIN fights fl ON fs.fight_id = fl.id
        WHERE fl.result_current != 'upcoming'
        """
    ).fetchall()

    agg: dict[int, dict] = {}
    for r in stat_rows:
        fid = r["fighter_id"]
        a = agg.setdefault(fid, dict(
            n=0, secs=0, sig=0, tdl=0, tda=0, sub=0, ctrl=0,
            wins=0, ko_wins=0, sub_wins=0))
        rnd = r["round"] or 1
        dur = (rnd - 1) * SECONDS_PER_ROUND + (r["time_seconds"] or 0)
        a["n"] += 1
        a["secs"] += max(dur, 1)
        a["sig"] += r["sig_strikes_landed"] or 0
        a["tdl"] += r["takedowns_landed"] or 0
        a["tda"] += r["takedowns_attempted"] or 0
        a["sub"] += r["sub_attempts"] or 0
        a["ctrl"] += r["control_time_seconds"] or 0
        if r["winner_id"] == fid:
            a["wins"] += 1
            if r["method"] == "KO/TKO":
                a["ko_wins"] += 1
            elif r["method"] == "SUB":
                a["sub_wins"] += 1

    out: list[StyleRates] = []
    for fid, a in agg.items():
        div = home.get(fid)
        if div is None:
            continue  # no rated pool -> handled as insufficient_data downstream
        mins = a["secs"] / 60.0
        wins = a["wins"]
        out.append(StyleRates(
            fighter_id=fid,
            division=div,
            n=a["n"],
            sig_str_min=a["sig"] / mins,
            td_att_15=a["tda"] / mins * 15.0,
            td_acc=(a["tdl"] / a["tda"]) if a["tda"] else 0.0,
            sub_att_15=a["sub"] / mins * 15.0,
            control_pct=a["ctrl"] / a["secs"],
            ko_share=(a["ko_wins"] / wins) if wins else 0.0,
            sub_share=(a["sub_wins"] / wins) if wins else 0.0,
        ))
    return out


# ---------------------------------------------------------------------------
# Shrinkage + classification
# ---------------------------------------------------------------------------
def classify(rates: list[StyleRates]) -> dict[int, str]:
    """Return fighter_id -> style label for every fighter in ``rates``.

    Rates are shrunk toward their division mean by ``n/(n+SHRINK_K)``, z-scored
    across the roster, then combined into striking / wrestling / grappling
    indices whose argmax (subject to ``STYLE_MARGIN``) is the label.
    """
    if not rates:
        return {}
    df = pd.DataFrame([r.__dict__ for r in rates])

    # Division means for shrinkage (simple per-division average of each rate).
    div_mean = df.groupby("division")[list(_RATE_COLS)].transform("mean")
    w = (df["n"] / (df["n"] + SHRINK_K)).to_numpy()[:, None]
    shrunk = df[list(_RATE_COLS)].to_numpy() * w + div_mean.to_numpy() * (1.0 - w)
    shrunk = pd.DataFrame(shrunk, columns=list(_RATE_COLS), index=df.index)

    # Standardize each shrunk rate across the roster (z-scores).
    z = (shrunk - shrunk.mean()) / shrunk.std(ddof=0).replace(0, 1.0)

    # Archetype indices.  Wrestling = takedown volume+accuracy+control; grappling
    # = submission volume + finishing by submission; striking = strike volume +
    # KO finishing.  Control feeds both grappling styles but with less weight on
    # grappling so a control-heavy takedown artist reads as a wrestler.
    striking = z["sig_str_min"] + z["ko_share"]
    wrestling = z["td_att_15"] + z["td_acc"] + z["control_pct"]
    grappling = z["sub_att_15"] + 1.5 * z["sub_share"] + z["control_pct"]

    idx = pd.DataFrame({
        STYLE_STRIKER: striking,
        STYLE_WRESTLER: wrestling,
        STYLE_GRAPPLER: grappling,
    })
    labels: dict[int, str] = {}
    for i, fid in enumerate(df["fighter_id"]):
        n = int(df["n"].iloc[i])
        if n < MIN_FIGHTS_FOR_STYLE:
            labels[int(fid)] = STYLE_INSUFFICIENT
            continue
        row = idx.iloc[i]
        top = row.idxmax()
        # 'balanced' if the leading archetype doesn't stand out from the others.
        if row[top] - row.mean() < STYLE_MARGIN:
            labels[int(fid)] = STYLE_BALANCED
        else:
            labels[int(fid)] = top
    return labels


def write_style_tags(conn: sqlite3.Connection, labels: dict[int, str]) -> int:
    """Persist labels to ``fighters.style_tag``.

    Every fighter is set: those absent from ``labels`` (no rated fights / no
    stats at all) become ``insufficient_data``.
    """
    all_ids = [r[0] for r in conn.execute("SELECT id FROM fighters").fetchall()]
    payload = [(labels.get(fid, STYLE_INSUFFICIENT), fid) for fid in all_ids]
    conn.executemany("UPDATE fighters SET style_tag = ? WHERE id = ?", payload)
    conn.commit()
    return len(payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
_SPOT_CHECKS = {
    "Merab Dvalishvili": STYLE_WRESTLER,
    "Charles Oliveira": STYLE_GRAPPLER,
    "Max Holloway": STYLE_STRIKER,
    "Khabib Nurmagomedov": STYLE_WRESTLER,
    "Israel Adesanya": STYLE_STRIKER,
    "Demian Maia": STYLE_GRAPPLER,
}


def spot_check(conn: sqlite3.Connection, labels: dict[int, str]) -> list[tuple]:
    """Return (name, expected, got, ok) rows for the named sanity fighters."""
    out = []
    for name, expected in _SPOT_CHECKS.items():
        row = conn.execute(
            "SELECT id FROM fighters WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            out.append((name, expected, "MISSING", False))
            continue
        got = labels.get(row[0], STYLE_INSUFFICIENT)
        out.append((name, expected, got, got == expected))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify fighter styles.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print distribution + spot checks, write nothing.")
    args = ap.parse_args()

    conn = get_conn()
    try:
        rates = load_rates(conn)
        labels = classify(rates)

        from collections import Counter
        dist = Counter(labels.values())
        print("Style distribution (fighters with stats):")
        for style, cnt in dist.most_common():
            print(f"  {style:<18} {cnt}")

        print("\nSpot checks:")
        for name, expected, got, ok in spot_check(conn, labels):
            mark = "OK " if ok else "XX "
            print(f"  {mark}{name:<24} expected {expected:<10} got {got}")

        if not args.dry_run:
            n = write_style_tags(conn, labels)
            print(f"\nWrote style_tag for {n} fighters.")
        else:
            print("\n(dry run -- no writes)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
