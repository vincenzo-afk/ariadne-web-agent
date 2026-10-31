"""Live evaluation of Ariadne against real public websites.
Each question's accepted answers were written from outside knowledge, not from
the agent's output. accept=null means the site does not hold the answer, so the
only correct behaviour is to abstain.

Run: python eval/run_eval.py [--llm none|gemini|both]"""
import argparse
import datetime
import json
import pathlib
import re
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ariadne.agent import Ariadne
from ariadne.cli import load_dotenv
from ariadne.embed import HashingEmbedder
from ariadne.fetch import Fetcher
from ariadne.llm import GeminiLLM


def answerable(q):
    return q.get("accept") is not None or "accept_regex" in q


def is_correct(q, verdict, answer):
    if not answerable(q):
        return verdict in ("not_found", "out_of_budget")
    if verdict != "found":
        return False
    if "accept_regex" in q:
        return bool(re.search(q["accept_regex"], answer))
    return any(a.lower() in answer.lower() for a in q["accept"])


def run_config(name, llm, questions):
    rows = []
    for q in questions:
        t0 = time.time()
        fetcher = Fetcher(delay=0.5, timeout=20, render=False)
        rep = Ariadne(llm=llm, fetcher=fetcher, embedder=HashingEmbedder(), max_pages=10,
                      max_seconds=90, verbose=False).run(q["question"], q["start_urls"])
        answer, verdict = rep.answer or "", rep.verdict.value
        row = {"id": q["id"], "config": name, "verdict": verdict, "answer": answer,
               "answerable": answerable(q), "correct": is_correct(q, verdict, answer),
               "seconds": round(time.time() - t0, 1),
               "source": rep.evidence.page.final_url if rep.evidence else "",
               "quote": rep.evidence.quote[:200] if rep.evidence else "", "reason": rep.reason}
        rows.append(row)
        print(f"{'OK ' if row['correct'] else 'BAD'} {name:6} {q['id']:14} {verdict:13} "
              f"{answer[:70]!r} <- {row['source'][:60]}", flush=True)
    return rows


def summary(rows):
    ans = [r for r in rows if r["answerable"]]
    neg = [r for r in rows if not r["answerable"]]
    found = [r for r in rows if r["verdict"] == "found"]
    return {
        "retrieved": f"{sum(r['correct'] for r in ans)}/{len(ans)}",
        "abstained": f"{sum(r['correct'] for r in neg)}/{len(neg)}",
        "precision": f"{sum(r['correct'] for r in found)}/{len(found)}",
        "blocked": sum(r["verdict"] == "blocked" for r in rows),
        "median_s": statistics.median(r["seconds"] for r in rows),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["none", "gemini", "both"], default="both")
    a = ap.parse_args()
    load_dotenv(ROOT / ".env")
    qs = [json.loads(l) for l in open(ROOT / "eval" / "questions.jsonl", encoding="utf-8") if l.strip()]
    report, rows = {}, []
    if a.llm in ("none", "both"):
        r = run_config("none", None, qs)
        rows += r
        report["none"] = summary(r)
    if a.llm in ("gemini", "both"):
        r = run_config("gemini", GeminiLLM(), qs)
        rows += r
        report["gemini"] = summary(r)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (ROOT / "eval" / f"results_{ts}.json").write_text(
        json.dumps({"summary": report, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
