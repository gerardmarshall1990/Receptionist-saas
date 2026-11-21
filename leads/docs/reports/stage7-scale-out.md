# Stage 7 — scale-out (in progress)

User, 2026-11-21: "keep going until done as per my specs." The database build goes ahead on open data at $0;
calling at scale still waits for the pilot1 readout.

## Universe: all 7 categories, US (Overture 2026-11-18.0)

930,691 raw listings -> **725,895 kept** (chains, duplicates, exclusions removed), plus 117 older rows kept for history.

| Category | Businesses | With website | With phone |
|---|---:|---:|---:|
| Auto repair | 253,570 | 133,510 | 246,268 |
| Law firms | 144,479 | 119,581 | 138,825 |
| Dental | 138,977 | 115,645 | 136,071 |
| Roofing | 56,802 | 45,992 | 53,830 |
| HVAC | 55,169 | 43,274 | 53,516 |
| Plumbing | 39,292 | 29,241 | 38,318 |
| Electrical | 37,723 | 27,577 | 36,620 |

The spec's 3M is not reachable with these 7 US categories: about 726k businesses here, roughly 1M with
CA/AU/UK. Getting there needs more categories or countries (open question for the user).

## Crawl (best first)

About 447k new websites queued. Priority = category value weight + usable unshared phone + independent. A
site without a usable phone goes last because it can never become an exportable lead. So law firms (0.95) come
first, then roofing (0.90), then HVAC and dental (0.85).

- Speed: ~10k sites per 20-minute round with 2 workers, plus incremental extraction.
- Disk: saved pages ~66 KB per site. This container stops at 6 GB free, which is roughly the best 200k sites.
  The rest needs the lasting host in `docs/DEPLOY.md`.

## Fixes made while scaling

1. **Claims stalled:** the claim query scanned the whole 447k-row queue on every claim, because the new index
   never got created (name collision with migration 004). Fixed (migration 011, two-step claim): ~0.1 ms per claim.
2. **Extraction cost:** extraction re-read every saved page each run. It's now incremental and batched, with flat memory.
3. **Reload protection:** the 7-category reload would have deleted rows that pilot1 references. They're kept and
   flagged `not_in_latest_build`, which the gate blocks.
4. **Accuracy on law sites:** two hand-graded samples (extractor v8, v9); see `stage2-precision.md`.
5. **Openers:** the law template no longer assumes criminal defence.

## Lead list

Published as a private page, rebuilt from each gated export (`scripts/build_lead_list.py`):
https://claude.ai/artifact/JvbTF3fXVsjukmnZmMDYZG

| Snapshot | Leads passing the gate | Tier A | With strong evidence |
|---|---:|---:|---:|
| Start (3 categories) | 3,670 | 691 | — |
| Round 2 (v8) | 8,028 | 1,245 | 883 |

## Still open

- Accuracy samples for roofing, electrical and auto: there aren't enough crawled sites yet.
- Lasting host, so the crawl can go past the container's disk (`docs/DEPLOY.md`).
- 3M target vs 7 categories.
