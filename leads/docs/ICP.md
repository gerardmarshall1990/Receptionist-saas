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

Hard exclusions (spec §1): shops, travel agencies, chains/franchises, large employers. How Stage 1 applies them
(in this order; first match wins, every excluded row keeps its reason):

| Rule | What it catches |
|---|---|
| `closed` | Overture says permanently/temporarily closed |
| `institution` | website on `.edu` / `.mil` (university clinics, military) |
| `referral_listing` | "Emergency Dentist 24/7", "Plumber Near Me" (lead-gen listings) |
| `brand_wikidata` | Overture links the place to a known brand |
| `chain_name_pattern` | brand at the start of the name ("Aspen Dental - X"), or a `chain_name_contains` brand anywhere |
| `exclusion_keyword` | supply houses, dental labs, VA/hospital/university-of, answering services |
| `chain_multi_location` | same website at >= 10 locations (ZIPs) |
| `chain_name_network` | same name at >= 10 locations and >= 50% of those listings share one website |

Kept but flagged: 2-9 locations (`multiple_locations`, a value signal), phone shared by > 3 listings, generic names,
names repeated in many places with different websites (`name_repeated_review`).

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
11. ~~Overture codes unconfirmed~~ **Answered in Stage 1:** all 7 categories confirmed against release 2026-11-18.0
    (`docs/reports/taxonomy-2026-11-18.0.md`).
12. Chain lists are still hand-seeded, but Stage 1 now detects chains by shared website domain and name networks,
    and the report lists the top candidates for review.
16. **3M looks out of reach with these 7 categories.** US has ~931k raw listings across all 7 (~690k after the pilot's
    ~74% keep rate); CA/AU/UK add perhaps 250-350k. Reaching 3M needs the watch-list categories (or more). Do we
    redefine the target as "every reachable, qualified business in the ICP" or add categories?
17. Law: 113k US firms carry only the generic `attorney_or_law_firm` code. They're kept as `practice_unknown`
    until the Stage 2 crawl shows the practice area.
13. All value weights, keyword sets and call windows are starting guesses until outcome data arrives.

**Tactics to test (Stage 6+)**
14. Call seasonal trades (HVAC in heat waves, roofers after storms) at peak pain, or wait until they're less slammed?
15. Online booking or text-for-status tools: lower the pain score (current rule) or exclude?
