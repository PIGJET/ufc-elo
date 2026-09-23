# UFC Elo

> A full-stack combat-sports analytics platform combining historical ratings, fighter profiles, event data, and matchup probabilities.

![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React_19-20232A?style=flat-square&logo=react&logoColor=61DAFB)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)

![UFC Elo rankings dashboard](docs/demo.png)

## Results

The prediction layer was evaluated out of sample on **6,419 fights from 2013
onward**. For each calendar year, the model was refit using only earlier fights.

| Model | Log loss | Brier score | Accuracy |
| --- | ---: | ---: | ---: |
| Coin flip | 0.6931 | 0.2500 | 50.0% |
| Vanilla Elo | 0.6845 | 0.2457 | 55.7% |
| UFC Elo prediction layer | **0.6592** | **0.2332** | **60.1%** |

Expected calibration error is **0.0336**. See the
[backtest report](docs/backtest_report.md) and
[optimization report](docs/optimization_report.md) for the methodology,
ablations, calibration buckets, and known limitations.

## Overview

UFC Elo replays the promotion's fight history through a division-aware Glicko-2 engine, then layers matchup features on top to produce calibrated win probabilities. The result is a searchable web product with rankings, fighter profiles, rating histories, upcoming events, odds, and head-to-head analysis.

## What makes the model interesting

- Maintains rating, uncertainty, and volatility separately for each division.
- Expands uncertainty during layoffs instead of silently decaying a fighter's skill.
- Adjusts rating movement for finish type, round, upset magnitude, and fight stakes.
- Transfers fighters between divisions using fitted, monotonic division offsets.
- Keeps matchup features separate from the underlying rating engine.
- Fits age, inactivity, stance, style, rematch, and physical attributes using walk-forward evaluation.
- Records data provenance and preserves conflicting source values for auditability.

![Walk-forward calibration report](docs/calibration.png)

## Product surface

- Official and model-based division rankings
- Fighter search, profiles, bios, and rating-history charts
- Upcoming-event cards with predictions and available betting odds
- Any-fighter matchup comparison with an explainable probability breakdown
- FastAPI endpoints for rankings, fighters, events, and matchups

## Architecture

| Directory | Responsibility |
| --- | --- |
| `data/` | SQLite schema, ingestion, scraping, synchronization, and quality reports |
| `elo/` | Glicko-2 engine, features, calibration, backtests, and prediction model |
| `api/` | FastAPI application, caches, serialization, and route modules |
| `web/` | React/TypeScript interface and data visualizations |
| `docs/` | Model, optimization, calibration, and data-quality reports |

## Run locally

```powershell
pip install -r requirements.txt
cd web; npm install; cd ..

python data\sync.py --all
python elo\recompute.py
```

Start the API and frontend in separate terminals:

```powershell
python -m uvicorn api.main:app --port 8000
```

```powershell
cd web
npm run dev
```

Odds enrichment is optional and requires `ODDS_API_KEY`. The checked-in SQLite database lets the application run without performing a fresh ingest.

## Verify the release

```powershell
pip install -r requirements-dev.txt
pytest -q

cd web
npm ci
npm run lint
npm run build
```

GitHub Actions runs the same backend and frontend checks on every pull request.

## Deployment

`render.yaml` builds the React client and serves it from the FastAPI process as
one Render service. The blueprint includes a health check at `/api/health`.

`POST /api/refresh` is disabled by default. To allow a trusted operator to
rebuild in-memory caches without restarting the service, set
`UFC_ELO_REFRESH_TOKEN` and send the same value in the `X-Refresh-Token` header.

## Limitations

- Betting odds are not included in the current database, so the model has not
  yet been benchmarked against the market.
- Cross-division predictions use a documented physical prior and are marked
  speculative; large weight gaps are extrapolations, not directly validated.
- Historical physical attributes are incomplete and are imputed with explicit
  missing-data indicators.
- The checked-in database is a reproducible snapshot, not a promise of live
  rankings. Refresh it with `data/sync.py` before publishing time-sensitive data.
