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

## What the reviews taught (for scoring)

- **`emergency_247_claim` is true for ~half of all trade websites.** It is accurate but barely separates leads.
  Expect it to get a low weight once outcomes arrive.
- **`leave_message_text` is rare (<1% of sites) but strong:** these businesses state on their own site that
  callers reach voicemail. It's the most direct evidence of the pain we sell against.
- **`promises_live_answer` (caution flag)**: businesses that market "a real person always answers". Not
  disqualified, but likely a harder sell. Track its conversion in Stage 6.
- **Customer reviews on business sites are evidence too** ("left an after-hours voicemail"), but only when they
  describe *this* business, not a competitor.
