"""Chronological Glicko-2 replay of UFC fight history.

The engine walks every completed fight in date order, maintaining a
per-division rating pool for each fighter.  It is deliberately *pure*: it reads
fights + the fitted offset table and produces in-memory rating states and
``elo_history`` rows.  Persistence is the job of :mod:`elo.recompute`.

Design decisions (all traceable to ``docs/PLAN.md`` section 3):

* **One Glicko-2 update per bout.**  Glicko-2 nominally batches a rating period,
  but UFC fighters compete rarely and rating periods would be arbitrary, so we
  treat each bout as its own one-game period.  RD is inflated for the real-time
  layoff *before* each update (and again at reporting time), which is what the
  rating-period machinery would otherwise approximate.

* **MOV x stakes multiplier on the rating delta only.**  We run the textbook
  Glicko-2 update to get (mu_post_raw, rd_post, sigma_post), then rescale the
  *rating* change by the compounded MOV*stakes multiplier and clamp it.  RD and
  volatility come straight from Glicko-2, untouched -- so uncertainty still
  behaves canonically.

* **Cross-pool bouts.**  Catchweight / open-weight / tournament bouts are rated
  in each fighter's own home division (see :mod:`elo.divisions`).  Opponent
  strength is translated across pools with the fitted offsets D_w, and the
  pre-fight MOV gap is measured on the offset-adjusted (pound-for-pound) scale.

* **Division change.**  Re-entering (or entering) a pool while carrying history
  in another pool seeds ``mu_old + (D_new - D_old)``, regressed 25% toward the
  pool mean, with an RD bump -- never a fresh 1500.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from elo import config
from elo.divisions import (
    OPEN_WEIGHT_POOL,
    STANDARD_DIVISIONS,
    is_standard_division,
    normalize_weight_class,
)
from elo.glicko2 import Rating, expected_score, update

# Result codes as they appear in fights.result_current.
_RESULT_WIN = "win"
_RESULT_DRAW = "draw"
_RESULT_DQ = "dq"
_RESULT_NC = "nc"
_RATED_RESULTS = {_RESULT_WIN, _RESULT_DRAW, _RESULT_DQ}


# ---------------------------------------------------------------------------
# Lightweight records
# ---------------------------------------------------------------------------
@dataclass
class PoolState:
    """A fighter's live rating in one division pool."""

    rating: Rating
    last_date: date
    n_fights: int = 0


@dataclass
class EloRow:
    """One row destined for the ``elo_history`` table."""

    fighter_id: int
    fight_id: int
    date: str
    division: str
    mu_pre: float
    rd_pre: float
    mu_post: float
    rd_post: float
    sigma_post: float
    pfp_mu_post: float
    update_note: str


@dataclass
class FightRow:
    """A single completed fight, joined with its event date."""

    id: int
    date: str
    red_id: int
    blue_id: int
    winner_id: int | None
    result: str
    method: str | None
    round: int | None
    weight_class: str | None
    is_title: int
    is_interim_title: int
    is_main_event: int
    card_position: int | None
    scheduled_rounds: int | None


# ---------------------------------------------------------------------------
# Pure multiplier helpers
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def _years_between(a: date, b: date) -> float:
    return max(0.0, (b - a).days / config.DAYS_PER_YEAR)


def m_base(method: str | None, rnd: int | None, result: str) -> float:
    """Base MOV multiplier from method/round/result (PLAN table)."""
    if result == _RESULT_DRAW:
        return config.M_BASE_DRAW
    if result == _RESULT_DQ:
        return config.M_BASE_DQ
    if method in ("KO/TKO", "SUB"):
        r = rnd if rnd in config.M_BASE_FINISH_BY_ROUND else 5
        return config.M_BASE_FINISH_BY_ROUND[r]
    if method == "U-DEC":
        return config.M_BASE_UNANIMOUS_DECISION
    if method in ("S-DEC", "M-DEC"):
        return config.M_BASE_SPLIT_MAJORITY_DECISION
    # Fallback: treat any unknown decisive method as a unanimous decision.
    return config.M_BASE_UNANIMOUS_DECISION


