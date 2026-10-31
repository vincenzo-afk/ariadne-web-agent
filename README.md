# Ariadne

**An AI agent that navigates websites, understands a natural-language request, and accurately retrieves the specific information a user needs.**

Named for the thread that led Theseus through the labyrinth: Ariadne finds its way through a site, and leaves a trail back to exactly where each answer came from. Every answer comes with a verbatim quote and source URL. If it can't prove an answer, it says so instead of guessing.

```
$ ariadne "What is the price of A Light in the Attic?" https://books.toscrape.com/

verdict : FOUND
answer  : £51.77
source  : https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
quote   : "Price (excl. tax) £51.77"
```

## Quick start

```bash
pip install -e .                     # or: pip install -r requirements.txt
cp .env.example .env                 # optional: add an LLM key for harder questions

ariadne "Who wrote PEP 8?" https://peps.python.org/pep-0008/
ariadne "Can you tell me the latest version of requests on https://pypi.org/project/requests/?"
ariadne "When was Albert Einstein born?" https://quotes.toscrape.com/ --json
```

If the `ariadne` command isn't on your path, use `python -m ariadne` instead. Exit code is `0` when an answer was found and `2` otherwise.

| Option | Purpose |
|---|---|
| `--llm auto\|gemini\|none` | LLM use. `auto` uses a key from `.env` if one works, otherwise rules only |
| `--max-pages N`, `--max-seconds N` | Crawl budget (defaults: 12 pages, 120 s) |
| `--json` | Machine-readable report |
| `--no-render` | Don't use a headless browser for JavaScript pages |
| `--no-robots` | Ignore robots.txt |
| `--allow-private` | Permit local or private addresses, which are refused by default |

## How it meets the brief

| Requirement | How Ariadne does it | Code |
|---|---|---|
| **Navigate websites** | Tries the site's own search and sitemap, then follows the links most relevant to the question. Follows pagination, and skips logout and cart links | [agent.py](ariadne/agent.py), [scent.py](ariadne/scent.py) |
| **Understand a natural-language request** | Handles polite phrasing and all-caps. Works out the answer type, the named subject ("PEP 8") and the attribute asked about ("author") | [understand.py](ariadne/understand.py) |
| **Retrieve the specific information** | Reads JSON-LD, table rows, label/value pairs and sentences, and returns the value itself ("£51.77"), not the paragraph around it. An LLM is optional | [extract.py](ariadne/extract.py), [parse.py](ariadne/parse.py) |
| **Accurately** | Every candidate must pass the checks below | [verify.py](ariadne/verify.py) |

## What an answer must pass

1. **Type:** a "when" question needs a date, a "how much" question needs a number.
2. **Verbatim quote:** the quote must appear in text a reader can see, or in the page's declared metadata. Only case and typography may differ. HTML comments, scripts and hidden elements don't count.
3. **Grounding:** the answer's values must come from the quote.
4. **Subject:** the quote or the page's headline or URL must name the thing asked about, so one product's price isn't given for another.
5. **Entailment:** an independent LLM check, or a deterministic rule check when no LLM is available.

There are four possible verdicts: `found`, `not_found`, `blocked` (login wall, robots.txt, rate limit, refused address) and `out_of_budget`.

Each run also records its steps in a hash chain. This detects casual edits to a report, but anyone who can rewrite the whole chain can recompute it, so it is not a signature.

## Testing

```bash
pytest tests                         # offline, against local mock sites
python eval/run_eval.py --llm none   # live, against real public websites
```

See [AUDIT.md](AUDIT.md) for current results and known limitations.

## Project layout

```
ariadne/   agent  understand  fetch  parse  scent  extract  verify  llm  embed  ledger  models  cli
tests/     test_ariadne.py (core flows)   test_edge_cases.py (adversarial and edge cases)
eval/      run_eval.py + questions.jsonl (live websites, independently written answers)
```
