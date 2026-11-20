# Stage 4 — rule-based scoring, pilot (scoring v3, extractor v6)

Model `rules-v3-3f184a99c4b1d4c2`. Run: `leads score run`, which scored 233,798 businesses in ~11 s (COPY + MERGE).
**$0**: no LLM and no paid API. The scorer is idempotent, so re-running it overwrites the scores and keeps reasons.

## How a score is built

`total = 0.35 pain + 0.25 value + 0.25 reachability + 0.15 fit`. Reachability or fit at 0 sets the total to 0 (a dead lead).
Each dimension is built from items that are stored in `scores.components` and shown in the explorer under
"How this score was built" (screenshot: `docs/screenshots/explorer-score.png`).

| Dimension | Built from |
|---|---|
| Pain | evidence signal weights (voicemail on own site 10, hiring front desk 8, phone-only booking 6, limited hours 3, single number 2, 24/7 claim 2, no booking / no chat 1), scaled so 12 points = 100 |
| Value | category value weight x 80, + pricing published 10, + multiple locations 15 |
| Reachability | phone status (likely 70, unverified 45, none/invalid 0) + email on own site 15; minus 15 if the listed phone isn't on their site, minus 10 if the phone is shared |
| Fit | site works 55 / unknown (blocked) 45 / no site 35, + owner-operated 25; minus 30 "a real person always answers", 25 website mismatch, 15 name repeated, 10 generic name, 5 thin homepage |
| Disqualified (fit 0) | chain, answering service, franchise disclaimer, parked site |

Tiers: A >= 70, B >= 60, C >= 40, D below. A moved from 80 to 70 because the 95th percentile was 69.7 (see DECISIONS).

## Results

Only the 19,657 businesses whose website was crawled can score above D: without crawl evidence, pain is 0.

| | A | B | C | D |
|---|---:|---:|---:|---:|
| Crawled (19,657) | **771** (3.9%) | 3,257 | 8,539 | 7,090 |
| Not crawled (214,141) | 0 | 0 | 0 | 214,141 |

| Category (crawled) | n | A | B | C | D | 95th pct |
|---|---:|---:|---:|---:|---:|---:|
| Dental | 6,933 | 246 | 1,102 | 3,489 | 2,096 | 68.6 |
| HVAC | 5,729 | 270 | 978 | 2,274 | 2,207 | 69.8 |
| Plumbing | 6,995 | 255 | 1,177 | 2,776 | 2,787 | 68.0 |

Average dimensions by tier (crawled). Pain is what separates the tiers; value is flat because the categories are similar:

| Tier | Pain | Value | Reach | Fit | Total |
|---|---:|---:|---:|---:|---:|
| A | 78.6 | 73.4 | 77.9 | 68.1 | 75.6 |
| B | 48.0 | 72.4 | 76.8 | 65.1 | 63.9 |
| C | 25.5 | 74.6 | 59.2 | 56.3 | 50.8 |
| D | 3.1 | 71.0 | 43.7 | 40.7 | 32.8 |

Dead leads (all 233,798): no reachable phone 5,549 · fit pushed to 0 by penalties 166 · answering service 133 ·
parked site 31 · franchise disclaimer 21. (Chains were already excluded in Stage 1.)

Signals present on A-tier leads (out of 771): no chat 700, single number 587, 24/7 claim 564, no online booking 521,
limited hours 472, phone-only booking 231, pricing 118, hiring front desk 99, multiple locations 66,
voicemail on own site 41.

## Hand review (50 A-tier leads)

All 50 are plausible SMB targets: independent, reachable phone, working site. About 150 reasons were checked and
**2 were wrong**: a "$500 van" story counted as pricing, and a review where the *dentist* left the voicemail.
Both are fixed in extractor v6 and covered by regression tests, and the leads were re-scored afterwards.

Finding: 403 of 771 A-tier leads (52%) get there on many weak signals plus good reachability and fit, without a strong
signal (voicemail, hiring, phone-only booking). They're good-fit, reachable businesses, but the evidence of pain is
weaker. Options are to require at least one strong signal for A, or to let Stage 6 outcomes decide. I recommend
waiting for outcomes rather than guessing.

## Cost projection at 3M

| Step | Cost |
|---|---:|
| Rule scoring (this stage), all records | $0 (~2-3 min) |
| Optional T3 LLM check, top 10% only (300k x claude-haiku-5-5) | ~$210 (needs an Anthropic key in an env var) |
| T5 openers, top 1.5% (45k x claude-opus-5-5), Stage 5+ | ~$1,170 |

The real 3M constraint is the crawl, not scoring: only crawled sites can rank. At ~2 s per page and a 5-page cap,
crawling ~700k US websites is several days with 2 workers. It costs $0, but it needs a long-running host.

## Not done / gaps

- No outcome data yet, so the weights are evidence-based judgments rather than fitted values (Stage 8).
- Google review content is shown live but is **not** used in scoring (policy; needs counsel).
- No LLM check yet (T3).
