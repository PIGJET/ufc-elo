# UFC Elo — Data Quality Report

_Generated 2026-07-17T13:53:10 from `data/ufc.db`._

Source dataset: **Greco1899/scrape_ufc_stats** (complete ufcstats.com export). Raw CSVs archived in `data/raw/`.

## Row counts

| Table | Rows |
|---|---:|
| fighters | 4,497 |
| events | 774 |
| fights | 8,701 |
| fight_stats | 17,358 |
| odds | 0 |
| rankings_snapshot | 0 |
| elo_history | 0 |
| provenance | 66,707 |

## Fights per year (coverage gaps)

| Year | Events | Fights |
|---|---:|---:|
| 1994 | 3 | 31 |
| 1995 | 4 | 40 |
| 1996 | 5 | 43 |
| 1997 | 5 | 41 |
| 1998 | 3 | 25 |
| 1999 | 6 | 44 |
| 2000 | 6 | 43 |
| 2001 | 5 | 40 |
| 2002 | 7 | 53 |
| 2003 | 5 | 41 |
| 2004 | 5 | 39 |
| 2005 | 10 | 80 |
| 2006 | 18 | 158 |
| 2007 | 19 | 171 |
| 2008 | 20 | 201 |
| 2009 | 20 | 215 |
| 2010 | 24 | 253 |
| 2011 | 27 | 300 |
| 2012 | 31 | 341 |
| 2013 | 33 | 386 |
| 2014 | 46 | 503 |
| 2015 | 41 | 473 |
| 2016 | 41 | 493 |
| 2017 | 39 | 457 |
| 2018 | 39 | 474 |
| 2019 | 42 | 516 |
| 2020 | 41 | 456 |
| 2021 | 43 | 509 |
| 2022 | 42 | 511 |
| 2023 | 43 | 520 |
| 2024 | 42 | 517 |
| 2025 | 43 | 522 |
| 2026 | 16 | 205 |

## Missing / null rates for key fields

| Entity | Field | Missing | Total | % missing |
|---|---|---:|---:|---:|
| fighters | reach_in | 2,018 | 4,497 | 44.9% |
| fighters | height_in | 396 | 4,497 | 8.8% |
| fighters | dob | 768 | 4,497 | 17.1% |
| fighters | stance | 911 | 4,497 | 20.3% |
| fighters | ufcstats_id | 1 | 4,497 | 0.0% |
| fights | method | 0 | 8,701 | 0.0% |
| fights | round | 0 | 8,701 | 0.0% |
| fights | time_seconds | 0 | 8,701 | 0.0% |
| fights | weight_class | 0 | 8,701 | 0.0% |
| fights | winner_id (decisive only) | 0 | 8,547 | 0.0% |
| fights | scheduled_rounds | 31 | 8,701 | 0.4% |

## Fight-stats coverage

- Fights with at least one stat row: **8,679 / 8,701** (99.7%).
- Fights with stats for **both** fighters: **8,679**.
- Stat rows missing control time (pre-2010ish, not tracked): **360 / 17,358**.

## Duplicate-name fighters

Distinct fighters sharing a display name (bout-string name matching resolves these deterministically to the lowest ufcstats id — flagged for the scraper to reconcile):

| Name | Count | ufcstats ids |
|---|---:|---|
| Bruno Silva | 2 | 294aa73dbf37d281, 12ebd7d157e91701 |
| Jean Silva | 2 | 9211aae062b799d6, 52ef95b5860fb28c |
| Joey Gomez | 2 | 0778f94eb5d588a5, 3a28e1e641366308 |
| Michael McDonald | 2 | d52ef694108f8235, d0314416a7f26527 |
| Mike Davis | 2 | c8661e204c66f325, fb3e61720be4690c |
| Tony Johnson | 2 | 3641a0d117e9bc6c, a45bab49951a45cd |
| Victor Valenzuela | 2 | de277a4abcfeea46, 078695e385ec2f57 |

## Orphan references (referential integrity)

| Check | Count |
|---|---:|
| fights -> missing event | 0 |
| fights -> missing red fighter | 0 |
| fights -> missing blue fighter | 0 |
| fights -> winner not a participant | 0 |
| fight_stats -> missing fight | 0 |
| fight_stats -> missing fighter | 0 |
| fight_stats -> fighter not in its fight | 0 |

## Method / result vocabulary

Fights method values present (should all be in the schema vocab KO/TKO, SUB, U-DEC, S-DEC, M-DEC, DQ, NC):

| method | count | valid? |
|---|---:|---|
| U-DEC | 3135 | yes |
| KO/TKO | 2837 | yes |
| SUB | 1685 | yes |
| S-DEC | 825 | yes |
| M-DEC | 104 | yes |
| NC | 92 | yes |
| DQ | 23 | yes |

| result_current | count | valid? |
|---|---:|---|
| win | 8524 | yes |
| nc | 89 | yes |
| draw | 65 | yes |
| dq | 23 | yes |

## Ground-truth spot checks

- Total events: **774** (expected 700+). Date range **1994-03-11 -> 2026-05-16**.
- Miocic/Ngannou @ UFC 220: Miocic vs. Ngannou (2018-01-20): Stipe Miocic by U-DEC R5.
- Miocic/Ngannou @ UFC 260: Miocic vs. Ngannou (2021-03-27): Francis Ngannou by KO/TKO R2.
- Nunes/Rousey @ UFC 207: Nunes vs. Rousey (2016-12-30): Amanda Nunes by KO/TKO.

## Known gaps & caveats

- **UFC 1 (Nov 1993) is absent from the source export** — ufcstats.com's event listing that this scrape mirrors starts at UFC 2. Earliest event here is UFC 2 (1994-03-11). The live ufcstats scraper should backfill UFC 1.
- Round-by-round **control time is not recorded for older fights** (ufcstats only began tracking it ~2010); such stat rows have `control_time_seconds` NULL.
- Some earliest events have **no per-fight stats** at all in the source.
- `catchweight_lbs` is NULL: the source records the bout as 'Catch Weight' but not the contracted poundage.
- `country`, `division`, `image_url`, `ufc_slug`, `style_tag`, `pre_ufc_record`, `leg_reach_in` are not in this source and are left for ufc.com / derived stages.
