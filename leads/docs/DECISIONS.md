# Decisions log

Newest first. One entry per decision that someone might later ask "why?" about.

## 2026-11-21 — Stage 7 database build starts before the pilot readout

User: "go ahead as per most efficient route. I need to see list pretty soon so keep going until done as per my
specs." Building the database is open data and $0, so it goes ahead now; decisions about **calling** at scale
still wait for the pilot1 readout.
- **All 7 categories, US:** 930,691 raw -> 725,895 kept and loaded. The reload found 530 existing rows that the
  wider build reclassifies (306 now visible as chain locations, 224 duplicates). Rows nothing references are
  removed; rows referenced by pilot1 or exports are kept and flagged `not_in_latest_build`, which the gate blocks.
- **Best-first crawl:** priority = category value + usable unshared phone + independent; a site with no usable
  phone goes last because it can never become an exportable lead.
- **Disk bounds the crawl here:** saved pages take ~78 KB per site, about 35 GB for all 447k new sites, against
  ~20 GB free. The loop stops at 6 GB free, so this container covers roughly the best 150k sites. The rest needs
  a lasting host (or dropping saved HTML after extraction, which would end re-extraction when rules improve).
- **Incremental extraction:** only sites crawled since their last pass (or extracted by an older version), in
  batches, so cost tracks new sites rather than the whole crawl.
- **The lead list is published as a private claude.ai page** from each gated export, so it can be seen without
  hosting the explorer.

## 2026-11-21 — Experiment records are committed to git

The container (and its Postgres) is ephemeral, and pilot1's frozen assignment must outlive it. `experiments/<name>/`
holds the minimum to reproduce it: Overture source IDs (stable within the pinned release), arm, call order, seed,
params, config hash, and call outcomes without free-text notes. No names, phones or emails. This is a narrow
exception to "never commit data": it is the experiment's record, not bulk data. `restore` re-creates the
experiment on a fresh database and re-applies opt-outs logged as `do_not_call`. Callers work from the call list,
not the repo; the arm column in the snapshot is not shown to them. The full suppression table still lives only
in Postgres until there is a lasting host.

## 2026-11-21 — Stage 6 (pilot experiment)

User took the recommended option on all four:
- **Control = random draw from the whole callable universe**, using no score information. Its phones may be
  `unverified` (listed by Overture, not confirmed on the site). This is a deliberate exception to the Stage 5
  phone bar, for the comparison only; it's on the needs-counsel list. Safety rules still apply.
- **2 arms x 1,000**, same category quotas in both arms, assignment frozen by a database trigger before any call.
- **No openers in pilot1** (one variable at a time: targeting first, script later).
- **Outcomes by generic CSV import**; `do_not_call` becomes a permanent suppression.
- Website dropped from the call list after seeing that ~22% of control leads have none: it would unblind callers.
- Verdict rule fixed in advance: no winner below 300 dialled per arm, a winner only at p < 0.05.

## 2026-11-20 — Stage 5 (compliance gate + export)

