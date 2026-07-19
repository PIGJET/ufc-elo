"""Unit tests for the Glicko-2 core, checked against Glickman's worked example.

The paper (http://www.glicko.net/glicko/glicko2.pdf, section "Example
calculation") walks a player rated 1500/200 (sigma 0.06, tau 0.5) through a
rating period against three opponents:

    opponent 1: 1400 / 30   -> win  (s = 1)
    opponent 2: 1550 / 100  -> loss (s = 0)
    opponent 3: 1700 / 300  -> loss (s = 0)

and reports the post-period values rating = 1464.06, RD = 151.52, sigma = 0.05999.

Run directly (no pytest required):

    python -m elo.tests.test_glicko2
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# Allow running as a plain script (``python elo/tests/test_glicko2.py``).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from elo.glicko2 import Rating, expected_score, update  # noqa: E402


def test_glickman_worked_example() -> None:
    player = Rating(1500.0, 200.0, 0.06)
    opponents = [
        (Rating(1400.0, 30.0, 0.06), 1.0),
        (Rating(1550.0, 100.0, 0.06), 0.0),
        (Rating(1700.0, 300.0, 0.06), 0.0),
    ]
    post = update(player, opponents, tau=0.5)

    assert math.isclose(post.rating, 1464.06, abs_tol=0.01), post.rating
    assert math.isclose(post.rd, 151.52, abs_tol=0.01), post.rd
    assert math.isclose(post.sigma, 0.05999, abs_tol=1e-5), post.sigma


def test_scale_roundtrip() -> None:
    r = Rating(1650.0, 120.0, 0.06)
    r2 = Rating.from_mu_phi(r.mu, r.phi, r.sigma)
    assert math.isclose(r.rating, r2.rating, abs_tol=1e-9)
    assert math.isclose(r.rd, r2.rd, abs_tol=1e-9)


def test_expected_score_symmetry() -> None:
    a = Rating(1600.0, 80.0, 0.06)
    b = Rating(1400.0, 80.0, 0.06)
    ea = expected_score(a, b)
    eb = expected_score(b, a)
    assert math.isclose(ea + eb, 1.0, abs_tol=1e-9)
    assert ea > 0.5  # higher-rated favourite


def test_win_raises_rating() -> None:
    a = Rating(1500.0, 200.0, 0.06)
    post = update(a, [(Rating(1500.0, 200.0, 0.06), 1.0)])
    assert post.rating > a.rating
    assert post.rd < a.rd  # a game always sharpens the estimate


def main() -> int:
    tests = [
        test_glickman_worked_example,
        test_scale_roundtrip,
        test_expected_score_symmetry,
        test_win_raises_rating,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:  # noqa: PERF203
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
