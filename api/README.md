# UFC Elo API (Phase 3)

FastAPI service over `data/ufc.db`. It serves the four page payloads the React
frontend (PLAN section 8) needs plus a free-form matchup endpoint. It is
**read-only**: every rating and prediction number is imported from `elo/`
(`elo.predict.predict_fight`, `elo.engine.preview_update`) — this layer never
recomputes or writes a rating.

## Run

```bash
# from the project root (so the sibling `data` / `elo` packages import)
uvicorn api.main:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs`.

On startup the app builds in-memory caches (prediction model, cross-division
offsets, per-fighter career aggregates, search index) — ~90 ms. Because the DB
is read-only from the API's side, these only go stale when `ufc.db` is rebuilt
offline. Restart the process to reload them. An operator may instead configure
`UFC_ELO_REFRESH_TOKEN` and call **`POST /api/refresh`** with that value in the
`X-Refresh-Token` header; the route is unavailable when no token is configured.

## Conventions

- All keys `snake_case`. **Nulls are present, not omitted** — a missing rating
  is `{"mu": null, "rd": null}`, a missing image is `null`.
- Every rating is `{"mu": float|null, "rd": float|null}` (display 1500/350 scale).
- Errors: **404** unknown fighter id, **422** bad/missing params (FastAPI
  validation) and `matchup` with `a == b`.
- CORS is open for any `http://localhost:<port>` / `127.0.0.1:<port>` origin
  (Vite 5173 etc.).

## Measured warm latency (local, single process)

| Endpoint | Warm |
|---|--:|
| `/api/rankings` | ~40 ms |
| `/api/events/upcoming` (8 events, 62 fights) | ~82 ms |
| `/api/fighters/{id}` | ~40 ms |
| `/api/matchup` | ~26 ms |

Both performance-critical endpoints are well under the 300 ms target.

---

## Endpoints

### `GET /api/rankings`

Latest `rankings_snapshot`, one block per division (rank 0 = champion, the rest
in order) plus the two P4P lists. Division ratings come from `ratings_current`
for **that** division; P4P entries (which have no single division) use the
fighter's primary rating.

```jsonc
{
  "snapshot_date": "2026-07-17",
  "divisions": [
    {
      "division": "Flyweight",
      "champion": {
        "fighter_id": 4121, "name": "Joshua Van", "nickname": "The Fearless",
        "image_url": "https://ufc.com/...png", "rank": 0, "movement": 0,
        "rating": { "mu": 2225.8, "rd": 156.2 }
      },
      "contenders": [
        { "fighter_id": 2998, "name": "Alexandre Pantoja", "nickname": "The Cannibal",
          "image_url": null, "rank": 1, "movement": 0,
          "rating": { "mu": 2013.3, "rd": 183.1 } }
      ]
    }
  ],
  "pound_for_pound": {
    "mens":  [ { "fighter_id": 2383, "name": "Islam Makhachev", "rank": 1, "...": "..." } ],
    "womens": [ { "...": "..." } ]
  }
}
```

Frontend notes: a snapshot row whose scraped name never resolved to a fighter
comes back with `fighter_id: null`, a plain `name`, and a null rating/image
(currently 2 such rows). `champion` can be `null` for a vacant title.

### `GET /api/fighters?search=<query>`

Typeahead: case- and accent-insensitive substring over name **and** nickname,
top 10. Word-start matches (e.g. surname "makhachev") rank ahead of interior
matches.

```jsonc
{
  "query": "makhachev",
  "results": [
    { "id": 2383, "name": "Islam Makhachev", "nickname": null,
      "division": "Welterweight", "record": "17-1-0",
      "image_url": "https://ufc.com/...png", "mu": 2597.4 }
  ]
}
```

### `GET /api/fighters/{id}`

Full profile payload. **404** if the id is unknown.

