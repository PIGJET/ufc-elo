# UFC Elo — Ratings & Matchup Predictor

A full-stack UFC analytics site: a Glicko-2-based rating ("Elo") for every UFC
fighter in history, division rankings, fighter profiles with rating-history
charts, upcoming events with predictions and betting odds, and a head-to-head
matchup predictor for any two fighters — styled after UFC.com.

## Quickstart

```powershell
# 0. One-time setup
pip install -r requirements.txt
cd web; npm install; cd ..

# 1. Data: refresh historical fights, upcoming cards, rankings, odds
python data\sync.py --all          # or individual flags, see below

# 2. Ratings: full recompute from fight history (~1 s)
python elo\recompute.py

# 3. Run the site
python -m uvicorn api.main:app --port 8000        # terminal 1
cd web; npm run dev                                # terminal 2 → http://localhost:5173
```

## Data pipeline (`data/`)

| Command | What it does |
|---|---|
| `python data\sync.py --historical` | Re-ingests the complete UFC fight history (1994→present) from the MIT-licensed [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) mirror of ufcstats.com (refreshed daily upstream). Idempotent. |
| `python data\sync.py --events` | Scrapes upcoming cards from ufc.com/events (robots-compliant, 15s crawl delay, cached). |
| `python data\sync.py --rankings` | Scrapes the official UFC rankings into a dated snapshot. |
| `python data\sync.py --odds` | Pulls DraftKings/FanDuel moneylines via **The Odds API**. Needs a free key from [the-odds-api.com](https://the-odds-api.com) in `.env` as `ODDS_API_KEY=...`. Without a key it prints a pointer and exits cleanly. |
| `python data\sync.py --athletes N` | Enriches N fighters (bio, country, leg reach, cutout photo URL) from ufc.com athlete pages. |

Everything lands in `data/ufc.db` (SQLite; schema in `data/schema.sql`, written
portable so a Postgres move is dump/restore). Every scraped field logs its
source to a `provenance` table; when two sources disagree (e.g. two different
reach values) the existing value is kept and both are logged for audit —
nothing is silently overwritten. Data-quality details: `docs/data_quality_report.md`.

**Compliance notes.** ufcstats.com sits behind an anti-bot challenge which this
project does not bypass — completed-fight data comes from the licensed mirror
instead (set `UFCSTATS_COOKIE` from your own browser session if you ever want
direct access). ufc.com scraping honors its robots.txt crawl-delay. Betting
odds come from The Odds API because DraftKings'/FanDuel's terms prohibit
scraping their pages directly; a DraftKings scraper exists only as a
disabled, unimplemented stub. Fighter photos are hotlinked, never rehosted.

## The rating model (`elo/`)

Plain-language version — every constant lives documented in `elo/config.py`.

- **Glicko-2 core, per division.** Each fighter carries a rating (μ), a rating
  deviation (RD — how uncertain the rating is), and a volatility, per division
  they've fought in. Displayed as e.g. `2597 ± 208`. RD starts high for
  newcomers, shrinks with fights, and grows again during layoffs — so a
  returning fighter's rating is honest about its staleness instead of being
  silently decayed. The core math is unit-tested against the worked example in
  Glickman's Glicko-2 paper.
- **Margin of victory, inflation-proofed.** Finishes move ratings more than
  decisions (round-1 finish ×1.50 down to split decision ×0.85), but the
  multiplier is scaled by `2.2 / ((μ_winner − μ_loser)/1000 + 2.2)` — the
  FiveThirtyEight autocorrelation correction. An underdog's round-1 upset gets
  amplified; a heavy favorite crushing an overmatched opponent gets shrunk.
  Measured result: per-division mean ratings hold at ~1500 across 30 years —
  no systematic inflation.
- **Stakes.** Title fights ×1.25 (interim ×1.15), main events ×1.10,
  co-mains ×1.05. The compounded update is hard-capped. These multipliers apply
  to the rating change only, never to the uncertainty update.
- **Messy results.** No-contests change nothing but the activity clock; an
  overturned result (e.g. a failed drug test) is stored alongside the original
  cage announcement, and ratings always recompute from the *current* ruling.
- **Division changes.** A fighter moving weight classes is seeded from their
  old rating plus a fitted per-division offset (calibrated from the ~76
  crossover fights in history, `elo/division_offsets.json`), regressed 25%
  toward the mean, with widened RD — never restarted from scratch. The offset
  table is now fit under an **isotonic (monotonic) constraint**: a heavier
  division can never rate below a lighter one on the pound-for-pound scale,
  removing the physically impossible inversions the sparse, red-corner-biased
  crossover data used to produce.
- **Prediction layer, separate from ratings.** Win probabilities start from the
  Glicko expected score, then a logistic layer (fit strictly walk-forward —
  trained only on fights before the ones it's evaluated on) adjusts for: age
  (−0.08 logit/year older), ring rust (−0.15/year of layoff), wrestler-vs-striker
  (+0.12), southpaw-vs-orthodox (+0.12), rematch history, height/reach, and a
  temperature parameter that fixes raw Glicko's measured overconfidence.
  Ratings themselves are never touched by this layer.
- **Cross-division weight gap.** For matchups across weight classes the layer
  adds an explicit `weight_gap` term — a penalty proportional to the pounds of
  representative-weight difference between the two fighters' divisions
  (≈0.185 logit per 10 lb to the heavier fighter). Its coefficient is a
  documented *physical prior*, not a data fit: the crossover corpus is far too
  thin and red-corner-confounded to estimate it (the red/favored corner wins
  ~93% of those bouts whether heavier or lighter). This is what makes a
  145 lb champion vs a 250 lb heavyweight land in the low single digits instead
  of the ~30% the offset-only model returned. Gaps beyond ~2 divisions are
  linear extrapolations, always flagged **speculative** — model estimates, not
  data-validated numbers.

**Backtest** (walk-forward, 2013→present, n=6,419 fights): log-loss **0.659**,
accuracy 60.1% — beats coin flip (0.693), vanilla Elo (0.685), and raw Glicko
(0.696). Calibration error (ECE) 0.034: when the model says 74%, favorites win
~72% of the time. Each multiplier was ablation-tested and kept only because it
helps (or is neutral) under that harness. Full report: `docs/backtest_report.md`;
rating sanity checks: `docs/rating_sanity_report.md`.

Recompute everything from scratch anytime with `python elo\recompute.py`
(wipes and rewrites `elo_history` + `ratings_current`; ~1 second).

## API (`api/`)

FastAPI, read-only over the DB: `/api/rankings`, `/api/fighters?search=`,
`/api/fighters/{id}`, `/api/events/upcoming`, `/api/matchup?a=&b=`.
Endpoint reference with example payloads: `api/README.md`.

## Frontend (`web/`)

Vite + React + TypeScript, no UI framework. UFC.com-style theme: near-black
`#0f0f0f`, white nav with red-underlined "UFC ELO" wordmark, Oswald condensed
type, red/blue corner coloring in comparison modules. Pages: **Rankings**
(landing — division grid with ratings in place of movement arrows), **Events**
(upcoming cards with tabbed stat comparisons, odds, predicted winner, and
win/loss rating previews), **Matchups** (any-two-fighters picker with a ranked
factor breakdown), and fighter profiles (rating ± RD, stat row, Elo-history
chart, fight history). `npm run build` outputs `web/dist`.

## Weekly refresh routine

```powershell
python data\sync.py --all
python elo\recompute.py
```

## Known limitations

- UFC 1 (Nov 1993) is absent from the upstream source; reach is missing for
  ~45% of (mostly older) fighters — handled as missing data, never guessed.
- Odds stay empty until you add a (free) `ODDS_API_KEY`.
- Pre-UFC record seeding is wired but dormant: no clean data source for
  pre-UFC records yet, so debuting fighters start at 1500 ± 350.
- P4P comparisons across eras favor deeper divisions; user-facing boards
  filter to ≥5 fights and rank conservatively where it matters.
