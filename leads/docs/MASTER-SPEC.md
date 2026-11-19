# Leads Engine — MASTER SPEC (supersedes frontdesk-lead-database-spec.md)
Save as `docs/MASTER-SPEC.md` in the `frontdesk-leads` repo. Claude Code: read fully, then follow the stages in order. One stage at a time, plan first, wait for "verified".

## 0. Mission
Build the best database of small businesses (US first; then CA, AU, UK; target ~3M total) for selling an AI receptionist at ~$389/mo. Every exported lead must be:
1. **Reachable:** verified phone (valid, line type known, cross-checked), plus a verified public business email WHEN one exists.
2. **Qualified:** fits the ICP (category, size, not a chain).
3. **Scored with reasons:** a ranked score and a list of evidence-backed reasons it is likely to convert ("why this lead").
4. **Compliant:** passed the per-country compliance gate; permanent suppression honored.
Reality check: many small businesses publish no email. Do NOT guess emails by pattern. Measure email coverage in the pilot and report it. A lead can be exported as Phone-qualified without email; "Gold" = phone + email both verified.

## 1. Target categories (hypotheses to validate with outcome data)
Why they should convert: a missed call = lost money, the owner is hands-busy, calls are time-sensitive, and ticket value is high relative to $389/mo. Rank is a starting guess; the feedback loop re-ranks it.
1. **Plumbing** — emergency jobs, owner on site, after-hours demand.
2. **HVAC** — seasonal spikes, urgent calls, high-ticket installs.
3. **Electrical** — emergency/urgent, owner-operator common.
4. **Roofing and restoration (water/fire damage)** — storm surges, urgent, very high-ticket jobs, calls come in bursts.
5. **Dental practices** — new-patient calls missed at lunch/after hours, front desk overwhelmed, high lifetime value per patient.
6. **Small law firms (personal injury, family, criminal defense)** — each new lead is worth a lot, callers won't leave voicemail, evenings/weekends.
7. **Auto repair and body shops** — mechanics are under cars, phones go unanswered, steady booking demand.
Watch list (test later): vets, med spas/salons, property management, locksmith/towing/garage doors, real estate agencies. Exclude: shops, travel agencies, chains/franchises, large employers.
Pilot order: (a) plumbing + HVAC, (b) dental. Each category gets a config: value weight, pain-keyword set, typical staff size, best call hours, niche opener templates.

## 2. Funnel (cheap and wide -> expensive and narrow)
| Tier | Records | What happens | Cost |
|---|---|---|---|
| T0 Universe | up to ~3M+ | Free bulk sources: Overture Maps Places (GeoParquet via DuckDB), OpenStreetMap, government registries | ~free |
| T1 Crawl | all T0 with websites | Polite crawl of site + contact/about pages; rule-based signals; public email extraction; phone cross-check | compute |
| T2 Cheap verify + pre-score | all T1 | Phone format/validity, cross-source phone agreement, email syntax+MX; rule-based pre-score | ~free |
| T3 Evidence + LLM score | top ~10% | Review pain (provider within terms), cheap LLM scoring with reasons | low $ |
| T4 Paid verification | top ~2-3% | Phone line-type/carrier lookup; email deliverability verification; Places API confirmation | per-record $ |
| T5 Openers | top ~1-2% | Stronger model writes evidence-grounded opener + "why this lead" summary | modest |
Never run LLMs or per-record paid APIs on the whole universe.

## 3. Data model (Postgres; raw files in Parquet)
`businesses`: id, name, category, subcategory, country, state, city, address, lat, lng, tz, phone, website, domain, staff_estimate, is_chain, source, source_id, license, attribution, first_seen_at, crawled_at.
`phones`: business_id, e164, source(s), agree_count, valid_format, line_type (landline/mobile/voip/unknown), carrier, status (verified/likely/unverified/invalid), verified_at.
`emails`: business_id, email, source_url, is_role_based, syntax_ok, mx_ok, deliverability (valid/risky/catch_all/invalid/unknown), verified_at. Only publicly published business emails.
`signals`: business_id + booleans/ints from section 4, plus raw evidence snippets and source_urls.
`scores`: business_id, pain, value, reachability, fit, total, tier (A/B/C/D), model_version, scored_at.
`reasons`: business_id, array of {signal, evidence, source_url, weight}. This is the "why it converts".
`suppression` (permanent), `outcomes` (from main repo: answered, demo, trial, paid), `runs` (cost, counts, config hash).

## 4. Signals (rule-based first; LLM only for top slice)
Pain/need: phone-only booking; no online booking; no chat widget; single phone number; "leave a message"/"we'll call you back"/limited-hours text; emergency/24-7 claims without after-hours answering; hiring a receptionist (job page or Adzuna); already uses answering service/AI receptionist (NEGATIVE, exclude).
Reviews (top slice only, within provider terms): "never answers", "voicemail", "couldn't get through", "called multiple times", "hard to reach".
Value: category weight; service pricing hints; multiple locations (more value, but check decision-maker).
Fit: staff 1-20, owner-operated, not a chain, has a website that works, phone reachable during business hours.
Reachability: verified phone, direct owner/decision-maker name if public, email present and valid, best call window by tz.