def mov_correction(pre_gap: float) -> float:
    """538-style autocorrelation correction on the pre-fight winner-loser gap.

    ``pre_gap`` is (mu_winner - mu_loser) on the offset-adjusted display scale.
    Positive (favourite won) shrinks the multiplier; negative (upset) grows it.
    """
    k = config.MOV_CORRECTION_K
    return k / (pre_gap * config.MOV_GAP_SCALE + k)


def stakes_multiplier(f: FightRow) -> float:
    """Stakes multiplier S (highest applicable tier wins)."""
    if f.is_title and not f.is_interim_title:
        return config.STAKES_UNDISPUTED_TITLE
    if f.is_interim_title:
        return config.STAKES_INTERIM_TITLE
    if f.card_position == config.CARD_POSITION_MAIN_EVENT or f.is_main_event:
        return config.STAKES_MAIN_EVENT
    if f.card_position == config.CARD_POSITION_CO_MAIN:
        return config.STAKES_CO_MAIN
    return config.STAKES_DEFAULT


# ---------------------------------------------------------------------------
# Core bout math (shared by replay and preview)
# ---------------------------------------------------------------------------
def _clamp_rd(rd: float) -> float:
    return min(config.RD_MAX, max(config.RD_MIN, rd))


def rate_bout(
    a: Rating,
    b: Rating,
    score_a: float,
    multiplier: float,
    *,
    offset_a: float = 0.0,
    offset_b: float = 0.0,
    tau: float = config.GLICKO_TAU,
) -> tuple[Rating, Rating]:
    """Rate one bout, returning ``(a_post, b_post)``.

    ``score_a`` is a's Glicko score (1 win / 0.5 draw / 0 loss); b's is the
    complement.  ``multiplier`` (= MOV*stakes) rescales each fighter's *rating*
    delta only and is clamped to +/- MAX_RATING_DELTA.  ``offset_a``/``offset_b``
    are the fitted D_w for each fighter's pool, used to translate the opponent's
    strength into this fighter's frame for cross-pool bouts (0 for same-pool).
    """
    # Opponent expressed in the other fighter's pool frame.
    b_in_a = Rating(b.rating + (offset_a - offset_b), b.rd, b.sigma)
    a_in_b = Rating(a.rating + (offset_b - offset_a), a.rd, a.sigma)

    a_raw = update(a, [(b_in_a, score_a)], tau=tau)
    b_raw = update(b, [(a_in_b, 1.0 - score_a)], tau=tau)

    a_post = _apply_multiplier(a, a_raw, multiplier)
    b_post = _apply_multiplier(b, b_raw, multiplier)
    return a_post, b_post


def _apply_multiplier(pre: Rating, raw: Rating, multiplier: float) -> Rating:
    """Rescale the rating delta by ``multiplier`` (capped); keep RD/sigma."""
    delta = (raw.rating - pre.rating) * multiplier
    if delta > config.MAX_RATING_DELTA:
        delta = config.MAX_RATING_DELTA
    elif delta < -config.MAX_RATING_DELTA:
        delta = -config.MAX_RATING_DELTA
    return Rating(pre.rating + delta, _clamp_rd(raw.rd), raw.sigma)


def grow_rd(rating: Rating, last: date, now: date) -> Rating:
    """Inflate RD for the layoff between ``last`` and ``now`` (PLAN extension)."""
    years = _years_between(last, now)
    if years <= 0:
        return rating
    extra = config.RD_INACTIVITY_C_PER_YEAR ** 2 * years
    rd = min(config.RD_MAX, (rating.rd * rating.rd + extra) ** 0.5)
    return Rating(rating.rating, rd, rating.sigma)


# ---------------------------------------------------------------------------
# Pre-UFC seeding hook (NO-OP today: pre_ufc_record is empty everywhere)
# ---------------------------------------------------------------------------
def seed_rating(pre_ufc_record: str | None) -> Rating:
    """Seed a debut rating from a pre-UFC record.

    ``fighters.pre_ufc_record`` is EMPTY for every fighter in the current DB,
    so this returns the default 1500 / 350 / 0.06 for everyone today.  The
    parsing/promotion-tier logic is left as a documented hook (see
    ``config.SEED_PROMOTION_TIER_BUMP`` / ``config.SEED_MU_RANGE``) to be
    filled in once a source populates the column.
    """
    if not pre_ufc_record:
        return Rating(config.INITIAL_MU, config.INITIAL_RD, config.INITIAL_SIGMA)
    # --- future: parse "15-0 (Bellator)" -> promotion tier x record quality ---
    # tier = _classify_promotion(pre_ufc_record)
    # bump = config.SEED_PROMOTION_TIER_BUMP.get(tier, 0.0) * record_quality
    # mu = clamp(config.INITIAL_MU + bump, *config.SEED_MU_RANGE)
    # return Rating(mu, config.INITIAL_RD, config.INITIAL_SIGMA)
    return Rating(config.INITIAL_MU, config.INITIAL_RD, config.INITIAL_SIGMA)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