Top-level keys: `fighter` (full bio row), `record` (`{wins,losses,draws,display}`),
`ratings` (per-division `{division,mu,rd,sigma,pfp_mu,n_fights,last_fight}`,
ordered by `n_fights`), `rankings` (rank/division chips from the latest snapshot,
possibly empty), `career_stats`, `elo_timeline`, `fight_history`, `last_fight`.

```jsonc
{
  "fighter": { "id": 2383, "name": "Islam Makhachev", "nickname": null,
    "dob": "1991-...", "height_in": 70.0, "reach_in": 70.5, "stance": "Southpaw",
    "country": "Russia", "division": "Welterweight", "status": "active",
    "image_url": "https://ufc.com/...png", "style_tag": "wrestler",
    "pre_ufc_record": null },
  "record": { "wins": 17, "losses": 1, "draws": 0, "display": "17-1-0" },
  "ratings": [ { "division": "Lightweight", "mu": 2597.4, "rd": 207.7, "sigma": 0.06,
                 "pfp_mu": 2597.4, "n_fights": 17, "last_fight": "2025-01-18" } ],
  "rankings": [ { "division": "Welterweight", "rank": 0, "movement": 0 },
                { "division": "Men's Pound-for-Pound", "rank": 1, "movement": 0 } ],
  "career_stats": {
    "wins_by_ko": 3, "wins_by_sub": 8, "wins_by_decision": 6,
    "first_round_finishes": 6,
    "sig_strikes_landed": 485, "sig_strikes_attempted": 830,
    "sig_strike_accuracy": 0.5843, "sig_strikes_per_min": 2.45,
    "takedowns_landed": 41, "takedowns_attempted": 73, "takedown_accuracy": 0.5616,
    "control_time_seconds": 6088, "control_time_share": 0.5118 },
  "elo_timeline": [ { "date": "2015-05-...", "division": "Lightweight",
    "mu": 1510.2, "rd": 289.1, "opponent_id": 1234, "opponent_name": "...",
    "result": "loss" } ],
  "fight_history": [ { "fight_id": 248, "opponent_id": 4103,
    "opponent_name": "Jack Della Maddalena", "event_name": "UFC 3xx",
    "date": "2025-11-15", "result": "win", "method": "SUB",
    "method_detail": "Rear-naked choke", "round": 4, "time_seconds": 132,
    "weight_class": "Welterweight", "is_title": true } ],
  "last_fight": { "...": "same shape as a fight_history entry",
    "fighter_image_url": "https://...", "opponent_image_url": "https://..." }
}
```

`result` is from the profiled fighter's perspective: `win` / `loss` / `draw` /
`nc`. `elo_timeline` is ascending (oldest→newest) for charting; `fight_history`
is descending (newest first). `last_fight` is the newest completed fight,
repeated with both fighters' `*_image_url` for the hero card (`null` if the
fighter has no completed fights).

### `GET /api/events/upcoming`

Every `status='upcoming'` event, soonest first, fights in `card_position` order.
Each fight embeds the shared **comparison payload** (see below) plus `odds`.

```jsonc
{
  "events": [
    { "id": 775, "name": "UFC Fight Night: Du Plessis vs Usman",
      "date": "2026-07-18", "location": "...", "venue": "...",
      "fights": [
        { "fight_id": 8702, "card_position": 1, "weight_class": "Middleweight",
          "is_title": false, "is_interim_title": false, "is_main_event": true,
          "odds": null,
          "red": { "id": 1019, "name": "...", "nickname": "...", "image_url": "...",
                   "record": {"wins":0,"losses":0,"draws":0,"display":"..-..-.."},
                   "division": "Middleweight", "mu": 2399.5, "rd": 150.1 },
          "blue": { "...": "same shape" },
          "stats": { "red": { "...career comparison..." }, "blue": { "..." } },
          "prediction": {
            "prob_red": 0.6297, "prob_blue": 0.3703, "expected_score_raw": 0.71,
            "factors": [ { "name": "glicko_logit", "contribution": 0.31,
                           "description": "Glicko rating/deviation base (favours A)" } ],
            "cross_division": false, "speculative": false },
          "rating_preview": {
            "red":  { "division": "Middleweight", "current_mu": 2399.5,
                      "if_win_mu": 2469.5, "if_loss_mu": 2326.6 },
            "blue": { "..." } },
          "rating_gap": { "red_pfp_mu": 2354.5, "blue_pfp_mu": 2100.0, "gap": 254.5 } }
      ] }
  ]
}
```

