import argparse
import json
import os
import sys

from .agent import Ariadne
from .embed import HashingEmbedder, STEmbedder
from .fetch import Fetcher
from .llm import GeminiLLM, OpenAICompatLLM, RateLimited
from .models import Verdict


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="ariadne",
        description="Grounded website-QA agent: answers only with verbatim proof, else says 'not found'.")
    p.add_argument("question")
    p.add_argument("urls", nargs="+", help="one or more starting URLs")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.add_argument("--no-robots", action="store_true", help="ignore robots.txt (be responsible)")
    p.add_argument("--no-render", action="store_true", help="disable headless-browser rendering")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests per host")
    p.add_argument("--max-pages", type=int, default=12)
    p.add_argument("--llm", choices=["auto", "none", "gemini"], default="auto")
    p.add_argument("--embedder", choices=["auto", "hash", "st"], default="auto")
    a = p.parse_args(argv)

    llm = None
    if a.llm == "gemini":
        try:
            candidate = GeminiLLM()
        except RuntimeError as e:
            print(f"[ariadne] {e}", file=sys.stderr)
            sys.exit(1)
        try:
            candidate.complete("Reply with exactly: OK", max_tokens=5)
        except RateLimited:
            print("[ariadne] Gemini rate-limited during smoke test; "
                  "running in deterministic offline mode.", file=sys.stderr)
            candidate = None
        except Exception as e:
            print(f"[ariadne] LLM unavailable ({e.__class__.__name__}); "
                  "running in deterministic offline mode.", file=sys.stderr)
            candidate = None
        llm = candidate
    elif a.llm == "auto":
        base_url = os.environ.get("LLM_BASE_URL", "")
        api_key = os.environ.get("LLM_API_KEY", "")
        use_gemini = api_key.startswith("AIzaSy") or "generativelanguage.googleapis.com" in base_url
        try:
            if use_gemini:
                candidate = GeminiLLM()
            else:
                candidate = OpenAICompatLLM()
            candidate.complete("Reply with exactly: OK", max_tokens=5)
            llm = candidate
        except RateLimited:
            print("[ariadne] LLM rate-limited during smoke test; "
                  "running in deterministic offline mode.", file=sys.stderr)
        except Exception as e:
            print(f"[ariadne] LLM unavailable ({e.__class__.__name__}); "
                  "running in deterministic offline mode.", file=sys.stderr)
    # none: unchanged

    embedder = None
    if a.embedder == "hash":
        embedder = HashingEmbedder()
    elif a.embedder == "st":
        embedder = STEmbedder()

    fetcher = Fetcher(respect_robots=not a.no_robots, delay=a.delay, render=not a.no_render)
    agent = Ariadne(llm=llm, fetcher=fetcher, embedder=embedder,
                    max_pages=a.max_pages, verbose=not a.json)
    report = agent.run(a.question, a.urls)

    if a.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.pretty())
    sys.exit(0 if report.verdict is Verdict.FOUND else 2)


if __name__ == "__main__":
    main()
