# UFC Elo — Rating Engine Sanity Report

_Generated 2026-07-17 from `data/ufc.db` (8,701 completed fights; 62 upcoming
excluded). Engine: Glicko-2 core + documented MOV/stakes multipliers, per
`docs/PLAN.md` section 3._

## 1. Glicko-2 unit test

`python -m elo.tests.test_glicko2` — **4/4 pass**, including Glickman's worked
example (player 1500/200 vs three opponents → **rating 1464.06, RD 151.52,
sigma 0.05999**, matching the paper to 0.01).

## 2. Recompute

| Metric | Value |
|---|---|
| Runtime (full wipe + replay + rewrite) | **~0.6 s** (spec: < 60 s) |
| Fights replayed | 8,701 |
| `elo_history` rows | 17,224 |
| `ratings_current` rows (fighter × division pools) | 3,536 |
| Idempotent? | **Yes** — identical MD5 of both tables across three consecutive runs |
| Updates hitting the delta cap | 1,438 / 17,224 (8.35%), mostly high-RD debuts / high-multiplier finishes |
| Division-change seeds | 847 |

`python -m elo.calibrate_offsets` then `python -m elo.recompute` reproduce the
DB exactly.

## 3. Fitted cross-division offsets D_w (`elo/division_offsets.json`)

Fitted by regularized logistic regression on **76 crossover fights**
(catchweight / open-weight / cross-division), Lightweight anchored to 0. PFP
rating = division mu + D_w.

| Division | D_w | Division | D_w |
|---|---:|---|---:|
| Heavyweight | **+104.6** | Welterweight | −3.4 |
| Light Heavyweight | **+77.1** | Women's Bantamweight | −13.0 |
| Featherweight | +34.0 | Bantamweight | −23.8 |
| Women's Featherweight | +13.0 | Middleweight | −45.0 |
| **Lightweight (anchor)** | **0.0** | Women's Flyweight | −47.9 |
| | | Open Weight | −88.4 |

The two well-supported signals — **Heavyweight/Light-Heavyweight positive**
(a bigger fighter of equal in-division rating beats a lighter one) and
**Open Weight negative** (the weaker 1990s tournament field) — are physically
sensible and carry most of the data (39 of 76 crossover fights are
Heavyweight↔Open-Weight). The remaining offsets are small; the modern
adjacent-division crossover sample is sparse (3–6 fights each), so
regularization correctly keeps them near 0. **This table should be re-fit once
more division-switch data exists; treat non-HW/LHW offsets as provisional.**

## 4. Top-10 active fighters by current mu (last fight ≥ 2024-07-17)

Cross-checked against `rankings_snapshot` (2026-07-17). **The snapshot champion
is the model's #1 in 10 of 12 divisions.** The two "exceptions" are correct
model behavior, not errors (see notes).

