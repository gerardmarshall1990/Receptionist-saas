# Lead explorer benchmark

Generated 2026-11-19 14:08 UTC by `leads ui bench`. Throwaway database with **3,000,000 businesses** (the 233,798 real kept rows cloned to scale, same category/state/flag mix). Businesses table incl. indexes: 4.23 GB. Times are end-to-end API calls (FastAPI + Postgres 16, same machine), 7 runs each.

Target: p95 under 300 ms.

| call | p50 ms | p95 ms | ok |
|---|---|---|---|
| Default list (score sort) | 10 | 15 | yes |
| Category + state, name sort | 9 | 14 | yes |
| Search 'rooter' | 95 | 122 | yes |
| Search 'smith' (common) | 21 | 47 | yes |
| Callable now | 17 | 117 | yes |
| Flag filter, locations sort | 10 | 12 | yes |
| Rare: WY + has email | 7 | 10 | yes |
| State/city sort | 7 | 8 | yes |
| Deep page (cursor, page 50) | 9 | 10 | yes |
| Count, no filter | 9 | 12 | yes |
| Count, dental + email | 70 | 82 | yes |
| Count, WY | 8 | 11 | yes |
| Facets, no filter | 155 | 181 | yes |
| Facets, hvac (state+subcategory) | 60 | 83 | yes |
| Lead detail | 9 | 12 | yes |

Notes: counts are exact up to 50,000 matches and a labelled planner estimate ("≈") beyond that.
Counts and facets are cached for 60 s per filter (cleared on suppress); every timed call here is
measured on a fresh app with an empty cache, so the numbers are uncached.
Deep page = average time per page while walking 49 pages with the cursor.