class RatingEngine:
    """Replays fight history into per-fighter, per-division rating pools."""

    def __init__(self, offsets: dict[str, float] | None = None,
                 pre_ufc: dict[int, str] | None = None) -> None:
        #: Fitted cross-division offsets D_w (0 for any missing pool).
        self.offsets: dict[str, float] = dict(offsets or {})
        #: fighter_id -> pre_ufc_record string (empty today).
        self.pre_ufc: dict[int, str] = dict(pre_ufc or {})
        #: (fighter_id, pool) -> PoolState.
        self.pools: dict[tuple[int, str], PoolState] = {}
        #: fighter_id -> most recent standard division fought (for home lookup).
        self._last_standard: dict[int, str] = {}
        #: fighter_id -> most frequent career standard division (fallback).
        self._primary_standard: dict[int, str] = {}
        #: Emitted history rows, in processing order.
        self.history: list[EloRow] = []

    # -- offsets ------------------------------------------------------------
    def offset(self, pool: str) -> float:
        return self.offsets.get(pool, 0.0)

    # -- home-division resolution ------------------------------------------
    def _home_division(self, fighter_id: int) -> str:
        """The pool a non-standard bout for this fighter is rated in."""
        if fighter_id in self._last_standard:
            return self._last_standard[fighter_id]
        if fighter_id in self._primary_standard:
            return self._primary_standard[fighter_id]
        return OPEN_WEIGHT_POOL

    def _resolve_pools(self, f: FightRow) -> tuple[str, str]:
        wc = normalize_weight_class(f.weight_class)
        if is_standard_division(wc):
            return wc, wc  # type: ignore[return-value]
        # Non-standard (open weight / catchweight / tournament): per fighter.
        return self._home_division(f.red_id), self._home_division(f.blue_id)

    # -- state access / seeding --------------------------------------------
    def _most_recent_state(self, fighter_id: int) -> tuple[str, PoolState] | None:
        best: tuple[str, PoolState] | None = None
        for (fid, pool), st in self.pools.items():
            if fid != fighter_id:
                continue
            if best is None or st.last_date > best[1].last_date:
                best = (pool, st)
        return best

    def _get_pre_state(self, fighter_id: int, pool: str,
                       when: date) -> tuple[Rating, str]:
        """Return the RD-grown pre-fight rating and a seeding note."""
        existing = self.pools.get((fighter_id, pool))
        if existing is not None:
            grown = grow_rd(existing.rating, existing.last_date, when)
            return grown, ""

        # No history in this pool: division change or debut?
        prior = self._most_recent_state(fighter_id)
        if prior is None:
            return self.seed_debut(fighter_id), "debut"

        old_pool, old = prior
        mu_translated = old.rating.rating + (self.offset(pool) - self.offset(old_pool))
        # Regress toward the pool mean.
        frac = config.DIVISION_CHANGE_REGRESSION_FRACTION
        mu_seed = config.INITIAL_MU + (1.0 - frac) * (mu_translated - config.INITIAL_MU)
        # Bump RD (fresh uncertainty at the new weight) then grow for layoff.
        rd_bumped = min(
            config.RD_MAX,
            (old.rating.rd ** 2 + config.DIVISION_CHANGE_RD_BUMP ** 2) ** 0.5,
        )
        seeded = grow_rd(Rating(mu_seed, rd_bumped, old.rating.sigma),
                         old.last_date, when)
        note = f"div-change from {old_pool} (mu {old.rating.rating:.0f}->{mu_seed:.0f})"
        return seeded, note

    def seed_debut(self, fighter_id: int) -> Rating:
        return seed_rating(self.pre_ufc.get(fighter_id))

    # -- main replay --------------------------------------------------------
    def process(self, f: FightRow) -> None:
        when = _parse_date(f.date)
        pool_r, pool_b = self._resolve_pools(f)

        if f.result == _RESULT_NC:
            # No rating change; advance the activity clock in each fighter's
            # current pool so the layoff is measured from here.
            self._touch_activity(f.red_id, pool_r, when)
            self._touch_activity(f.blue_id, pool_b, when)
            return

        if f.result not in _RATED_RESULTS:
            return  # upcoming or unknown -- skip.

        pre_r, note_r = self._get_pre_state(f.red_id, pool_r, when)
        pre_b, note_b = self._get_pre_state(f.blue_id, pool_b, when)

        # Determine scores and the winner/loser orientation for MOV.
        if f.result == _RESULT_DRAW:
            score_r = 0.5
            gap = 0.0
            corr = 1.0
        else:  # win or dq -- winner_id set
            winner_is_red = f.winner_id == f.red_id
            score_r = 1.0 if winner_is_red else 0.0
            mu_w = (pre_r.rating + self.offset(pool_r)) if winner_is_red \
                else (pre_b.rating + self.offset(pool_b))
            mu_l = (pre_b.rating + self.offset(pool_b)) if winner_is_red \
                else (pre_r.rating + self.offset(pool_r))
            gap = mu_w - mu_l
            corr = mov_correction(gap)

        base = m_base(f.method, f.round, f.result)
        stakes = stakes_multiplier(f)
        multiplier = base * corr * stakes

        post_r, post_b = rate_bout(
            pre_r, pre_b, score_r, multiplier,
            offset_a=self.offset(pool_r), offset_b=self.offset(pool_b),
        )

        note = (f"M_base {base:.2f} x MOV {corr:.2f} x stakes {stakes:.2f} "
                f"= {multiplier:.2f}")
        capped_r = abs((post_r.rating - pre_r.rating)) >= config.MAX_RATING_DELTA - 1e-9
        capped_b = abs((post_b.rating - pre_b.rating)) >= config.MAX_RATING_DELTA - 1e-9

        self._commit(f, f.red_id, pool_r, pre_r, post_r, when,
                     note + (", capped" if capped_r else "")
                     + (f"; {note_r}" if note_r else ""))
        self._commit(f, f.blue_id, pool_b, pre_b, post_b, when,
                     note + (", capped" if capped_b else "")
                     + (f"; {note_b}" if note_b else ""))

    def _commit(self, f: FightRow, fighter_id: int, pool: str,
                pre: Rating, post: Rating, when: date, note: str) -> None:
        prev = self.pools.get((fighter_id, pool))
        n = (prev.n_fights if prev else 0) + 1
        self.pools[(fighter_id, pool)] = PoolState(post, when, n)
        if is_standard_division(pool):
            self._last_standard[fighter_id] = pool
        self.history.append(EloRow(
            fighter_id=fighter_id, fight_id=f.id, date=f.date, division=pool,
            mu_pre=pre.rating, rd_pre=pre.rd,
            mu_post=post.rating, rd_post=post.rd, sigma_post=post.sigma,
            pfp_mu_post=post.rating + self.offset(pool),
            update_note=note,
        ))

    def _touch_activity(self, fighter_id: int, pool: str, when: date) -> None:
        st = self.pools.get((fighter_id, pool))
        if st is not None:
            st.last_date = when

    # -- reporting ----------------------------------------------------------
    def current_ratings(self, as_of: date) -> list[dict]:
        """One dict per (fighter, pool) with RD grown to ``as_of``."""
        rows: list[dict] = []
        for (fid, pool), st in self.pools.items():
            grown = grow_rd(st.rating, st.last_date, as_of)
            rows.append({
                "fighter_id": fid,
                "division": pool,
                "mu": grown.rating,
                "rd": grown.rd,
                "sigma": grown.sigma,
                "pfp_mu": grown.rating + self.offset(pool),
                "n_fights": st.n_fights,
                "last_fight": st.last_date.isoformat(),
                "as_of": as_of.isoformat(),
            })
        return rows

    # -- setup helpers ------------------------------------------------------
    def precompute_primary_divisions(self, fights: list[FightRow]) -> None:
        """Populate the most-frequent-standard-division fallback map."""
        counts: dict[int, dict[str, int]] = {}
        for f in fights:
            wc = normalize_weight_class(f.weight_class)
            if not is_standard_division(wc):
                continue
            for fid in (f.red_id, f.blue_id):
                counts.setdefault(fid, {}).setdefault(wc, 0)  # type: ignore[arg-type]
                counts[fid][wc] += 1  # type: ignore[index]
        for fid, dc in counts.items():
            self._primary_standard[fid] = max(dc.items(), key=lambda kv: kv[1])[0]

    def run(self, fights: list[FightRow]) -> None:
        """Replay ``fights`` (assumed already in chronological order)."""
        self.precompute_primary_divisions(fights)
        for f in fights:
            self.process(f)