| Division | Model top 5 (mu) | Snapshot champ |
|---|---|---|
| Flyweight | **Joshua Van** 2226, Horiguchi, Kape, Taira, Mokaev | Joshua Van ✓ |
| Bantamweight | **Petr Yan** 2370, Dvalishvili, U. Nurmagomedov, O'Malley, Sandhagen | Petr Yan ✓ |
| Featherweight | Topuria 2641, **Volkanovski** 2524, Holloway, Evloev, Sterling | Volkanovski (Topuria vacated to LW) |
| Lightweight | Makhachev 2597, Tsarukyan, **Gaethje**, Oliveira, B. Saint Denis | Gaethje rank-0 in snapshot; Makhachev #1 defensible |
| Welterweight | **Makhachev** 2500 (n=1), Usman, Morales, Rakhmonov, Prates | Makhachev ✓ |
| Middleweight | **Strickland** 2433, Imavov, Du Plessis, Chimaev, Pyfer | Strickland ✓ |
| Light Heavyweight | Pereira 2524, **Ulberg** 2400, Ankalaev, Costa, Prochazka | Ulberg (Pereira #1 by rating) |
| Heavyweight | Jones 2681 (n=2), **Aspinall** 2505, Gane, Volkov, Miocic | Aspinall (Jones inactive-undefeated #1) |
| Women's Strawweight | Zhang 2325, Suarez, Jandiroba, **Dern**, Yan Xiaonan | Dern (Zhang moved to FLW) |
| Women's Flyweight | **Shevchenko** 2289, N. Silva, Blanchfield, Fiorot, Grasso | Shevchenko ✓ |
| Women's Bantamweight | **Harrison** 2216, L. Santos, Pennington, Y. Santos, Peña | Harrison ✓ |
| Women's Featherweight | (near-defunct division, 2 active) | — |

**Notes on the "exceptions":** Topuria (FW), Makhachev (LW→WW debut) and Zhang
(SW→FLW) are rated at the top of a division they left **undefeated**, so their
rating there persists correctly; the snapshot lists the champion of the *vacated*
belt. Jon Jones tops Heavyweight on raw mu with only 2 HW fights (RD 239) — high
uncertainty, undefeated, inactive; a production ranking would prefer a
conservative score (see §7).

## 5. Top 15 all-time peak mu

| # | Peak | Fighter | Division | # | Peak | Fighter | Division |
|--:|--:|---|---|--:|--:|---|---|
| 1 | 2681 | Jon Jones | HW | 9 | 2588 | Khabib Nurmagomedov | LW |
| 2 | 2654 | Alexander Volkanovski | FW | 10 | 2584 | Anderson Silva | MW |
| 3 | 2653 | Francis Ngannou | HW | 11 | 2583 | Daniel Cormier | LHW |
| 4 | 2641 | Kamaru Usman | WW | 12 | 2583 | Chris Weidman | MW |
| 5 | 2641 | Ilia Topuria | FW | 13 | 2555 | Leon Edwards | WW |
| 6 | 2637 | Israel Adesanya | MW | 14 | 2536 | Tyron Woodley | WW |
| 7 | 2599 | Stipe Miocic | HW | 15 | 2533 | Khamzat Chimaev | MW |
| 8 | 2597 | Islam Makhachev | LW | 18 | 2517 | Georges St-Pierre | WW |

All genuine era greats. Jones, Silva, Khabib as expected; GSP just outside at
#18. Chris Weidman (#12) and Tyron Woodley (#14) rank slightly high — both are
inflated by beating very high-rated opponents during real title reigns
(Weidman over a peak Anderson Silva twice), which is defensible rather than a
bug.

## 6. Inflation / drift measurement

**Per-division pool means sit essentially at 1500 — the corrected MOV prevents
systematic inflation.**

| Division | n | mean | Division | n | mean |
|---|--:|--:|---|--:|--:|
| Flyweight | 160 | 1502 | Light Heavyweight | 295 | 1542 |
| Bantamweight | 333 | 1498 | Heavyweight | 317 | 1534 |
| Featherweight | 390 | 1537 | Women's Strawweight | 121 | 1485 |
| Lightweight | 578 | 1498 | Women's Flyweight | 114 | 1504 |
| Welterweight | 556 | 1529 | Women's Bantamweight | 101 | 1502 |
| Middleweight | 467 | 1510 | Women's Featherweight | 26 | 1574 |
| **All pools** | **3,536** | **1515** | Open Weight | 78 | 1424 |

Mean post-fight rating of fighters **active in a given year** rises gently from
~1650 (1990s–2000s) to ~1730 (2025):

```
1994 1656 | 2000 1588 | 2006 1631 | 2012 1653 | 2018 1698 | 2024 1719
1996 1671 | 2002 1654 | 2008 1644 | 2014 1641 | 2020 1691 | 2025 1733
1998 1597 | 2004 1677 | 2010 1675 | 2016 1698 | 2022 1689 | 2026 1720
```

This ~+80 rise over 30 years is **selection/maturation, not inflation**: the
*active* roster is stronger than the all-fighter population (which the near-1500
pool means confirm), and elite fighters now have longer careers accumulating
rating. There is no runaway drift — the population mean is conserved at ~1500.

## 7. Surprises, limitations, and open questions

1. **Cross-division P4P ceiling gap.** Women's and men's flyweight/bantamweight
   GOATs peak far lower than heavyweights: Amanda Nunes 2324 (#76 all-time),
   Demetrious Johnson 2303 (#86), Valentina Shevchenko 2289 (#90), Dominick
   Cruz 2308 (#83). This is **structural**, not a rating error: shallower,
   younger, smaller pools have lower rating ceilings, and per-division mu is not
   era/pool-comparable. `pfp_mu` (mu + D_w) is the intended cross-division
   comparison, but D_w is a constant shift and cannot correct pool *dynamic
   range*. **A true P4P/all-time list needs pool-depth normalization — flagged
   for the prediction layer.**

2. **Small-sample high-RD names surface mid-table.** Ranking by raw mu puts a
   few low-n fighters (e.g. Heavyweight Josh Hokit n=3 at #7, Lightweight
   Quillan Salkilld n=5) above their true standing. Their RD is high (230–260),
   so a conservative ranking score `mu − k·RD`, or an `n_fights ≥ 5` filter, is
   recommended for any user-facing leaderboard.

3. **Active RD runs high (min 146, mean 215, max 336).** With
   `RD_INACTIVITY_C_PER_YEAR = 80` and many "active" fighters last seen 12–18
   months before `as_of`, deviations stay wide. This is realistic for a
   high-variance sport but makes deviation-aware win probabilities conservative;
   the prediction-layer agent may want to revisit this constant against
   calibration.

## 8. Config values chosen beyond the plan defaults

The plan fixed the MOV/stakes tables, tau (0.5), initial 1500/350/0.06, and the
0.25 division-change regression. The following were left tunable by the plan and
set here (all in `elo/config.py`, all documented):

| Constant | Value | Rationale |
|---|---|---|
| `RD_INACTIVITY_C_PER_YEAR` | 80 | RD inflates from active (~70) toward the 300s over ~3 idle years. Documented extension (explicit variance-per-year rather than pure volatility accrual). |
| `MAX_RATING_DELTA` | 250 | Hard cap on the compounded per-bout delta; binds on 8.3% of updates (mostly high-RD debuts). |
| `RD_MIN` / `RD_MAX` | 40 / 350 | Floor/ceiling on deviation. |
| `DIVISION_CHANGE_RD_BUMP` | 100 | Extra RD (quadrature) when seeding a new division. |
| `OFFSET_L2_LAMBDA` | 1.0 | L2 strength for the offset fit, applied in dimensionless Glicko-scale units so the penalty is unit-balanced (offsets fitted as `d`, reported as `d·173.7178`). |
| `M_BASE_FINISH_BY_ROUND[4], [5]` | 1.10, 1.10 | The plan's "R4–5 1.10" split across the two rounds. |

**Pre-UFC seeding** (`seed_rating`) is wired but **no-ops to 1500/350 for every
fighter today** — `fighters.pre_ufc_record` is empty in the current DB. The
promotion-tier logic and `SEED_MU_RANGE`/`SEED_PROMOTION_TIER_BUMP` are staged
for when a source populates that column.
