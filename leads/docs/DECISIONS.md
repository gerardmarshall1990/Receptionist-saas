# Decisions log

Newest first. One entry per decision that someone might later ask "why?" about.

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