User chose the recommended option on all four ("best, most efficient and purposeful"):
- **Phone bar = `likely`** (listed by Overture AND on the business's own site). Nothing can reach `verified`
  without a paid line-type lookup, which we don't do. Exported with `line_type: unknown`, stated honestly.
- **Calling mode = human.** An AI-voice mode exists in `config/compliance.yaml`; switching to it blocks every
  record whose line isn't a confirmed landline (FCC 2024: AI voices are "artificial voice" under the TCPA).
- **Export = files + explorer download** (Parquet + CSV + manifest under `data/exports/`, audit rows in
  `exports`/`export_items`). The main API has no `leads` table yet; adding one is a separate, cross-app change.
- **Openers = $0 rule-based**: the category template for the lead's strongest stored reason. Templates that need
  `{evidence}` are skipped (we never paraphrase evidence). No strong reason -> `opener: null`.
- **DNC = recorded, not checked** (`dnc_unchecked` warning on every row). A switch makes it a block. The FTC
  registry needs an org account; counsel decides.
- Also blocked: shared phone, listed phone not on their site, website mismatch, toll-free/foreign/fake numbers,
  emails not from their own site, unknown sources/licenses, any suppression match (re-read at write time).
- **Extractor v7** (found in the export hand check): ~7% of site emails had phone digits or words glued on
  ("664-2498info@x.com", "info@x.comoffice") because page text joined adjacent elements. Page text now keeps
  element boundaries; text emails must be whole tokens on a real TLD. Bad matches are dropped, never repaired.

## 2026-11-20 — Stage 4 (rule-based scoring)

- **Rules only, no LLM yet** (user chose; the T3 LLM check needs an Anthropic key in an env var). $0 per record,
  233,798 businesses scored in ~75 s; scales linearly to 3M.
- **Precision gate passed** before weighting: fresh blind sample of v5 hits, every signal >= 89% (hiring 8/9,
  everything else 100% in the sample; leave_message 45/46 from the full review).
- **Weights follow evidence strength**: voicemail-on-own-site 10, hiring front desk 8, phone-only booking 6,
  limited hours 3, single number 2, 24/7 claim 2, no booking / no chat 1 (those are on 50-80% of sites).
- **Tier A cut-off 80 -> 70** after measuring: 95th percentile total among crawled pilot leads = 69.7, so the
  spec default put only 0.8% in A. A >= 70 is ~top 5%. Refit all cut-offs from outcomes in Stage 8.
- **Every score is explainable:** `scores.components` lists every point added or removed.

## 2026-11-19 — Stage 3 redirected: no paid phone lookups; live Google reviews instead

- **No Twilio (user decision).** Phones stay at `likely` at best (Overture + own website agree). Consequence:
  line type (mobile vs landline) is unknown, which Stage 5's AI-voice rules need; revisit there.
- **No scraping of Google Search/Maps or social sites** (spec section 7 and those sites' terms).
- **Google reviews via the official Places API, live only.** Policy read 2026-11-19: place IDs may be stored
  indefinitely; review text and other Places content may not be copied/stored, and content may not be derived
  from it. So the explorer fetches rating + up to 5 reviews (Google's relevance pick) when a user clicks, shows
  them with attribution, highlights phone-pain phrases, and stores only the place ID. Nothing feeds scoring.
- **Why not batch review mining for the 3M:** ~$25 per 1,000 businesses for 5 mostly-positive reviews each, plus
  unresolved terms on storing derived signals. Batch use only if counsel approves derived signals.
- **Reviews on businesses' own sites** were measured instead (free): 3,907 sites show reviews, only 8 mention
  unanswered calls, and those praise the business vs competitors. Useful as sales-pitch language, not as lead pain.
- **Cost control:** IDs-only Text Search, then one Place Details call (Enterprise + Atmosphere SKU, $25 per 1,000,
  first 1,000/month free) reserved against `api_usage`; `google_places_monthly_cap` defaults to the free 1,000.
  Matches must agree on phone, or on name + ZIP/street number; otherwise "no confident match", nothing stored.

## 2026-11-19 — Stage 2 (crawl + signals, pilot)

- **Bot identity:** `FrontdeskLeadsBot/0.1`, no contact link (user chose not to publish one).
- **Unit of work = website**, not business: 20k pilot businesses -> 15,399 sites; shared sites crawled once.
- **HTML is kept** (gzipped Parquet, gitignored) so rules can be fixed and re-extracted without re-crawling.
  At 3M scale that is TBs: store extracted text/features only, or object storage (decide in Stage 7).
- **Conservative block rules:** 403/429, 202 and other non-404 4xx on a page = blocked (WAFs answer bots that
  way); challenge = marker in <title>, or in the body of a near-empty page only (reCAPTCHA in a contact form is
  not a block). 19 sites from the first batch reclassified error -> blocked when this was found.
- **Playwright not used.** JS-only sites are flagged `thin_homepage` and measured; decide later if worth it.
- **Precision loop:** v1 -> v3 after two blind hand reviews (see stage2-precision.md). Biggest catches:
  "answering service" disqualified businesses that say they *don't* use one (negation); "leave a message"
  fired on web-form labels; "independently owned and operated" is a franchise disclaimer, not independence.
- **New caution flag `promises_live_answer`:** sites that sell on "a real person always answers". Not a
  disqualifier, but likely a weaker fit for an AI receptionist; Stage 6 outcomes will tell.
- **Phone corroboration:** Overture phone also on the business's own site -> agree_count 2, status `likely`.
  `verified` stays reserved for the paid line-type lookup (Stage 3), per spec section 6.
- **Two crawl workers** ran in parallel to finish the pilot in time; politeness is per site and unaffected.
- **Pacing tightened after the pilot crawl:** the pilot was paced start-to-start (>= 2 s between request starts,
  like Scrapy's download delay), so a slow response could be followed quickly by the next request. Found when
  the report measured finish-to-finish gaps of 0.04 s (no site was ever fetched by two workers: 0 cross-run
  pairs). From now on: >= 2 s of idle time after each response, request start times are stored, and the report
  and an end-to-end test measure that gap. The 33,443 pilot pages can't be re-measured this way.

## 2026-11-19 — Stage 1b (lead explorer UI)

- **Stack:** FastAPI + plain HTML/CSS/JS served by the same process, inside `leads/`. No build step, no CDN.
- **Scale:** keyset (cursor) paging; one index per sort; (state|category, score) indexes; trigram search; exact
  counts up to 50k, labelled planner estimate above; counts/facets cached 60 s (cleared on suppress); facet queries
  run concurrently. A single GROUPING SETS facet query was measured and rejected (slower: no parallel hash agg).
  Result at 3M rows: every call p95 < 300 ms (worst 181 ms), `docs/reports/explorer-benchmark.md`.
- **"Callable now"** is computed in Python per (category, time zone) and matched in SQL (was 3.4 s per-row).
- **Suppress** is the only write: business id + every phone + every email (+ optionally the domain), permanent.
  State-changing calls must be JSON (blocks cross-site form posts); token cookie is HttpOnly + SameSite=Strict.
- **No export** in the UI until the Stage 5 compliance gate (spec section 7).
- **All 233,798 kept businesses** can be loaded (`load --all`); loading is set-based (COPY), ~70 s.
- **`reasons` table** (spec section 3) created now so the UI contract exists before Stage 2 writes to it.
- Hosting is not decided: this container's Postgres is reset on restart. Plan hosting separately.

## 2026-11-19 — Stage 1 (universe pilot)

- **Source:** Overture Maps Places, release `2026-11-18.0` (pinned in `global.yaml`), read straight from the public
  bucket over HTTPS: only the needed columns, and only the 7 of 16 files overlapping the US. No bulk download.
- **OpenStreetMap deferred to Stage 3** (approved): a US extract is ~10 GB and in the pilot it would only add a
  second phone source.
- **Sample:** stratified random across the whole US (plumbing 8k, HVAC 6k, dental 6k), deterministic seed `stage1-v1`.
  The full extract stays in Parquet; only the sample goes to Postgres.
- **Primary code only.** Places are matched on `taxonomy.primary`, not `alternates`. The census report shows the
  alternates ceiling; for the pilot codes it's small.
- **Locations, not cities.** Chains and multi-location businesses are counted by distinct ZIPs. Counting cities missed
  chains concentrated in one metro (Absolute Dental, ~40 sites in Las Vegas).
- **Dedupe keys:** same phone (unless > 3 listings share it), same website + ZIP, same name + ZIP (not for generic
  names). An earlier "same website + city" key merged separate locations of one chain into one record; replaced.
- **Phone agreement starts at 1.** Overture records provenance per record, not per field, so the cross-check
  (`agree_count`) gets real values in Stage 2/3 (website, lookup). The record's contributing datasets are stored as
  `source_datasets`.
- **Emails from Overture are `source_listed`**, never exportable as-is. Only `business_site` emails (found on the
  business's own site in Stage 2) can be.
- **`.gov` "websites" are dropped, not excluded.** Plumbers often list their state license page; excluding them
  threw away real businesses.
- **Chain patterns match the start of the name.** Matching anywhere excluded "Twin Aspen Dental", "New Castle Dental",
  "Tim Ferguson Plumbing". Generic-sounding brands are left to the domain rule.

## 2026-11-19 — Stage 0

- **Location.** The engine lives in `leads/` inside the `Receptionist-saas` repo, not a separate `frontdesk-leads`
  repo (the spec's name), because this session can only reach this repo. It's self-contained (own Python project,
  own Postgres DB `frontdesk_leads`, no imports from the TypeScript app), so it can move out later unchanged.
- **Models.** Scoring (T3, top ~10%): `claude-haiku-5-5`, because the spec asks for a cheap model. Openers (T5, top ~1.5%):
  `claude-opus-5-5`, because the spec asks for a stronger model. Both are config values (`config/models.yaml`) and can be
  overridden by env vars. Sampling params (`temperature`) are rejected by the loader because the current API rejects them;
  `effort` is the control. Projected LLM spend at 3M universe is printed by `leads config validate`.
- **Roofing + restoration** share one config with subcategories (spec lists them together). Split if outcomes diverge.
- **Law** includes PI, family and criminal defense only, as the spec says. Immigration and estate are listed but `include: false`.
- **"Already solved" means exclude.** Only an answering service or AI receptionist disqualifies (spec §4). Online booking or chat
  only lowers pain.
- **Calling-hours guard rail.** 08:00-20:00 business-local, the stricter of federal and state rules. This is not legal advice.
- **Suppression is permanent at the DB level.** A trigger blocks UPDATE and DELETE.
- **Only `runs` and `suppression` tables in Stage 0.** The other spec §3 tables arrive with the stage that writes them.

## Requested, not yet scheduled

- **Interactive lead database UI** (asked 2026-11-19): your own UI to browse, filter, sort and drill into all leads
  (target 3M) with their score, tier, reasons, evidence, contact status and compliance status. This isn't a spec stage yet.
  Proposal: plan it as its own stage right after Stage 1, so it's built against real pilot rows and real column shapes.
  It needs server-side filtering and pagination plus the right Postgres indexes to stay fast at 3M rows. Scope to be
  agreed before it's built.
