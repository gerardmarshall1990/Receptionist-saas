# Stage 6 — pilot experiment set-up (pilot1)

**Question:** do scored leads beat a random, unscored list on demos booked per lead dialled?
**Status:** pilot1 is locked and its call list is ready. The verdict waits for your calls:
`leads experiment readout pilot1` currently reports "not enough data yet".

## Design (all defaults in `config/experiment.yaml`)

| | |
|---|---|
| Arms | **scored**: tier A/B leads passing the Stage 5 export gate · **control**: a random draw from every lead passing the safety rules only |
| Size | 1,000 per arm, 2,000 leads in all |
| Balance | both arms get the same category quotas (dental 321 · plumbing 368 · HVAC 311), so category can't explain a difference |
| Randomness | sha256(seed + id), seed `pilot1-2026-11-21`: anyone can reproduce the picks |
| Frozen | assignments are written once; a database trigger refuses any edit or delete |
| Blinding | one shuffled call list, with no arm, tier, score, reason, opener or website column (a blank website would give the control arm away) |
| Protocol | up to 3 attempts per lead in different call windows; same script for everyone; no openers (one variable at a time) |
| Primary metric | demo booked per lead dialled; secondary: reached, conversation, trial, paid, data accuracy |
| Verdict rule | no winner below 300 dialled per arm; a winner only at p < 0.05 (two-sided) |

**Control = "the list you'd otherwise buy".** It uses no score information (no tier, reasons, disqualifiers or
crawl-derived flags). The phone only has to be a valid listed business number, which may be `unverified`. All
safety rules still apply: suppression, US only, no toll-free/fake/foreign numbers, no phone shared with another
business, allowed source and license. The control pool is 223,723 leads.

What landed in each arm:

| Arm | A | B | C | D | Site crawled | Phone confirmed on own site |
|---|---:|---:|---:|---:|---:|---:|
| Scored | 195 | 805 | 0 | 0 | 100% | 100% |
| Control | 5 | 16 | 46 | 933 | 11% | 5% |

The control arm holds its natural share of good leads (21 of 1,000 are A/B), as a random list should.

## Sample size

| To detect | Dialled per arm |
|---|---:|
| Demo rate 2% -> 4% | 1,141 |
| Demo rate 2% -> 5% | ~550 |
| Reach rate 30% -> 40% | 356 |

1,000 per arm detects a doubling of demo rate if the baseline is about 2.5% or higher. A smaller lift, or a
lower baseline, will read "no significant difference yet" and say how many more calls are needed.

## Running it

```bash
# callers: data/experiments/pilot1/call_list.csv + PROTOCOL.md (outcomes_template.csv shows the format)
uv run leads outcomes import calls.csv --experiment pilot1 --source <dialer>   # repeatable; bad rows stop it
uv run leads experiment call-list pilot1     # refresh the list: drops anyone suppressed since
uv run leads experiment readout pilot1       # docs/reports/stage6-readout-pilot1.md
```

- `do_not_call` outcomes are suppressed permanently on import (business and phone).
- The explorer shows each lead's call history but never its arm.

## Tested

- **Statistics vs hand-computed values:** Wilson intervals, two-proportion p (4% vs 2% at n=1,000 -> p = 0.0088),
  sample size (2% -> 4% = 1,141).
- **Verdict rules:** not enough data, scored wins, control wins, no difference.
- **Control profile:** ignores score but keeps every safety rule.
- **Arms:** balanced by category, non-overlapping, reproducible from the seed, and frozen (edits raise).
- **Call list:** blinded (no column that differs by arm).
- **Import:** validated (time zone, disposition, unknown lead, yes/no), idempotent, `do_not_call` -> suppression and
  removal from the refreshed list; bad files write nothing.
- **End-to-end readout** on simulated outcomes.

## Not done / gaps

- **No real outcomes yet:** the verdict and the scale-out decision need your calls (about 2,000 leads, up to 3 attempts each).
- **The import format is generic CSV.** Mapping a specific dialer or CRM export is a small add once you name the tool.
- **Signal-level results are descriptive only** at this size; refitting weights is Stage 8.