`odds` is **`null` for every fight today** — the `odds` table is empty until an
API key arrives (schema is ready; `_latest_odds` returns the most-recently
fetched row once populated). `red` = fighter A for the prediction, so
`prob_red` = P(red wins). Events cards carry the **top 6** factors.

### `GET /api/matchup?a={id}&b={id}`

Any two fighters. Same comparison shape as an events fight, with the **full**
factor list and `cross_division` / `speculative` / `rating_gap` surfaced at the
top level for the cross-division note. **404** if either id is unknown, **422**
if `a == b`. `a` is the red corner; the projection is order-invariant.

```jsonc
{
  "red": { "id": 2383, "name": "Islam Makhachev", "...": "..." },
  "blue": { "id": 4121, "name": "Joshua Van", "...": "..." },
  "stats": { "red": { "..." }, "blue": { "..." } },
  "prediction": { "prob_red": 0.4351, "prob_blue": 0.5649, "expected_score_raw": 0.62,
    "factors": [ { "name": "age_delta", "contribution": -0.8056,
                   "description": "Age delta +10.0 yr (favours B)" } ],
    "cross_division": true, "speculative": true },
  "rating_preview": { "red": { "..." }, "blue": { "..." } },
  "rating_gap": { "red_pfp_mu": 2597.4, "blue_pfp_mu": 2225.8, "gap": 371.6 },
  "cross_division": true,
  "speculative": true
}
```

### Comparison stat block (`stats.red` / `stats.blue`)

Career aggregates driving the events/matchup stat tabs (all from the startup
cache):

```jsonc
{
  "wins_by_ko": 3, "wins_by_sub": 8, "wins_by_decision": 6, "wins_by_dq": 0,
  "first_round_finishes": 6, "knockdowns": 5, "sub_attempts": 12,
  "sig_strikes_landed": 485, "sig_strikes_attempted": 830,
  "sig_strike_accuracy": 0.5843, "sig_strikes_per_min": 2.45,
  "takedowns_landed": 41, "takedowns_attempted": 73, "takedown_accuracy": 0.5616,
  "control_time_seconds": 6088, "control_time_share": 0.5118,
  "total_fight_seconds": 11898, "fights_with_stats": 17
}
```

Accuracy/share fields are `0-1` fractions (`null` when the denominator is 0);
`sig_strikes_per_min` is landed per minute of fight time (`null` if no timed
fights). Fight duration is reconstructed as `(round-1)*300 + time_seconds`.

### `GET /api/health` · protected `POST /api/refresh`

`health` returns `{status, caches_loaded, career_fighters_cached,
search_index_size}`. `refresh` rebuilds the caches from the current DB and
requires the configured `X-Refresh-Token`.

---

## Frontend must handle

- **Empty odds.** `fight.odds` is `null` for all fights until the odds API key
  is configured. Show "odds unavailable", not a crash.
- **Debutants → null prediction/preview.** ~15 fighters on upcoming cards have
  no `ratings_current` row; those fights return `prediction: null`,
  `rating_preview: null`, `rating_gap.gap: null`, and null `mu`/`rd` on the
  corner. 13 of the 62 upcoming fights are affected today.
- **Missing images.** Only 134 fighters have an `image_url`; the rest are `null`.
  Plan for a placeholder cutout everywhere an image is shown.
- **Unresolved ranking names.** A few `rankings` entries have `fighter_id: null`
  (scraped name didn't map to a fighter) — render the `name`, no profile link.
- **`speculative` badge.** True for cross-division matchups or thin/uncertain
  ratings; show the "speculative comparison" note when set.
```
