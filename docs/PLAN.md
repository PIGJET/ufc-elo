# UFC Elo Rating & Matchup Predictor — Approved Plan (v2)

This is the approved build plan. All implementation work must conform to it.
Repo layout: `data/` (scrapers + ingestion + SQLite DB), `elo/` (rating engine),
`api/` (FastAPI), `web/` (React frontend), `docs/` (this plan + reports).

## 1. Project goal

A website that:
1. Computes a Glicko-2-based rating ("Elo" in the UI) for every UFC fighter, past and present.
2. Shows fighter rankings by division with ratings, plus fighter profile pages.
3. Shows confirmed upcoming UFC events with betting odds, predicted winner/confidence,
   and each fighter's potential rating gain/loss.
4. Lets a user match up any two fighters (cross-division allowed) for a full stat
   comparison and win probability.

## 2. Database (SQLite, Postgres-portable)

Single DB file: `data/ufc.db`. Schema lives in `data/schema.sql` — that file is the
source of truth; do not deviate from it without updating it. No SQLite-only features
(no WITHOUT ROWID, no non-standard types) so a later Postgres move is dump/restore.

Tables: `fighters`, `events`, `fights`, `fight_stats`, `odds`, `rankings_snapshot`,
`elo_history`, `provenance`, `sync_state`.

Key policies:
- Fights store `result_original` AND `result_current`. Ratings always compute from
  `result_current`; a USADA/commission overturn flips it to NC and a full recompute
  erases the rating change downstream. Overturns are annotated in fight history UI.
- No-contests update activity clocks but never ratings.
- Catchweight bouts rate in the nearest fighter's primary division pool.
- Odds are append-only (line movement preserved).
- Every scraped field logs `(entity, entity_id, field, source, value_seen, fetched_at)`
  to `provenance`. Cross-source disagreements go to a discrepancy report, never
  silently overwritten.
- `sync_state` holds per-source high-water marks so re-syncs are incremental.

## 3. Rating engine (Glicko-2 core + documented multipliers)

Per-division pools; each fighter carries (rating mu, deviation RD, volatility sigma)
per division. Displayed as e.g. "1650 ± 120". Scale: Glicko-2 internal, reported on
the classic 1500/350 scale.

- RD replaces all ad hoc K rules: high for newcomers, shrinks with fights, grows with
  inactivity. No manual layoff decay, no K tiers.
- MOV and stakes are a documented post-multiplier on the rating update delta only
  (NOT on the RD update). Non-standard vs. textbook Glicko-2 — must be documented in
  README and `elo/config.py`.

MOV (autocorrelation-corrected, 538-style):
```
M_final = M_base(finish) * 2.2 / ((mu_winner - mu_loser) * 0.001 + 2.2)
```
(pre-fight mu gap; negative for upsets → amplifies underdog finishes, shrinks
favorite finishes.)

M_base: R1 finish 1.50, R2 1.35, R3 1.20, R4-5 1.10, U-DEC 1.00,
S/M-DEC 0.85, DQ win 0.60, draw 0.75 (score 0.5 each).

Stakes S: undisputed title 1.25, interim title 1.15, non-title main event 1.10,
co-main 1.05, else 1.00. Compounded update has a hard cap (tunable in config).

- New fighter seeding: prior from pre-UFC record — promotion tier
  (Bellator/PFL/ONE > major regional > other) x record quality → seeded mu in
  ~[1450, 1620], RD at max (~350).
- Division change: new-division mu seeded as `mu_old + (D_new - D_old)`, regressed
  25% toward pool mean, RD bumped. Never a fresh-1500 restart.
- Cross-division offsets D_w: fitted (regularized) from division-switchers and
  catchweight bouts by a committed calibration script. PFP rating = division mu + D_w.
- All constants in `elo/config.py`, explained in README.

## 4. Prediction layer (separate from ratings — ratings stay pure)

Win probability = Glicko expected score adjusted in logit space by a logistic
regression fit on historical fights:
- rating gap (RD-aware), style-class matchup (striker/wrestler/grappler/balanced),
  stance matchup (southpaw-vs-orthodox dummy), reach/height/age deltas (esp. >35),
  layoff delta, recent-form (last-3-fight rating trend, computed from ratings as
  they stood at fight time), rematch dummy (2nd/3rd meeting + who won prior).
- Style classification from career fight_stats with shrinkage: blend rate stats with
  division means via n/(n+8); below a confidence floor display "insufficient data".
- ALL fitted parameters (betas, D_w, MOV constants) fit strictly walk-forward:
  train on fights before T, evaluate after. No future info in any feature.
- Cross-division matchups get D_w adjustment + "speculative comparison" flag.

## 5. Backtesting harness (Phase 2 deliverable)

Walk-forward replay of full history producing: log-loss + Brier vs. three baselines
(coin flip, vanilla Elo, betting odds where available), calibration curve
(70% favorites should win ~70%), and an ablation table (each multiplier on/off —
anything that doesn't improve walk-forward log-loss gets cut).

## 6. Data sources & compliance

| Source | Used for | Compliance approach |
|---|---|---|
| Kaggle UFC dataset (or equivalent public mirror) | Fast historical base | Openly licensed. |
| ufcstats.com | Canonical fight history, per-fight stats, fighter physicals; reconcile/backfill | Public, permissive; rate limit ~1 req/2s, cache all raw HTML. |
| ufc.com (athletes, rankings, events) | Bios/photos, current rankings, upcoming cards | Only needed public pages, ~1 req/3s, identifying User-Agent, aggressive caching, incremental. Photos hotlinked, not rehosted. |
| The Odds API (PRIMARY odds source) | DK/FanDuel moneylines | Compliant API, free tier. Key via ODDS_API_KEY env var; degrade gracefully without it. |
| DraftKings direct scrape | Fallback ONLY, disabled by default | Against DK ToS; documented at-your-own-risk, public pages, very low rate. |

Cross-check fields present in 2+ sources; disagreements → discrepancy report.

## 7. API (FastAPI, `api/`)

- `GET /api/rankings` — divisions with champion + ranked contenders + ratings
- `GET /api/fighters?search=` — typeahead search
- `GET /api/fighters/{id}` — profile, rating history, fight history
- `GET /api/events/upcoming` — cards + odds + predictions + rating gain/loss previews
- `GET /api/matchup?a={id}&b={id}` — head-to-head prediction + factor breakdown

## 8. Frontend (React + Vite + TS, `web/`)

Look: UFC.com-inspired. Near-black #0f0f0f bg, white text, UFC red #d20a0a accents.
White top nav, black bold all-caps tabs (RANKINGS | EVENTS | MATCHUPS), centered
"UFC ELO" wordmark (own mark, not UFC's logo) with red underline. Fonts: Oswald
(headings/numbers, all-caps, tight tracking) + system sans body. Charts: Recharts.

Pages: RankingsPage (landing: grid of division columns, rating shown on right of
each row), EventsPage (upcoming cards → matchup modules with stat tabs
Matchup Stats / Win By / Sig Strikes / Grappling + odds + prediction + rating
gain/loss), MatchupsPage (two typeahead pickers → same module + factor breakdown +
cross-division note), FighterProfilePage (hero w/ cutout photo, chips, nickname,
record, rating ± RD, stat row with red underlines, rating-over-time chart, last
fight card, fight history).

## 9. Build phases

1. Data layer (schema, ingestion, scrapers, odds client, provenance, DQ report)
2. Rating engine + calibration + backtest harness
3. API
4. Frontend

Check in with the user after each phase.