# ---------------------------------------------------------------------------
# Loading + preview
# ---------------------------------------------------------------------------
def load_fights(conn: sqlite3.Connection) -> list[FightRow]:
    """Load all completed (non-upcoming) fights in chronological order.

    Ordered by event date, then card position descending so that undercard
    bouts precede the main event, and one-night-tournament earlier rounds
    precede the final (which carries card_position 1).
    """
    cur = conn.execute(
        """
        SELECT f.id, e.date, f.fighter_red_id, f.fighter_blue_id, f.winner_id,
               f.result_current, f.method, f.round, f.weight_class,
               f.is_title, f.is_interim_title, f.is_main_event,
               f.card_position, f.scheduled_rounds
        FROM fights f JOIN events e ON f.event_id = e.id
        WHERE f.result_current != 'upcoming' AND e.date IS NOT NULL
        ORDER BY e.date ASC,
                 (f.card_position IS NULL) ASC,
                 f.card_position DESC,
                 f.id ASC
        """
    )
    return [
        FightRow(
            id=r[0], date=r[1], red_id=r[2], blue_id=r[3], winner_id=r[4],
            result=r[5], method=r[6], round=r[7], weight_class=r[8],
            is_title=r[9], is_interim_title=r[10], is_main_event=r[11],
            card_position=r[12], scheduled_rounds=r[13],
        )
        for r in cur.fetchall()
    ]


