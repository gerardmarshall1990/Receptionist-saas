# Ideal Customer Profile (ICP)

What we sell: an AI receptionist at ~$389/mo that answers every call, books jobs and takes messages.
Who buys: a small business where **a missed call is lost money**, the owner's **hands are busy**, calls are
**time-sensitive**, and the **ticket value is high** relative to $389/mo.

The machine-readable version of this document is `config/` (validated by `leads config validate`).
If this file and the YAML disagree, the YAML is what runs; fix whichever is wrong.

## 1. Who qualifies (all must hold)

| Rule | Value | Where it lives |
|---|---|---|
| Category | One of the 7 below | `config/categories/*.yaml` |
| Country | US now; then CA, AU, UK | `config/global.yaml: countries_enabled` |
| Size | Staff 1-20 (per-category target; disqualified above `hard_max`) | `staff_target` |
| Ownership | Owner-operated, **not** a chain or franchise | `exclusions.chain_name_patterns` |
| Website | Exists and works | signal `website_works` |
| Reachable | Verified phone (valid, line type known, cross-checked) | `phones.status` (Stage 3) |
| Need not already solved | No answering service / AI receptionist | `global.yaml: answering_vendor_patterns`, `negative_keywords` |

Hard exclusions (spec §1): shops, travel agencies, chains/franchises, large employers.

## 2. Target categories

Rank is the spec's starting hypothesis. `value` is our guess at ticket value vs. price (0-1). Both get refit
from outcomes (Stage 8).

| Rank | Category | Pilot | Value | Why they should convert |
|---|---|---|---|---|
| 1 | Plumbing | a | 0.80 | Emergency jobs, owner on site, after-hours demand |
| 2 | HVAC | a | 0.85 | Seasonal spikes, urgent calls, high-ticket installs |
| 3 | Electrical | - | 0.70 | Urgent calls, owner-operators common |
| 4 | Roofing and restoration | - | 0.90 | Storm bursts, urgent water/fire damage, very high ticket |
| 5 | Dental | b | 0.85 | New-patient calls missed at lunch/after hours; high patient lifetime value |
| 6 | Small law (PI, family, criminal) | - | 0.95 | Each client worth a lot; crisis callers won't leave voicemail |
| 7 | Auto repair and body | - | 0.55 | Mechanics under cars; steady booking calls |

Watch list (not configured): veterinary, med spa/salon, property management, locksmith/towing/garage door,
real estate agency.

Each category file holds: value weight, Overture/OSM source codes, included/excluded subcategories, site
pain keywords, review pain keywords, "already solved" negatives, chain/keyword exclusions, staff target,
best call windows, and opener templates.

## 3. How a lead is scored (spec §5)

`Total = 0.35*Pain + 0.25*Value + 0.25*Reachability + 0.15*Fit` (each 0-100).
Tiers: **A ≥ 80, B 60-79, C 40-59, D < 40**. Zero Reachability or zero Fit means total = 0 (dead lead).
Disqualifiers (`uses_answering_service`, `is_chain`, `staff_too_large`) remove a lead outright; they are gates,
not weights. All weights live in `config/scoring.yaml`.

Signals are defined once in `src/leads_engine/signals.py`. Config, scoring, reasons and openers may only
reference those names, so every "why this lead" reason points at something the crawler really stores.

## 4. Openers

One sentence, tied to one stored signal (`requires_signal`). Allowed placeholders: `{name}`, `{city}`,
`{evidence}`; `{evidence}` is filled only from the stored snippet for that signal. No genuine signal means
the opener is `null`. Disqualifiers and plain contact facts can't be opener reasons (enforced by the loader).

## 5. Call windows

Local time of the business. Every window must sit inside `global.yaml: calling_hours_hard_bounds`
(08:00-20:00, the strict end of federal + state rules). This is an engineering guard rail, not legal
advice; counsel signs off before any calling.

## 6. OPEN QUESTIONS

Decisions for you (also listed in each YAML's `open_questions`):

**Scope and definitions**
1. Is the 3M target the raw universe (T0) or the verified, exported set? Config assumes universe.
2. Law: include immigration and estate planning? Currently excluded (spec lists PI, family, criminal only).
3. Roofing vs. restoration: one config (current) or two? Different buyers and call patterns.
4. Dental: target the office manager or the dentist?
5. HVAC: include commercial-only firms in the pilot?

**Product readiness (blocks export for that category, not data collection)**
6. Dental: an AI receptionist handling patient details needs a HIPAA BAA. Is the product ready?
7. Law: can the receptionist qualify cases, or only take messages (privilege / unauthorized practice)?
8. Only `plumber` and `hvac` have niche knowledge files in the main app; the other 5 need one before selling.

**Compliance (needs counsel before Stage 5)**
9. AI-voice outbound to businesses: consent rules for landlines vs. mobiles under TCPA (FCC treats AI voices as
   "artificial").
10. Day-of-week and holiday calling restrictions by state.

**Data (answered in Stage 1/2)**
11. Overture category codes are written from memory and marked `unconfirmed`. Stage 1 checks them against the
    release's taxonomy before any query.
12. Chain lists are hand-seeded and incomplete. Stage 1 can find more by spotting names repeated across many cities.
13. All value weights, keyword sets and call windows are starting guesses until outcome data arrives.

**Tactics to test (Stage 6+)**
14. Call seasonal trades (HVAC in heat waves, roofers after storms) at peak pain, or wait until they're less slammed?
15. Online booking or text-for-status tools: lower the pain score (current rule) or exclude?
