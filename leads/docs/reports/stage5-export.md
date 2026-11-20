# Stage 5 — compliance gate + export (pilot)

Config: `config/compliance.yaml` v1, calling mode **human**, extractor **v7**. Cost: **$0** (no paid API, no LLM).

## What a lead must pass to be exported

The gate (`compliance/gate.py`) is a pure function: one record in, `pass`/`blocked` out, with every reason.
Exports re-run it, and re-read the suppression list at write time.

| Rule | Blocks when |
|---|---|
| Suppression (permanent) | business ID, phone, email or domain is on the list |
| Country | not enabled (US only) |
| Score | dead/disqualified, tier below **B**, or no evidence-backed reason |
| Phone | missing; toll-free, premium, foreign or fake; status below **likely** (= listed by Overture AND on their own site) |
| Contact safety | phone shared with another business, listed phone not on their site, website mismatch, no state |
| Email | not published on their own site (email is optional; only a bad one blocks) |
| Source | source not allowed, or license missing / not allowed |
| AI-voice mode (off) | line type isn't a confirmed landline |
| DNC switch (off) | DNC not checked |

Every passing row carries two honest notes: `line_type_unknown` and `dnc_unchecked` (see "Needs counsel").

## Result (pilot DB, 233,798 businesses)

| | |
|---|---:|
| Candidates (tier A + B) | 4,055 |
| **Passed and exported** | **3,670** (A 692 · B 2,978) |
| Blocked | 385 |

Why the 385 were blocked (one record can have several reasons): phone not confirmed on their site 319 ·
listed phone not on their site 294 · phone shared with another business 66 · website mismatch 7 · no state 1.

| Exported | Plumbing | Dental | HVAC |
|---|---:|---:|---:|
| Leads | 1,352 | 1,176 | 1,142 |

- Email on 2,283 (62%); every one is published on their own site.
- Opener on 2,179 (59%). The rest have `opener: null` because no stored reason is strong enough to open with.
- 3 reasons per row on 95% of leads, each with the quoted evidence and its source URL.

Output: `data/exports/<stamp>-<id>/leads.parquet`, `leads.csv` and `manifest.json` (counts, block reasons,
config hash, attribution, needs-counsel list). Every candidate gets an `export_items` audit row: the exact
exported record, or the block reasons.

Speed: export 1–2 s. Gate over all 233,798 is ~35 s; at 3M, about 8 min.

## Openers ($0, rule-based)

Each opener is a category template for the lead's **strongest stored reason**, by signal weight. Templates that need
`{evidence}` are skipped (we never paraphrase evidence), and legal suffixes are trimmed from names
("Coziahr Heating & Air Conditioning, Inc." -> "Coziahr Heating & Air Conditioning").

## Hand checks (20 random exported rows, twice) and what they fixed

1. **Glued emails (extractor v7).** 2/20 rows had phone digits glued to the email
   (`664-2498thompsonplumbing@windstream.net`). About 1,000 site emails (7%) had digits or words stuck on either
   end (`info@x.comoffice`). Cause: page text joined adjacent HTML elements with no space. Fix:
   - page text keeps element boundaries
   - emails read from text must be whole tokens on a real TLD
   - bad matches are dropped, never repaired

   Result: 0 glued emails left. Businesses with a site email went 6,074 -> 6,038 (-0.6%); the row drop was junk
   variants. Among exported leads, 261 emails were corrected and 11 dropped (mostly `filler@godaddy.com...`
   junk). All 72 extraction tests pass, including every earlier signal regression.
2. **Opener overclaim.** The plumbing template said "offers **24/7** emergency service", but the signal also fires
   on plain "emergency service". It now says only "emergency service", and the same fix went into `law_small`
   ("available 24/7" -> "urgent help"). The review templates said "a few reviews" where one review can
   trigger the signal; they now say "a review". 0 exported openers mention 24/7.
3. The second sample, after both fixes: all 20 rows have a phone on their own site, reasons quoting the site, and
   an opener that restates the reason (or is empty).

## Explorer

- **Compliance filter:** Any / Passes / Blocked.
- **"Export gate" section on each lead:** pass/blocked in plain words, the notes, and the opener preview.
- **"Export passing leads" button:** gates the current filtered list on the server and downloads the CSV,
  capped at 5,000 rows. Every export is recorded.
- **Suppress** blocks the lead at once.
- Screenshots: `docs/screenshots/explorer-export-gate.png`, `explorer-export-blocked.png`,
  `explorer-mobile-export.png`.
- Benchmark re-run at 3M rows: worst p95 262 ms (facets), detail 20 ms.

## Needs counsel (also in `config/compliance.yaml` and every manifest)

- Human calls to `likely` business numbers (line type unknown) without a DNC scrub.
- State mini-TCPA laws (FL, OK, MD, WA, ...): call times, Sundays and holidays.
- AI-voice outbound: needs prior express consent for mobiles; we can't tell mobile from landline yet.
- CA/AU/GB rules before enabling those countries.
- Storing derived Google-review signals (currently neither stored nor used).

## Not done / gaps

- **No `leads` table in the main API (`apps/api`) yet:** export is files plus the explorer download. Pushing to
  the main app is a separate change.
- **No phone is `verified`, and line type is unknown,** because there are no paid lookups.
- **`owner_name` is always null:** we don't collect it yet.
- **8 placeholder-looking site emails remain** (`example@`, `test@`, `email@`); some `email@business.com` are real.
  A placeholder rule can ship with the next extractor change.
- Leads whose site was never crawled can't pass: no evidence means no reasons.
