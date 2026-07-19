"""Fit the cross-division offset table D_w and write division_offsets.json.

PLAN section 3: the pound-for-pound offset D_w for each division is fitted
(regularized) from division-switchers and catchweight/open-weight bouts by
minimizing prediction error on crossover fights, with one division anchored to
0 (Lightweight).  ``PFP rating = division mu + D_w``.

Method
------
1. Replay history with the current offsets set to 0 (:mod:`elo.engine`), which
   yields, for every fight, each fighter's *pre-fight* rating and the pool it
   was rated in.
2. Keep only **crossover** fights: those where the two fighters were rated in
   different pools (all catchweight / open-weight / cross-division bouts).
3. Fit an offset per division by regularized logistic regression:

       P(red wins) = sigmoid( ((mu_r + D[pool_r]) - (mu_b + D[pool_b])) / S )

   S is the Glicko display scale (173.7178).  Lightweight is pinned to 0 and an
   L2 penalty shrinks the remaining offsets toward 0, so divisions with little
   or no crossover data end up ~0 rather than over-fit.  Draws contribute a
   score of 0.5.

The crossover graph in the current DB is dominated by 1990s open-weight bouts
and is sparse for modern adjacent divisions, so most offsets are expected to be
small.  That sparsity is real and documented; regularization keeps it honest.

Run:  python -m elo.calibrate_offsets
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.db import get_conn  # noqa: E402
from elo import config  # noqa: E402
from elo.divisions import CANONICAL_WEIGHT_ORDER  # noqa: E402
from elo.engine import RatingEngine, load_fights  # noqa: E402
from elo.glicko2 import SCALE  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / config.OFFSETS_FILENAME


def _collect_crossover_samples(
    eng: RatingEngine, winners: dict[int, int | None], reds: dict[int, int],
) -> tuple[list[str], list[str], np.ndarray, np.ndarray, np.ndarray]:
    """Return (pool_r, pool_b, mu_r, mu_b, score_red) for crossover fights."""
    by_fight: dict[int, list] = collections.defaultdict(list)
    for h in eng.history:
        by_fight[h.fight_id].append(h)

    pr, pb, mr, mb, y = [], [], [], [], []
    for fid, rows in by_fight.items():
        if len(rows) != 2 or rows[0].division == rows[1].division:
            continue  # same-pool -> offsets cancel, contributes nothing.
        red_id = reds[fid]
        red = rows[0] if rows[0].fighter_id == red_id else rows[1]
        blue = rows[1] if red is rows[0] else rows[0]
        w = winners.get(fid)
        score = 0.5 if w is None else (1.0 if w == red_id else 0.0)
        pr.append(red.division)
        pb.append(blue.division)
        mr.append(red.mu_pre)
        mb.append(blue.mu_pre)
        y.append(score)
    return pr, pb, np.array(mr), np.array(mb), np.array(y)


def _pava(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators: project ``v`` onto the non-decreasing
    cone (isotonic regression), minimizing the ``w``-weighted squared error."""
    n = len(v)
    vals = v.astype(float).copy()
    wts = w.astype(float).copy()
    idx = list(range(n))          # start of each active block
    blocks = [[i] for i in range(n)]
    i = 0
    while i < len(blocks) - 1:
        if vals[i] > vals[i + 1] + 1e-15:
            # Merge blocks i and i+1 into their weighted mean.
            tw = wts[i] + wts[i + 1]
            vals[i] = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / tw
            wts[i] = tw
            blocks[i] = blocks[i] + blocks[i + 1]
            del blocks[i + 1]
            vals = np.delete(vals, i + 1)
            wts = np.delete(wts, i + 1)
            if i > 0:
                i -= 1           # step back to re-check the new left neighbour
        else:
            i += 1
    out = np.empty(n)
    for bval, members in zip(vals, blocks):
        for m in members:
            out[m] = bval
    return out