def preview_update(
    a: Rating,
    b: Rating,
    *,
    pool_a: str,
    pool_b: str,
    offsets: dict[str, float] | None = None,
    method: str = "U-DEC",
    rnd: int = 3,
    stakes: float = config.STAKES_DEFAULT,
    tau: float = config.GLICKO_TAU,
) -> dict[str, Rating]:
    """Pure "what-if" for the API's potential rating gain/loss feature.

    Given both fighters' *current* ratings and a hypothetical bout context,
    returns the post-fight ratings under each outcome:

        {"a_if_win", "a_if_loss", "b_if_win", "b_if_loss"}

    ``a_if_win``/``b_if_loss`` come from the same replay (a beats b) so they are
    mutually consistent; likewise ``a_if_loss``/``b_if_win`` from (b beats a).
    ``method``/``rnd`` set the assumed MOV; default is a unanimous decision.
    """
    off = offsets or {}
    oa, ob = off.get(pool_a, 0.0), off.get(pool_b, 0.0)

    def outcome(winner: Rating, loser: Rating, off_w: float, off_l: float,
                score_w: float) -> tuple[Rating, Rating]:
        gap = (winner.rating + off_w) - (loser.rating + off_l)
        mult = m_base(method, rnd, _RESULT_WIN) * mov_correction(gap) * stakes
        return rate_bout(winner, loser, score_w, mult,
                         offset_a=off_w, offset_b=off_l, tau=tau)

    a_win, b_loss = outcome(a, b, oa, ob, 1.0)      # a beats b
    b_win, a_loss = outcome(b, a, ob, oa, 1.0)      # b beats a
    return {
        "a_if_win": a_win, "a_if_loss": a_loss,
        "b_if_win": b_win, "b_if_loss": b_loss,
    }


# Re-export for the API layer's convenience.
__all__ = [
    "RatingEngine", "FightRow", "EloRow", "PoolState",
    "load_fights", "preview_update", "expected_score", "rate_bout",
    "grow_rd", "seed_rating", "STANDARD_DIVISIONS",
]
