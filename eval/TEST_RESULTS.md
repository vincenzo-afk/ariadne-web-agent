# Ariadne Test Suite — 30 Tests

**Run at:** 2026-10-31T02:21:10Z
**Mock server:** http://127.0.0.1:54988
**LLM mode:** gemini-2.5-flash (fallback: gemini-2.5-flash-lite)
**Summary:** 26/30 PASS, 4 FAIL, 0 SKIP, 0 ERROR

## Results

| ID | Name | Status | Evidence | Notes |
|---|---|---|---|---|
| A1 | Price via JSON-LD Product | PASS | verdict=found, answer="999 USD" | — |
| A2 | Yes/No via FAQ schema | PASS | answer="Yes, Phone X supports 15W wireless charging." | — |
| A3 | Absent attribute -> not_found | PASS | verdict=not_found | — |
| A4 | Fabricated quote rejected | PASS | ok=False | — |
| A5 | Login wall -> blocked | PASS | verdict=blocked | — |
| A6 | Ledger tamper-evident | PASS | chain ok then broken | — |
| A7 | Trail records every step | PASS | fetch/verify/answer present | — |
| A8 | Returns via site search | PASS | answer="30 days" | — |
| A9 | Gemini offline fallback works | PASS | verdict=found | no LLM calls |
| A10 | Out-of-budget verdict | PASS | verdict=out_of_budget | — |
| B1 | B1 | FAIL |  | json-ld:Product |
| B2 | reasoning_effort latency | PASS | elapsed=2.02s | — |
| B3 | response_format accepted | PASS | no extract-error | — |
| B4 | B4 | FAIL |  | deterministic-gate PASS (llm reply ambiguous; quote located in page text) |
| B5 | B5 | FAIL |  | quote-gate: quote not found verbatim on fetched page (best fuzzy 0.35); refusing to trust model memory |
| B6 | RateLimited fires once | PASS | rate-limited count=0 | — |
| B7 | --llm auto selects Gemini | PASS | gemini client used | — |
| B8 | Gemini cannot override quote gate | PASS | ok=False | — |
| C1 | C1 | FAIL |  | [Step(n=1, action='start', url='http://127.0.0.1:54988/', detail="q='' type=text hints=", ts=1793413264.9324179, prev_hash='GENESIS', hash='40b5ebdd0834a6338acaa1776d82b1cd588a7cd409c39092c950f689fc897cf1'), Step(n=2, action='fetch', url='http://127.0.0.1:54988/', detail='status=200 bytes=367 render |
| C2 | Ambiguous question | PASS | verdict=found | observed |
| C3 | Multi-part question | PASS | verdict=found | observed |
| C4 | All stop words | PASS | not_found | — |
| C5 | Answer only in image | PASS | verdict=not_found | — |
| C6 | JS-rendered only render disabled | PASS | verdict=found | — |
| C7 | Conflicting values | PASS | verdict=found | observed |
| C8 | Negated/conditional quote | PASS | verdict=found, answer=If you return within 30 days, you get a refund. | — |
| C9 | Malformed URL | PASS | blocked | — |
| C10 | 404 page | PASS | verdict=found | — |
| C11 | robots.txt disallow | PASS | verdict=blocked | reason=Start page blocked: disallowed by robots.txt |
| C12 | Slow page timeout | PASS | elapsed=3.0s, verdict=blocked | — |

## Failure Log

### B1 — B1 — FAIL
**Observed:** json-ld:Product

### B4 — B4 — FAIL
**Observed:** deterministic-gate PASS (llm reply ambiguous; quote located in page text)

### B5 — B5 — FAIL
**Observed:** quote-gate: quote not found verbatim on fetched page (best fuzzy 0.35); refusing to trust model memory

### C1 — C1 — FAIL
**Observed:** [Step(n=1, action='start', url='http://127.0.0.1:54988/', detail="q='' type=text hints=", ts=1793413264.9324179, prev_hash='GENESIS', hash='40b5ebdd0834a6338acaa1776d82b1cd588a7cd409c39092c950f689fc897cf1'), Step(n=2, action='fetch', url='http://127.0.0.1:54988/', detail='status=200 bytes=367 render
