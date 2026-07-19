"""Clean Glicko-2 implementation (Glickman, 2013).

This is a faithful, self-contained implementation of Mark Glickman's Glicko-2
system, kept deliberately free of any UFC-specific logic so it can be unit
tested against the worked example in the paper
(http://www.glicko.net/glicko/glicko2.pdf).

Ratings are stored on the familiar 1500/350 display scale; all internal
computation happens on the Glicko-2 scale via the fixed 173.7178 conversion
factor.  The margin-of-victory / stakes multipliers required by the UFC engine
are applied *outside* this module (on the rating delta only) so that the core
here stays textbook-correct.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Conversion factor between the display scale (mean 1500) and the internal
# Glicko-2 scale (mean 0).  From Glickman's paper: 173.7178.
SCALE: float = 173.7178
DISPLAY_MEAN: float = 1500.0

# Default system constant tau (constrains volatility change); overridable.
DEFAULT_TAU: float = 0.5
# Convergence tolerance for the volatility iteration.
_EPSILON: float = 1e-6


@dataclass
class Rating:
    """A rating on the display scale (rating/RD) plus Glicko-2 volatility."""

    rating: float = 1500.0
    rd: float = 350.0
    sigma: float = 0.06

    # --- scale conversions -------------------------------------------------
    @property
    def mu(self) -> float:
        """Rating on the internal Glicko-2 scale."""
        return (self.rating - DISPLAY_MEAN) / SCALE

    @property
    def phi(self) -> float:
        """RD on the internal Glicko-2 scale."""
        return self.rd / SCALE

    @classmethod
    def from_mu_phi(cls, mu: float, phi: float, sigma: float) -> "Rating":
        return cls(rating=mu * SCALE + DISPLAY_MEAN, rd=phi * SCALE, sigma=sigma)


def _g(phi: float) -> float:
    """Weighting of a game by the opponent's rating deviation."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, mu_j: float, phi_j: float) -> float:
    """Expected score of the player vs. opponent j (Glicko-2 scale)."""
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def expected_score(a: Rating, b: Rating) -> float:
    """Probability that ``a`` beats ``b``, deviation-aware (both sides)."""
    # Symmetric form: weight by the combined deviation of both players.
    phi = math.sqrt(a.phi * a.phi + b.phi * b.phi)
    return 1.0 / (1.0 + math.exp(-_g(phi) * (a.mu - b.mu)))


def _new_sigma(sigma: float, phi: float, v: float, delta: float,
               tau: float) -> float:
    """Illinois-algorithm solution of the Glicko-2 volatility equation."""
    a = math.log(sigma * sigma)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (tau * tau)

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    while abs(B - A) > _EPSILON:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA = fA / 2.0
        B, fB = C, fC
    return math.exp(A / 2.0)


def update(rating: Rating, opponents: list[tuple[Rating, float]],
           tau: float = DEFAULT_TAU) -> Rating:
    """Return the post-period :class:`Rating` after playing ``opponents``.

    ``opponents`` is a list of ``(opponent_rating, score)`` where ``score`` is
    1.0 (win), 0.5 (draw) or 0.0 (loss).  An empty list applies the
    "did not compete" path: rating and volatility unchanged, RD inflated by the
    volatility (handled by :func:`inactivity_rd` in the engine instead).
    """
    if not opponents:
        return Rating(rating.rating, rating.rd, rating.sigma)

    mu, phi, sigma = rating.mu, rating.phi, rating.sigma

    # Step 3: estimated variance v of the team performance.
    inv_v = 0.0
    delta_sum = 0.0
    for opp, score in opponents:
        g = _g(opp.phi)
        e = _expected(mu, opp.mu, opp.phi)
        inv_v += g * g * e * (1.0 - e)
        delta_sum += g * (score - e)
    v = 1.0 / inv_v

    # Step 4: estimated improvement delta.
    delta = v * delta_sum

    # Step 5: new volatility.
    sigma_prime = _new_sigma(sigma, phi, v, delta, tau)

    # Step 6: pre-rating-period RD.
    phi_star = math.sqrt(phi * phi + sigma_prime * sigma_prime)

    # Step 7: new RD and rating.
    phi_prime = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_prime = mu + phi_prime * phi_prime * delta_sum

    return Rating.from_mu_phi(mu_prime, phi_prime, sigma_prime)
