# Decisions log

Newest first. One entry per decision that someone might later ask "why?" about.

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