def _fit_offsets(pool_r: list[str], pool_b: list[str], mu_r: np.ndarray,
                 mu_b: np.ndarray, y: np.ndarray, lam: float,
                 anchor: str, monotonic: bool = config.OFFSET_MONOTONIC,
                 order: tuple[str, ...] = CANONICAL_WEIGHT_ORDER
                 ) -> dict[str, float]:
    """Regularized logistic fit of per-division offsets (gradient descent).

    Offsets are fitted in dimensionless Glicko-scale units ``d`` (so the L2
    penalty is unit-balanced) and converted back to display rating points as
    ``D = d * SCALE`` on return.  The logit is
    ``z = (mu_r - mu_b)/SCALE + X @ d`` with an L2 penalty ``lam * ||d||^2``.

    When ``monotonic`` is set the offsets are constrained to be non-decreasing
    along ``order`` (light-to-heavy) by *projected* gradient descent: after each
    step the offset vector is isotonic-regressed (weighted PAVA) onto the
    monotone cone and re-anchored so the anchor division stays at 0.  This
    removes the physically impossible inversions an unconstrained fit produced
    from the sparse, red-corner-confounded crossover data.
    """
    present = set(pool_r) | set(pool_b)
    # All present divisions are fitted (incl. the anchor); the anchor is pinned
    # to 0 each step so the monotone chain spans the whole ladder consistently.
    divisions = [d for d in order if d in present]
    # Any present division missing from the canonical order goes last (defensive;
    # none exist in the current DB).
    divisions += sorted(present - set(divisions))
    idx = {d: i for i, d in enumerate(divisions)}
    n, k = len(y), len(divisions)
    if n == 0 or k == 0:
        return {}

    X = np.zeros((n, k))
    for i, (dr, db) in enumerate(zip(pool_r, pool_b)):
        X[i, idx[dr]] += 1.0
        X[i, idx[db]] -= 1.0
    base_gap = (mu_r - mu_b) / SCALE  # fixed part of the logit.
    a_i = idx.get(anchor)
    # PAVA weights: how much crossover data touches each division.
    touch = np.abs(X).sum(axis=0)
    touch = np.where(touch > 0, touch, 1.0)

    d = np.zeros(k)
    lr = 0.3
    for _ in range(50000):
        z = base_gap + X @ d
        p = 1.0 / (1.0 + np.exp(-z))
        grad = X.T @ (p - y) + 2.0 * lam * d
        d -= lr * grad / n
        if monotonic:
            d = _pava(d, touch)
            if a_i is not None:
                d = d - d[a_i]        # re-pin anchor to 0 (shift keeps monotone)
    if a_i is not None:
        d = d - d[a_i]
    return {div: float(d[idx[div]] * SCALE) for div in divisions if div != anchor}


def calibrate(db_path: Path | str | None = None,
              lam: float = config.OFFSET_L2_LAMBDA) -> dict:
    conn = get_conn(db_path) if db_path else get_conn()
    conn.execute(f"PRAGMA busy_timeout = {config.SQLITE_BUSY_TIMEOUT_MS}")
    fights = load_fights(conn)
    winners = {f.id: f.winner_id for f in fights}
    reds = {f.id: f.red_id for f in fights}
    conn.close()

    eng = RatingEngine(offsets={})  # zero offsets for the first pass.
    eng.run(fights)

    pr, pb, mr, mb, y = _collect_crossover_samples(eng, winners, reds)
    offsets = _fit_offsets(pr, pb, mr, mb, y, lam, config.OFFSET_ANCHOR_DIVISION)
    offsets[config.OFFSET_ANCHOR_DIVISION] = 0.0

    payload = {
        "offsets": {k: round(v, 2) for k, v in sorted(offsets.items())},
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "anchor_division": config.OFFSET_ANCHOR_DIVISION,
            "n_crossover_fights": int(len(y)),
            "l2_lambda": lam,
            "monotonic": config.OFFSET_MONOTONIC,
            "method": ("regularized logistic regression on pre-fight ratings of "
                       "crossover (catchweight/open-weight/cross-division) fights; "
                       "Lightweight anchored to 0"
                       + ("; isotonic (non-decreasing with weight) constraint"
                          if config.OFFSET_MONOTONIC else "")),
            "scale": SCALE,
        },
    }
    return payload


def main() -> int:
    payload = calibrate()
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    meta = payload["metadata"]
    print(f"Fitted {len(payload['offsets'])} division offsets from "
          f"{meta['n_crossover_fights']} crossover fights -> {OUT_PATH.name}")
    for d, v in payload["offsets"].items():
        print(f"  {d:<22} {v:+7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
