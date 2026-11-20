# Lead explorer benchmark

Generated 2026-11-20 10:10 UTC by `leads ui bench`. Throwaway database with **3,000,000 businesses** (the 233,798 real kept rows cloned to scale, same category/state/flag mix). Businesses table incl. indexes: 4.35 GB. Times are end-to-end API calls (FastAPI + Postgres 16, same machine), 7 runs each.

Target: p95 under 300 ms.

| call | p50 ms | p95 ms | ok |
|---|---|---|---|
| Default list (score sort) | 10 | 16 | yes |
| Category + state, name sort | 10 | 12 | yes |
| Search 'rooter' | 106 | 132 | yes |
| Search 'smith' (common) | 22 | 50 | yes |
| Callable now | 14 | 129 | yes |
| Flag filter, locations sort | 10 | 12 | yes |
| Rare: WY + has email | 10 | 11 | yes |
| State/city sort | 9 | 10 | yes |
| Deep page (cursor, page 50) | 11 | 11 | yes |
| Count, no filter | 10 | 13 | yes |
| Count, dental + email | 78 | 84 | yes |
| Count, WY | 12 | 20 | yes |
| Facets, no filter | 232 | 262 | yes |
| Facets, hvac (state+subcategory) | 64 | 68 | yes |
| Lead detail | 12 | 20 | yes |

Notes: counts are exact up to 50,000 matches and a labelled planner estimate ("≈") beyond that.
Counts and facets are cached for 60 s per filter (cleared on suppress); every timed call here is
measured on a fresh app with an empty cache, so the numbers are uncached.
Deep page = average time per page while walking 49 pages with the cursor.
