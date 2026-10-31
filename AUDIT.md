# Ariadne audit (2026-10-31)

Brief: "Build an AI agent that can navigate websites, understand a natural-language request and accurately retrieve the specific information a user needs."

## Results

Live run against PEP pages, Python docs, PyPI, Books to Scrape, Quotes to Scrape and Wikipedia. 26 questions, 10-page budget.

| Mode | Answerable questions retrieved | Unanswerable questions refused | Answers given that were correct | Median time |
|---|---|---|---|---|
| Rules only (no LLM) | 17/18 | 8/8 | 17/17 | 2.3 s |
| Gemini (`gemini-3.5-flash-lite`) | 16/18 | 8/8 | 16/16 | 6.8 s |

Before this audit, the old eval's own report showed 0% correct, although its labels were also unreliable.

Offline tests: 102 passed (`pytest tests`).

**How far to trust these numbers:** the rules were improved while looking at failures on these same 26 questions, so this is a development score, not a measure on unseen sites. Expect lower accuracy on new sites. One label was corrected during the audit (PEP 257's authors), because it was wrong. Run a fresh question set before quoting an accuracy figure externally.

## What was wrong and is now fixed

### Security
| Problem | Fix |
|---|---|
| A credential was pasted into `.env.example`, a tracked file, and `.env` had no `.gitignore` protection | Removed from the example file, `.gitignore` added. **The key still needs rotating (see below)** |
| Agent could be pointed or redirected at private addresses and cloud metadata | Private addresses refused by default, checked on every redirect hop |
| Response size was unlimited | Bodies capped at 5 MB |
| Crawler could follow logout, delete and cart links | Those links are skipped |
| Page text went to the LLM with no separation from instructions | Page text is marked as untrusted, and quotes that read as instructions are rejected |

### Accuracy
| Problem | Fix |
|---|---|
| `verify()` called the LLM, then ignored its reply | The reply now decides, and rate limits fall back to rules |
| Quotes could be matched against raw HTML, so comments and scripts counted as proof | Only visible text and declared metadata count |
| Hidden elements (`display:none`, `hidden`) were read as content | Removed before reading |
| 90% fuzzy matching accepted a quote with a changed number | Fuzzy matching removed |
| 404 and 500 pages could supply answers | Error pages are never evidence |
| Tables and label/value pairs were flattened, and answers were whole sentences | Pages are split into units, and the specific value is returned |
| A product's price could be given for a different product | New subject check |
| Yes/no logic matched text fragments (`piano,` read as a negative) and answered "no" when information was merely absent | Whole-word matching, and absence now means no answer |

### Navigation and understanding
| Problem | Fix |
|---|---|
| Only the first start URL was used | All are used |
| `www` to bare-domain redirects were blocked as off-site | Treated as the same site |
| Search results all got the top score, including menu links | Results are scored on relevance |
| Only the first 60 links on a page were considered, and pagination was missed | Up to 500 are considered, and "next" links are followed |
| `out_of_budget` was reported even with nothing left to explore | Reported only when promising links remain |
| Robots rules for `AriadneBot` were never applied, and the agent posed as Chrome | Rules apply, and it identifies itself honestly |
| "Can you tell me the price?" was treated as a yes/no question | Polite phrasing and all-caps are handled |

### Reliability
| Problem | Fix |
|---|---|
| **Gemini never worked: both models were retired, and the 404 was swallowed, so "Gemini" runs were really offline** | Model names updated, and LLM errors are now logged |
| `.env` was never loaded | The CLI loads it |
| A 400 from the fallback model crashed the run | Caught and logged |
| Non-ASCII output could crash on a Windows console | Handled |

### Misleading test evidence
"149/149 PASS" overstated coverage: 60 tests repeated one question, and 31 only checked that a fetch returned something. The "hostile" suite mostly searched the source for strings. These were replaced by adversarial tests that assert exact answers, and a live eval with answers written independently. Answerable questions had also been labelled as having no answer.

## Action needed from the project owner

1. **Rotate the Gemini API key.** It was in `.env.example`. It was never in a pushed commit, but it is in local Cline checkpoint refs (`refs/cline/checkpoints/*`), which still hold it.
2. **Gemini free-tier quota:** `gemini-3.8-flash` allows 20 requests a day on this key. Ariadne makes several calls per page, so it runs out quickly and falls back to rules.

## Known limitations

- **Wikipedia miss:** "Who is the father of Ariadne?" fails in both modes. The rules can't infer "father" from "daughter of Minos". Gemini should help here but didn't, and the cause wasn't diagnosed.
- **Gemini is not better yet:** it lost one question the rules answer (the book UPC, not diagnosed) and gained none. With this quota, rules-only is the more dependable mode.
- **Conflicting sources:** the first verified answer wins. Pages are not cross-checked against each other.
- **Not supported:** PDFs, image text, logins, and filling in forms other than simple GET search.
- **JavaScript pages:** the Playwright path has no automated test and wasn't exercised in this audit.
- **Rule extraction is English-centred:** other languages don't crash, but will mostly get no answer.
- **DNS rebinding** is not defended against, and browser-rendered pages check only the final URL. Add network isolation before hosting this as a public service.
- **Package name:** `ariadne` is already used on PyPI by an unrelated GraphQL library. Local install is fine, but choose another distribution name before publishing.
