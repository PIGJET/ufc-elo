"""Canonical division list and weight-class normalization.

The ``fights.weight_class`` column holds free-text values from ufcstats.com:
the twelve standard modern divisions, plus historical / non-standard buckets
("Open Weight", "Catchweight", the early one-night tournament titles, and the
old "UFC Superfight Championship").  ``fighters.division`` is effectively empty
in the current DB, so a fighter's *home* division is derived from fight
history, not read from that column.

Policy for non-standard bouts (per PLAN section 3):
  * "Catchweight" and "Open Weight" (and the tournament / superfight titles,
    which were all effectively open-weight) are NOT rated in a pool of their
    own.  Instead each fighter is rated in *their own* home division pool --
    the standard division of their most recent prior rated bout (falling back
    to their most frequent career division, then to a synthetic "Open Weight"
    pool only for fighters who never fought a standard-division bout, i.e. the
    1990s tournament-only competitors).
  * This makes such bouts genuine cross-pool comparisons, handled by the engine
    via the fitted cross-division offsets D_w.
"""
from __future__ import annotations

#: The twelve standard, currently-contested UFC divisions.  Order is
#: light-to-heavy (men) then the women's divisions, for stable reporting.
STANDARD_DIVISIONS: tuple[str, ...] = (
    "Flyweight",
    "Bantamweight",
    "Featherweight",
    "Lightweight",
    "Welterweight",
    "Middleweight",
    "Light Heavyweight",
    "Heavyweight",
    "Women's Strawweight",
    "Women's Flyweight",
    "Women's Bantamweight",
    "Women's Featherweight",
)

#: Synthetic pool for competitors who only ever fought open-weight/tournament
#: bouts (mostly the 1994-1996 era) and thus have no standard home division.
OPEN_WEIGHT_POOL: str = "Open Weight"

#: Representative fighting weight (lbs) per division -- the division's official
#: upper limit, used as the canonical anchor for two things:
#:   1. the *monotonic* (isotonic) ordering the offset fit is constrained to
#:      (heavier divisions must never rate below lighter ones on the P4P scale);
#:   2. the explicit cross-division ``weight_gap`` prediction feature, which
#:      penalizes the lighter fighter by their true mass deficit rather than by
#:      a coarse division count (so the LHW->HW leap of 60 lbs counts far more
#:      than the FW->LW leap of 10 lbs -- as it does in reality).
#: Open Weight sits between Light Heavyweight and Heavyweight: the 1990s
#: open-weight entrants were typically large-but-not-modern-super-heavyweights.
#: Heavyweight uses its 265-lb ceiling; the extreme-gap predictions that ride on
#: it are flagged speculative and are model-extrapolated, not data-validated.
DIVISION_WEIGHT_LB: dict[str, float] = {
    "Women's Strawweight": 115.0,
    "Women's Flyweight": 125.0,
    "Flyweight": 125.0,
    "Women's Bantamweight": 135.0,
    "Bantamweight": 135.0,
    "Women's Featherweight": 145.0,
    "Featherweight": 145.0,
    "Lightweight": 155.0,
    "Welterweight": 170.0,
    "Middleweight": 185.0,
    "Light Heavyweight": 205.0,
    "Open Weight": 235.0,
    "Heavyweight": 265.0,
}

#: Canonical light-to-heavy ordering (by representative weight) the offset fit is
#: monotonicity-constrained over.  Ties (e.g. Flyweight / Women's Flyweight at
#: 125) are broken by name for a stable, deterministic order.
CANONICAL_WEIGHT_ORDER: tuple[str, ...] = tuple(
    sorted(DIVISION_WEIGHT_LB, key=lambda d: (DIVISION_WEIGHT_LB[d], d))
)


def division_weight(division: str | None) -> float | None:
    """Representative fighting weight (lbs) for a division, or ``None``."""
    if division is None:
        return None
    return DIVISION_WEIGHT_LB.get(division)

#: Every raw weight_class value that means "not a standard-division bout".
#: These trigger per-fighter home-division resolution rather than defining a
#: pool themselves.
_NON_STANDARD_MARKERS: frozenset[str] = frozenset(
    {"open weight", "openweight", "catchweight", "catch weight"}
)


def normalize_weight_class(raw: str | None) -> str | None:
    """Map a raw ``weight_class`` string to a canonical division.

    Returns a value in :data:`STANDARD_DIVISIONS` for the twelve standard
    divisions, or :data:`OPEN_WEIGHT_POOL` for any non-standard bout
    (catchweight / open weight / tournament title / superfight).  Returns
    ``None`` only for an unrecognised, non-empty oddity (none exist in the
    current DB, but callers should treat ``None`` as "resolve per fighter").
    """
    if not raw:
        return None
    text = raw.strip()
    if text in STANDARD_DIVISIONS:
        return text
    low = text.lower()
    if low in _NON_STANDARD_MARKERS:
        return OPEN_WEIGHT_POOL
    # Tournament titles, "UFC Superfight Championship", "UFC N Tournament Title",
    # "Ultimate Ultimate 'NN Tournament Title" -- all effectively open weight.
    if "tournament" in low or "superfight" in low or "ultimate ultimate" in low:
        return OPEN_WEIGHT_POOL
    return None


def is_standard_division(division: str | None) -> bool:
    """True if ``division`` is one of the twelve standard pools."""
    return division in STANDARD_DIVISIONS


def is_non_standard_bout(raw: str | None) -> bool:
    """True if the raw weight_class is a catchweight/open-weight/title bout."""
    return normalize_weight_class(raw) == OPEN_WEIGHT_POOL
