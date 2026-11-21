# Stage 7 — scale-out (this container: done at the disk floor)

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

- Speed: ~600 sites/min crawled by 2 continuous workers; extraction runs in parallel (~3,000 sites/min)
  every 20 minutes without pausing the crawl (`scripts/pipeline.sh`).
- Disk: saved pages ~66 KB per site. This container stops at 6 GB free, which is roughly the best 200k sites.
  The rest needs the lasting host in `docs/DEPLOY.md`.

## Fixes made while scaling

1. **Claims stalled:** the claim query scanned the whole 447k-row queue on every claim, because the new index
   never got created (name collision with migration 004). Fixed (migration 011, two-step claim): ~0.1 ms per claim.
2. **Extraction cost:** extraction re-read every saved page each run. It's now incremental and batched, with flat memory.
3. **Reload protection:** the 7-category reload would have deleted rows that pilot1 references. They're kept and
   flagged `not_in_latest_build`, which the gate blocks.
4. **Accuracy on law sites:** two hand-graded samples (extractor v8, v9); see `stage2-precision.md`.
5. **Openers:** the law template no longer assumes criminal defence. Since v10 there's no opener when the listing's
   name isn't on its website (0.6% of passing leads), so a caller never greets a firm by the wrong name.

## Lead list

Published as a private page, rebuilt from each gated export (`scripts/build_lead_list.py`):
https://claude.ai/artifact/JvbTF3fXVsjukmnZmMDYZG

| Snapshot | Leads passing the gate | Tier A | With strong evidence |
|---|---:|---:|---:|
| Start (3 categories) | 3,670 | 691 | — |
| Round 2 (v8) | 8,028 | 1,245 | 883 |
| 13:17 UTC (v9, ~50k sites crawled) | 10,203 | 1,516 | 1,158 |
| 13:49 UTC (after a container restart; pipeline resumed) | 11,422 | 1,652 | 1,302 |
| 15:15 UTC | 12,629 | 1,798 | 1,448 |
| 15:42 UTC | 15,323 | 2,128 | 1,784 |
| 16:11 UTC | 17,887 | 2,445 | 2,086 |
| 17:03 UTC (v10) | 20,664 | 2,797 | 2,428 |
| 18:04 UTC (v11; roofing arriving: 3,018 roofing leads) | 26,219 | 3,693 | 2,904 |
| 18:30 UTC (roofing 8,474) | 31,678 | 4,684 | 3,142 |
| 19:40 UTC (v12; HVAC and dental arriving) | 34,914 | 5,262 | 3,346 |
| 20:01 UTC (dental 5,396, HVAC 3,540) | 40,217 | 6,194 | 3,850 |
| 20:29 UTC (dental 7,979, HVAC 4,083) | 43,384 | 6,763 | 4,204 |
| 20:55 UTC (dental 10,082, HVAC 4,698) | 46,172 | 7,279 | 4,505 |
| **21:23 UTC, final in this container** | **49,514** | **7,869** | **4,833** |

## Why the crawl keeps pausing here

This cloud container is reclaimed a few minutes after the session goes idle; Postgres and the pipeline stop
with it. For example, after the 13:50 check-in the last crawl update was 13:54 and the container came back at 15:06.
Nothing is lost (pages, queue and database survive; interrupted sites go back to `pending`), but the crawl only
advances while the session is active. That is the strongest reason for the lasting host in `docs/DEPLOY.md`.

## Result in this container

The crawl stopped at the disk floor (6 GB free; saved pages 16 GB) after a final refresh.

**Final list: 49,514 leads pass the compliance gate.** 7,869 are tier A and 4,833 have strong evidence
(voicemail on their own site, hiring for the phones, or phone-only booking). 32,942 have an email from
their own site and 15,545 have an opener. The list is published at
https://claude.ai/artifact/JvbTF3fXVsjukmnZmMDYZG.

| Category | Leads passing | Sites crawled | Sites still queued |
|---|---:|---:|---:|
| Law firms | 19,853 | 102,829 | 4,484 |
| Dental | 12,288 | 50,489 | 51,309 |
| Roofing / restoration | 10,269 | 38,781 | 2,500 |
| HVAC | 5,744 | 23,869 | 15,846 |
| Plumbing | 1,351 | 6,001 | 20,986 |
| Electrical | 6 | 128 | 25,653 |
| Auto repair | 3 | 225 | 120,655 |

- **Crawled:** 221,548 sites (140,740 done, plus errors, blocks, robots refusals and offsite).
- **Still queued:** 240,795, mostly auto repair (lowest value weight, so last).
- **Rules:** extractor v12, after six rounds of hand grading on law, roofing and HVAC/dental.

## To finish on a lasting host (`docs/DEPLOY.md`)

1. Move the database (or rebuild it and `experiment restore pilot1`) and keep the crawl queue.
2. Run `scripts/pipeline.sh` until the queue is empty: about 240k sites, roughly 8 hours at ~500 sites/min.
3. Grade electrical and auto repair samples once each has 300+ crawled sites (signal-change procedure).
4. Back up Postgres daily (the suppression list lives there).

## Still open

- Accuracy samples for electrical and auto: there aren't enough crawled sites yet (roofing graded, v11).
- Lasting host, so the crawl can go past the container's disk (`docs/DEPLOY.md`).
- 3M target vs 7 categories.
