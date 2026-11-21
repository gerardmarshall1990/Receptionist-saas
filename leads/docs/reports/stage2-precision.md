# Stage 2 signal precision (hand-graded)

Each review drew a **fresh random sample** of positive signal hits from the pilot crawl (stratified so rare
signals were checked in full), and every hit was graded by hand against the signal's definition using its
stored evidence snippet. Every false positive found became a regression test in `tests/test_crawl_extract.py`,
and the extractor version was bumped. Absence signals (`no_online_booking`, `no_chat_widget`) and phone counts
aren't graded here: their evidence is the list of pages checked, which is auditable per lead in the explorer.

Rule: a signal under ~90% precision is not used for scoring (Stage 4).

| Signal | v1 (review 1) | v2 (review 2) | v3 (review 3) | Fixed in v4 | Notes |
|---|---|---|---|---|---|
| `uses_answering_service` (disqualifier) | **~4/11** | 7/7 | 11/12 | competitor reviews | v1 disqualified businesses that say they *don't* use one ("No answering service or AI"). Negation guard added; those now get the caution flag `promises_live_answer`. |
| `is_chain` (franchise, disqualifier) | – | 1/1 | 10/12 | comparison pages | "Independently owned and operated" is the franchise disclaimer. v4: wording must be about *their* location, or a franchise count. |
| `leave_message_text` | **~2/14** | 5/11 | 11/12 | competitor reviews | v1 fired on web-form labels ("Leave us a message ... Submit"). Now only voicemail/answering machine/busy lines/"if we miss your call". |
| `hiring_receptionist` | ~5/10 | 14/14 | 12/12 | – | "Customer Service **Rep**ort" no longer matches; role must sit next to the hiring text. |
| `phone_only_booking` | ~9/12 | 14/14 | 11/12 | "call or fill out the form" | "Request/Schedule service" buttons and forms count as an online channel. |
| `emergency_247_claim` | ~25/28 | 7/8 | 7/8 | "Fire and Emergency Services" | "AC working 24/7" and "avoid emergency repairs" excluded since v2; live-answer wording suppresses the claim. |
| `owner_operated` | 13/13 | 8/8 | 8/8 | – | |
| `limited_hours_text` | 1/1 | 8/8 | 8/8 | – | |
| `pricing_hints` | 6/6 | 7/8 | 7/8 | "saved me $300" (review) | |
| `multiple_locations` | 2/2 | 6/8 | 8/8 | – | "offices in <city>" dropped in v3; "N locations nationwide" is now a chain signal. |

Totals: v1 ~72/97 (74%), v2 77/87 (89%), **v3 93/100 (93%)**. v4 fixes the 7 remaining review-3 false positives
(each is a regression test).

**v5 (final for the pilot):** a real lead in the explorer showed "leave your name and number *below*" (a web form)
counted as voicemail; fixed. Then **every** `leave_message_text` hit in the pilot was reviewed (not a sample):
**45/46 (98%)**. The one miss is a patient review where the *dentist* left the voicemail.
The other signals have not had a blind review on v4/v5; do one before Stage 4 uses the weights.

**Stage 4 precision gate (v5, fresh blind sample):** every signal >= 89% (hiring 8/9, the rest 100% in the
sample). Passed, so all of them are used for scoring.

**v6 (after the Stage 4 50-lead review):** 2 of ~150 reasons on A-tier leads were wrong. Both are fixed and both
are regression tests:
- `pricing_hints` fired on a story ("bought a $500 van"). A price must now follow the price keyword directly
  ("starting at $89", "only $49"), not just appear somewhere nearby.
- `leave_message_text` fired on a patient review where the *dentist* left the voicemail. Wording where the
  business leaves the message now counts as the opposite of the signal.

## What the reviews taught (for scoring)

- **`emergency_247_claim` is true for ~half of all trade websites.** It is accurate but barely separates leads.
  Expect it to get a low weight once outcomes arrive.
- **`leave_message_text` is rare (<1% of sites) but strong:** these businesses state on their own site that
  callers reach voicemail. It's the most direct evidence of the pain we sell against.
- **`promises_live_answer` (caution flag)**: businesses that market "a real person always answers". Not
  disqualified, but likely a harder sell. Track its conversion in Stage 6.
- **Customer reviews on business sites are evidence too** ("left an after-hours voicemail"), but only when they
  describe *this* business, not a competitor.

**v7 (Stage 5 export hand check):** emails only. ~7% of site emails had phone digits or words glued on
("664-2498info@x.com", "info@x.comoffice") because page text joined adjacent HTML elements. Page text now keeps
element boundaries; text emails must be whole tokens on a real TLD; bad matches are dropped, never repaired.
Signal counts barely moved (e.g. leave_message_text 45, hiring_receptionist 147, phone_only_booking 286).


## New categories (Stage 7): law first

The crawl goes best-first, so law firms (value weight 0.95) arrive first; roofing, electrical and auto follow.

**v7 sample (6 hits per signal, mostly law):** answering service 6/6, hiring 6/6, emergency 5/6, multiple
locations 5/6, owner-operated 5/6, voicemail 4/6, phone-only 4/6, pricing 4/6 (both misses were the wrong website).
**v8 fixes:** "line is answered 24 hours" is live-answer coverage; reviews about *other / many attorneys* and
"in a world of ... voicemail" praise are excluded; "email or call" and "submit your inquiry" are other channels;
"our clients range from family-owned businesses" is about clients.

**v8 sample (10 per signal, law only):** limited hours 10/10, phone-only 9/10, emergency 7/10, voicemail 7/10,
owner-operated 7/10. **v9 fixes:** "call answering 24/7" is coverage; 911, public "emergency services" and
government hotlines are not a claim; intake-form questions ("May we leave a voicemail? Yes/No") are not voicemail;
"you can also fill out" is another channel; owner wording about other businesses or a practice-area list is
excluded; "Call 24/7" now counts like "Call us 24/7". Every miss is a regression test.

Open: roofing, electrical and auto need their own sample once the crawl reaches them (fewer than 100 sites so far).
