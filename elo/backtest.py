"""Walk-forward backtesting harness (PLAN section 5).

Replays history and scores pre-fight predictions from

  * (a) the raw Glicko expected score, and
  * (b) the full prediction layer (:mod:`elo.predict`, refit walk-forward),

against three baselines:

  * **coin flip** -- constant 0.5,
  * **vanilla Elo** -- plain K=32, single global pool, no MOV/stakes (implemented
    minimally right here), and
  * **betting odds** -- reported *skipped* while the ``odds`` table is empty.

Metrics: log-loss, Brier, accuracy, plus a 10-bin calibration table (and a PNG
if matplotlib is available).  Finally an **ablation table**: the rating system is
re-replayed in memory with each documented multiplier toggled off, and the raw
Glicko walk-forward log-loss is reported for each variant, so a component that
doesn't earn its keep can be cut from ``elo/config.py``.

Everything is evaluated on the natural (red-corner = A) orientation from a
documented burn-in (``--start-year``, default 2013) onward: the pre-2013 roster
is still forming.  Vanilla Elo and raw Glicko still *train* on all earlier bouts.

Run:  python -m elo.backtest                 # full run + report to stdout
      python -m elo.backtest --no-ablation   # skip the (slower) ablation
      python -m elo.backtest --report docs/backtest_report.md
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
from elo import config  # noqa: E402
from elo.engine import RatingEngine, load_fights  # noqa: E402
from elo.features import (  # noqa: E402
    FEATURE_NAMES,
    build_training_frame,
    glicko_expected,
    sigmoid,
)
from elo.predict import (  # noqa: E402
    WALK_FORWARD_START_YEAR,
    walk_forward_predictions,
)
from elo.recompute import load_offsets  # noqa: E402

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    # p == 0.5 counts as half-right (coin flip has no edge).
    correct = np.where(p == 0.5, 0.5, (p > 0.5) == (y == 1))
    return float(np.mean(correct))


@dataclass
class Metrics:
    name: str
    n: int
    logloss: float
    brier: float
    acc: float

    def row(self) -> str:
        return (f"| {self.name} | {self.n} | {self.logloss:.4f} | "
                f"{self.brier:.4f} | {self.acc:.4f} |")


def score(name: str, p: np.ndarray, y: np.ndarray) -> Metrics:
    return Metrics(name, len(y), log_loss(p, y), brier(p, y), accuracy(p, y))


# ---------------------------------------------------------------------------
# Vanilla Elo baseline (plain K=32, single pool, no multipliers)
# ---------------------------------------------------------------------------
def vanilla_elo_predictions(conn: sqlite3.Connection, k: float = 32.0,
                            base: float = 1500.0, scale: float = 400.0
                            ) -> dict[int, float]:
    """Return fight_id -> pre-fight P(red) under a textbook Elo replay."""
    fights = load_fights(conn)  # chronological
    rating: dict[int, float] = {}
    preds: dict[int, float] = {}
    for f in fights:
        if f.result not in ("win", "draw", "dq"):
            continue
        ra = rating.get(f.red_id, base)
        rb = rating.get(f.blue_id, base)
        exp_red = 1.0 / (1.0 + 10 ** ((rb - ra) / scale))
        preds[f.id] = exp_red
        if f.result == "draw":
            s_red = 0.5
        else:
            s_red = 1.0 if f.winner_id == f.red_id else 0.0
        rating[f.red_id] = ra + k * (s_red - exp_red)
        rating[f.blue_id] = rb + k * ((1.0 - s_red) - (1.0 - exp_red))
    return preds


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 10
                      ) -> pd.DataFrame:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        n = int(m.sum())
        rows.append({
            "bin": f"{edges[b]:.1f}-{edges[b+1]:.1f}",
            "n": n,
            "pred_mean": float(p[m].mean()) if n else float("nan"),
            "actual": float(y[m].mean()) if n else float("nan"),
        })
    return pd.DataFrame(rows)


def calibration_error(tbl: pd.DataFrame) -> float:
    """Sample-weighted mean |pred - actual| across populated bins (ECE)."""
    d = tbl.dropna()
    if d["n"].sum() == 0:
        return float("nan")
    return float((d["n"] * (d["pred_mean"] - d["actual"]).abs()).sum() / d["n"].sum())


def save_calibration_plot(tbl: pd.DataFrame, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    d = tbl.dropna()
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfect")
    ax.plot(d["pred_mean"], d["actual"], "o-", color="#d20a0a",
            label="prediction layer")
    for _, r in d.iterrows():
        ax.annotate(int(r["n"]), (r["pred_mean"], r["actual"]),
                    fontsize=7, xytext=(3, -8), textcoords="offset points")
    ax.set_xlabel("Predicted P(red win)")
    ax.set_ylabel("Actual win rate")
    ax.set_title("Prediction-layer calibration (walk-forward)")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# Ablations: re-replay ratings in memory with a multiplier toggled off
# ---------------------------------------------------------------------------
ABLATIONS: dict[str, dict] = {
    "shipped": {},
    "mov_base_off": {  # flat M_base: every method/round weighted 1.0
        "M_BASE_FINISH_BY_ROUND": {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0},
        "M_BASE_UNANIMOUS_DECISION": 1.0,
        "M_BASE_SPLIT_MAJORITY_DECISION": 1.0,
        "M_BASE_DQ": 1.0,
        "M_BASE_DRAW": 1.0,
    },
    "mov_correction_off": {  # 538 autocorrelation correction -> ~1.0
        "MOV_CORRECTION_K": 1e12,
    },
    "stakes_off": {  # every bout weighted as a normal fight
        "STAKES_UNDISPUTED_TITLE": 1.0,
        "STAKES_INTERIM_TITLE": 1.0,
        "STAKES_MAIN_EVENT": 1.0,
        "STAKES_CO_MAIN": 1.0,
    },
    "delta_cap_off": {  # no hard cap on the per-bout rating change
        "MAX_RATING_DELTA": 1e9,
    },
}


def _replay_glicko_logloss(conn: sqlite3.Connection, overrides: dict,
                           offsets: dict[str, float],
                           start_year: int) -> tuple[float, float]:
    """Replay ratings with ``overrides`` applied to config; return raw-Glicko
    (log-loss, accuracy) on decisive red-oriented fights from ``start_year``."""
    saved = {k: getattr(config, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(config, k, v)
        fights = load_fights(conn)
        eng = RatingEngine(offsets=offsets)
        eng.run(fights)
    finally:
        for k, v in saved.items():
            setattr(config, k, v)

    # Pre-fight (mu, rd, division) per (fight, fighter) from the in-memory replay.
    per: dict[int, dict[int, tuple[float, float, str]]] = {}
    for h in eng.history:
        per.setdefault(h.fight_id, {})[h.fighter_id] = (h.mu_pre, h.rd_pre, h.division)

    meta = pd.read_sql_query(
        """
        SELECT f.id, substr(e.date,1,4) AS yr, f.fighter_red_id AS red,
               f.fighter_blue_id AS blue, f.winner_id, f.result_current
        FROM fights f JOIN events e ON f.event_id = e.id
        WHERE f.result_current IN ('win','dq') AND e.date IS NOT NULL
        """, conn)
    ps, ys = [], []
    for _, r in meta.iterrows():
        if int(r["yr"]) < start_year:
            continue
        rows = per.get(int(r["id"]))
        if not rows or int(r["red"]) not in rows or int(r["blue"]) not in rows:
            continue
        mr, rdr, dr = rows[int(r["red"])]
        mb, rdb, db = rows[int(r["blue"])]
        ps.append(glicko_expected(mr, rdr, offsets.get(dr, 0.0),
                                  mb, rdb, offsets.get(db, 0.0)))
        ys.append(1.0 if int(r["winner_id"]) == int(r["red"]) else 0.0)
    p, y = np.array(ps), np.array(ys)
    return log_loss(p, y), accuracy(p, y)


def run_ablations(conn: sqlite3.Connection, offsets: dict[str, float],
                  start_year: int) -> pd.DataFrame:
    rows = []
    for name, ov in ABLATIONS.items():
        ll, acc = _replay_glicko_logloss(conn, ov, offsets, start_year)
        rows.append({"variant": name, "raw_glicko_logloss": ll,
                     "raw_glicko_acc": acc})
    df = pd.DataFrame(rows)
    base = df.loc[df["variant"] == "shipped", "raw_glicko_logloss"].iloc[0]
    df["delta_vs_shipped"] = df["raw_glicko_logloss"] - base
    return df


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_backtest(start_year: int = WALK_FORWARD_START_YEAR,
                 do_ablation: bool = True) -> dict:
    conn = get_conn()
    try:
        offsets = load_offsets()
        df = build_training_frame(conn, offsets)

        # Prediction layer, walk-forward (corner-aware, A = red).
        wf = walk_forward_predictions(df, start_year=start_year)
        y = wf["y"].to_numpy(float)
        p_layer = wf["p_pred"].to_numpy(float)
        p_raw = wf["glicko_logit"].apply(sigmoid).to_numpy(float)

        # Baselines aligned to the same evaluated fights.
        elo_map = vanilla_elo_predictions(conn)
        p_elo = wf["fight_id"].map(elo_map).to_numpy(float)
        p_coin = np.full_like(y, 0.5)

        results = [
            score("Coin flip", p_coin, y),
            score("Vanilla Elo (K=32)", p_elo, y),
            score("Raw Glicko expected", p_raw, y),
            score("Prediction layer", p_layer, y),
        ]

        cal = calibration_table(p_layer, y)
        ece = calibration_error(cal)
        plot_ok = save_calibration_plot(cal, DOCS_DIR / "calibration.png")

        ablation = run_ablations(conn, offsets, start_year) if do_ablation else None

        return {
            "start_year": start_year,
            "n_eval": len(y),
            "results": results,
            "calibration": cal,
            "ece": ece,
            "plot_ok": plot_ok,
            "ablation": ablation,
            "odds_skipped": conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0] == 0,
        }
    finally:
        conn.close()


def format_report(bt: dict) -> str:
    lines: list[str] = []
    lines.append("### Headline metrics (walk-forward, "
                 f"{bt['start_year']}+, n={bt['n_eval']})\n")
    lines.append("| Model | n | Log-loss | Brier | Accuracy |")
    lines.append("|---|--:|--:|--:|--:|")
    for m in bt["results"]:
        lines.append(m.row())
    if bt["odds_skipped"]:
        lines.append("| Betting odds | - | skipped -- no odds rows | | |")

    lines.append("\n### Calibration (prediction layer, 10 bins)\n")
    lines.append(f"Expected calibration error (ECE): **{bt['ece']:.4f}**\n")
    lines.append("| Bin | n | Pred mean | Actual |")
    lines.append("|---|--:|--:|--:|")
    for _, r in bt["calibration"].iterrows():
        if r["n"] == 0:
            continue
        lines.append(f"| {r['bin']} | {int(r['n'])} | {r['pred_mean']:.3f} | "
                     f"{r['actual']:.3f} |")
    if bt["plot_ok"]:
        lines.append("\n![calibration](calibration.png)")

    if bt["ablation"] is not None:
        lines.append("\n### Ablation (raw-Glicko walk-forward log-loss)\n")
        lines.append("| Variant | Log-loss | Accuracy | delta vs shipped |")
        lines.append("|---|--:|--:|--:|")
        for _, r in bt["ablation"].iterrows():
            lines.append(f"| {r['variant']} | {r['raw_glicko_logloss']:.4f} | "
                         f"{r['raw_glicko_acc']:.4f} | {r['delta_vs_shipped']:+.4f} |")
        lines.append("\n_A positive delta means turning that component **off** hurts "
                     "(component earns its keep); negative means it should be cut._")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest harness.")
    ap.add_argument("--start-year", type=int, default=WALK_FORWARD_START_YEAR)
    ap.add_argument("--no-ablation", action="store_true")
    ap.add_argument("--report", type=str, default=None,
                    help="Write the markdown metrics block to this path too.")
    args = ap.parse_args()

    bt = run_backtest(start_year=args.start_year, do_ablation=not args.no_ablation)
    report = format_report(bt)
    print(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"(metrics block written to {args.report})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
