# Data refresh: November 17, 2026

The checked-in `data/ufc.db` now includes completed fights through UFC 334:
Gane vs. Hokit on November 14, 2026. The previous result cutoff was September 19.

| Event date | Event | Completed fights added |
| --- | --- | ---: |
| 2026-09-26 | Rosas Jr. vs. Barcelos | 12 |
| 2026-10-03 | UFC 332: Silva vs. Wang | 14 |
| 2026-10-10 | Allen vs. Duncan | 12 |
| 2026-10-17 | Buckley vs. Malott | 13 |
| 2026-10-24 | UFC 333: Volkanovski vs. Evloev | 14 |
| 2026-10-31 | Moicano vs. Nolan | 12 |
| 2026-11-07 | Cortes Acosta vs. Walker | 14 |
| 2026-11-14 | UFC 334: Gane vs. Hokit | 13 |
| **Total** | | **104** |

Completed results and per-fighter statistics came from the project's existing
[Greco1899 export](https://github.com/Greco1899/scrape_ufc_stats), downloaded with
`python data/ingest/ingest_historical.py`. Direct UFC Stats scraping returned its
browser challenge, so the existing historical ingestion path supplied the update.
The newest card is also listed on the
[official UFC results page](https://www.ufc.com/news/polymarket-ufc-334-gane-vs-hokit-results).

`python elo/recompute.py --as-of 2026-11-17` replayed 9,011 fights, producing
17,842 rating-history rows and 3,657 current division ratings. All 104 new bouts
have results, two fighter-stat rows each, and two rating-history rows each.
Prediction coefficients were retained; this refresh updates the underlying
fight history, fighter statistics, and ratings.

`python data/sync.py --events --rankings --max-completed 0` refreshed eight
upcoming cards and official rankings in 13 divisions. Eight old upcoming event
listings were closed using the existing sync behavior. Source-specific historical
event placeholders remain as designed by the sync layer.

One official ranking name contained a replacement character in Lone'er
Kavanagh's name. Its fighter link was resolved to the unique existing Kavanagh
profile (fighter 1973), preserving the raw source name and recording provenance.

Validation: SQLite integrity check passed, no foreign-key violations, 12 API and
scraper tests passed, and the ingestion test plus four rating-engine tests passed.
API tests required local socket access outside the sandbox; the ingestion test
used an explicit workspace temporary directory because the default pytest temp
folder was inaccessible.