## 5. Scoring (Harvey method + Hormozi value equation)
Think of each lead as: (Pain x Value x Reachability x Fit). A lead with zero reachability or zero fit is dead regardless of pain.
- Pre-score (rules): weighted sum of signals above, normalized 0-100.
- LLM score (top 10%): JSON {pain, value, reachability, fit, reasons[], disqualifiers[]}. Prompt rules: use ONLY provided evidence; every reason must cite a stored signal or snippet; if evidence is thin, say so and score low. No guessing.
- Total = 0.35*pain + 0.25*value + 0.25*reachability + 0.15*fit. Tier A >= 80, B 60-79, C 40-59, D < 40. Weights live in config and are re-fit from outcomes.
- "Why this lead" = 2-3 reasons, most predictive first (e.g., "Only one phone number listed and no after-hours coverage", "Reviews mention difficulty getting through", "Emergency plumber advertising 24/7 with phone-only booking").
- Openers: one sentence tied to one real reason. If no genuine reason, opener = null.

## 6. Verification (phone and email)
Phone: normalize to E.164 (libphonenumber); validity; count independent sources that agree (Overture/OSM vs website vs Places); detect obvious fakes/toll-free/call-tracking numbers; line type + carrier lookup via a paid lookup API for T4 only; re-verify if older than 90 days. Status: verified (line-type confirmed + >=2 sources or website match), likely, unverified, invalid.
Email: extract only addresses published on the business's own site (mailto links, contact page); syntax + MX check for all; deliverability verification via a paid verifier for T4 only; classify role-based (info@, sales@) vs named; skip catch-all unless otherwise justified; never harvest personal emails or guess patterns.
Report coverage: % phone verified, % email found, % email valid, per category/country.

## 7. Compliance and ethics (hard gate at export)
- Business contact data only. Honor opt-outs permanently via `suppression`.
- Per-country: US (TCPA, National DNC + state DNC lists, state mini-TCPA laws, CAN-SPAM for email, AI-voice rules), Canada (CASL, DNCL, PIPEDA), UK (PECR, TPS/CTPS, UK GDPR; sole traders are treated differently from companies), Australia (Spam Act, Do Not Call Register, Privacy Act). Do not assume; keep rules in versioned config and have counsel review before scale.
- Flag mobile vs landline; AI-voice calls to mobiles require extra care.
- Respect robots.txt, site terms, rate limits; stop on blocks and do not evade bot protection. Do NOT scrape Google Search/Maps pages; use official APIs within terms (storage/caching limits, attribution) or bulk open datasets.
- Respect source licenses (Overture/OSM attribution). Store source + license per record.
- No fabrication: every reason and opener traces to stored evidence.

## 8. Engineering
Python 3.12; DuckDB/Parquet for bulk; Postgres for working/serving data; async crawler (httpx/aiohttp) with per-domain rate limiting, retries with backoff, robots.txt cache, content-hash dedupe; Playwright only as a fallback for JS-rendered sites that fail the plain fetch; Postgres-backed job queue; all LLM calls cached by content hash; per-run budget caps and hard stop; `--dry-run`; idempotent, resumable jobs; structured logs; run table recording cost per stage; tests for normalization, dedupe, signal extraction (with saved HTML fixtures), scoring, and compliance gate.

## 9. Stages and acceptance criteria
0. **ICP config:** `docs/ICP.md` + category config files. Accept: 7 categories configured with weights, keyword sets, exclusions.
1. **Universe pilot:** US, plumbing/HVAC + dental, ~20k rows. Accept: counts, % phone, % website, dedupe stats, 20 sample rows, projected cost/disk for 3M.
2. **Crawler + signals:** pilot only. Accept: crawl success rate, signal coverage, extracted-email rate, fixtures + tests, politeness verified.
3. **Verification T2/T4 (small batch):** Accept: phone/email coverage and validity stats, cost per verified lead.
4. **Reviews + scoring:** pre-score, LLM score with reasons on top slice. Accept: 50 manually reviewed leads, reasons all trace to evidence, cost projection for 3M.
5. **Openers + export:** Accept: export contract passes; compliance gate blocks test violations; export to main repo `leads` table.
6. **Pilot readout:** Compare top-tier leads vs random unscored on answer rate, demo rate, trial rate after the first calls. Decide scale-out.
7. **Scale-out:** more categories, then CA/AU/UK, in rounds; monitor cost per qualified lead.
8. **Feedback loop:** weekly join of outcomes; refit weights; report which signals predict conversion; refresh stale records.

## 10. Export contract
business_id, name, category, country, state, city, tz, phone_e164, phone_status, line_type, email (nullable), email_status, website, owner_name (nullable, public business role only), score, tier, why_reasons[], opener (nullable), best_call_window, source, license, crawled_at, verified_at, compliance_status. Records failing the gate are never exported.

## 11. Definition of done for the whole system
A scheduled job refreshes data; every exported lead has verified contact data, a score, evidence-based reasons and a compliance pass; cost per qualified lead is tracked; outcomes feed back weekly; the top tier measurably beats an unscored list.
