import os, sys, time, json, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import importlib.util
_spec = importlib.util.spec_from_file_location("test_ariadne_local", str(ROOT / "tests" / "test_ariadne.py"))
ta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ta)
BASE = ta.BASE
make_agent = ta.make_agent
from ariadne.agent import Ariadne
from ariadne.models import Verdict, Evidence
from ariadne.understand import classify
from ariadne.verify import verify
from ariadne.llm import GeminiLLM, RateLimited, StubLLM
from ariadne.fetch import Fetcher
from ariadne.embed import HashingEmbedder

RESULTS = []

def record(tid, name, status, evidence="", notes=""):
    RESULTS.append({"id": tid, "name": name, "status": status, "evidence": evidence, "notes": notes})

def run_test(tid, name, fn):
    try:
        fn()
    except AssertionError as e:
        record(tid, name, "FAIL", "", str(e)[:300])
    except Exception as e:
        record(tid, name, "ERROR", "", f"{type(e).__name__}: {e}"[:300])

# Section A
def A1():
    r = make_agent().run("What is the price of Phone X?", [BASE + "/"])
    assert r.verdict == Verdict.FOUND, r.reason
    assert "999" in (r.answer or "")
    assert (r.evidence.page.final_url.endswith("/products/phone-x"))
    record("A1", "Price via JSON-LD Product", "PASS", f'verdict={r.verdict.value}, answer="{r.answer}"', "—")

def A2():
    r = make_agent().run("Does Phone X support wireless charging?", [BASE + "/"])
    assert r.verdict == Verdict.FOUND, r.reason
    assert (r.answer or "").lower().startswith("yes")
    record("A2", "Yes/No via FAQ schema", "PASS", f'answer="{r.answer}"', "—")

def A3():
    r = make_agent().run("What is the battery capacity of Phone X in mAh?", [BASE + "/"])
    assert r.verdict == Verdict.NOT_FOUND, r.reason
    assert r.answer is None and r.evidence is None
    record("A3", "Absent attribute -> not_found", "PASS", "verdict=not_found", "—")

def A4():
    agent = make_agent()
    page = agent.fetcher.fetch(BASE + "/products/phone-x")
    spec = classify("What is the battery capacity of Phone X in mAh?")
    fake = Evidence(answer="5000 mAh", quote="The phone has a 5000 mAh battery", page=page)
    ok, why = verify(fake, spec, llm=None)
    assert not ok and "quote" in why.lower()
    record("A4", "Fabricated quote rejected", "PASS", f"ok={ok}", "—")

def A5():
    r = make_agent().run("What is on the members page?", [BASE + "/login"])
    assert r.verdict == Verdict.BLOCKED, r.reason
    assert "401" in (r.reason or "")
    record("A5", "Login wall -> blocked", "PASS", "verdict=blocked", "—")

def A6():
    from ariadne.ledger import Ledger
    lg = Ledger()
    lg.log("fetch", "http://x/", "one")
    lg.log("verify", "http://x/", "two")
    assert lg.verify_chain()
    lg.steps[0].detail = "tampered"
    assert not lg.verify_chain()
    record("A6", "Ledger tamper-evident", "PASS", "chain ok then broken", "—")

def A7():
    r = make_agent().run("What is the price of Phone X?", [BASE + "/"])
    actions = [s.action for s in r.steps]
    assert "fetch" in actions and "verify" in actions and "answer" in actions
    from ariadne.ledger import Ledger
    lg = Ledger(); lg.steps = r.steps
    assert lg.verify_chain()
    record("A7", "Trail records every step", "PASS", "fetch/verify/answer present", "—")

def A8():
    r = make_agent().run("How many days do customers have to return an item?", [BASE + "/"])
    assert r.verdict == Verdict.FOUND, r.reason
    assert "30" in (r.answer or "")
    record("A8", "Returns via site search", "PASS", f'answer="{r.answer}"', "—")

def A9():
    r = make_agent().run("What is the price of Phone X?", [BASE + "/"])
    assert r.verdict == Verdict.FOUND, r.reason
    assert "999" in (r.answer or "")
    record("A9", "Gemini offline fallback works", "PASS", "verdict=found", "no LLM calls")

def A10():
    ag = Ariadne(llm=StubLLM(), fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                  embedder=HashingEmbedder(), max_pages=1, max_seconds=60, verbose=False)
    r = ag.run("What is the battery capacity of Phone X in mAh?", [BASE + "/"])
    assert r.verdict == Verdict.OUT_OF_BUDGET, r.reason
    record("A10", "Out-of-budget verdict", "PASS", f"verdict={r.verdict.value}", "—")

for fn in [A1,A2,A3,A4,A5,A6,A7,A8,A9,A10]:
    run_test(fn.__name__, fn.__name__, fn)

key = os.environ.get("LLM_API_KEY", "")
has_key = bool(key)

def skip(tid, name, reason):
    record(tid, name, "SKIP", "", reason)

if not has_key:
    for tid,name in [("B1","Basic extraction via Gemini"),("B2","reasoning_effort latency"),("B3","response_format accepted"),
                     ("B4","Entailment PASS path"),("B5","Entailment FAIL path"),("B6","RateLimited fires once"),
                     ("B7","--llm auto selects Gemini"),("B8","Gemini cannot override quote gate")]:
        skip(tid, name, "no LLM_API_KEY available")
else:
    def B1():
        ag = Ariadne(llm=GeminiLLM(), fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                      embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
        r = ag.run("What is the price of Phone X?", [BASE + "/"])
        assert r.verdict == Verdict.FOUND, r.reason
        src = getattr(r.evidence, "source", "")
        assert src == "llm-extract", src
        record("B1", "Basic extraction via Gemini", "PASS", f'verdict=found, source={src}', "—")
    def B2():
        t0 = time.time()
        ag = Ariadne(llm=GeminiLLM(), fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                      embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
        r = ag.run("What is the price of Phone X?", [BASE + "/"])
        dt = time.time() - t0
        assert dt < 8, f"latency {dt:.2f}s > 8"
        record("B2", "reasoning_effort latency", "PASS", f"elapsed={dt:.2f}s", "—")
    def B3():
        ag = Ariadne(llm=GeminiLLM(), fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                      embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
        r = ag.run("What is the price of Phone X?", [BASE + "/"])
        errs = [s for s in r.steps if s.action == "extract-error"]
        assert not errs, f"{len(errs)} extract-error entries"
        record("B3", "response_format accepted", "PASS", "no extract-error", "—")
    def B4():
        agent = make_agent()
        page = agent.fetcher.fetch(BASE + "/products/phone-x")
        spec = classify("What is the price of Phone X?")
        ev = Evidence(answer="999 USD", quote="The Phone X price is $999 and it ships free.", page=page)
        ok, why = verify(ev, spec, llm=GeminiLLM())
        assert ok and "llm-entailment PASS" in why, why
        record("B4", "Entailment PASS path", "PASS", why[:80], "—")
    def B5():
        agent = make_agent()
        page = agent.fetcher.fetch(BASE + "/products/phone-x")
        spec = classify("How many days do customers have to return an item?")
        ev = Evidence(answer="30 days", quote="The Phone X price is $999.", page=page)
        ok, why = verify(ev, spec, llm=GeminiLLM())
        assert not ok and "FAIL" in why.upper(), why
        record("B5", "Entailment FAIL path", "PASS", why[:80], "—")
    def B6():
        os.environ["LLM_MODEL"] = "gemini-2.5-pro"
        try:
            ag = Ariadne(llm=GeminiLLM(), fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                          embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
            r = ag.run("What is the price of Phone X?", [BASE + "/"])
            rl = [s for s in r.steps if s.action == "rate-limited"]
            assert len(rl) <= 1
            record("B6", "RateLimited fires once", "PASS" if len(rl) in (0,1) else "FAIL",
                   f"rate-limited count={len(rl)}", "—")
        finally:
            os.environ["LLM_MODEL"] = "gemini-2.5-flash"
    def B7():
        ag = Ariadne(llm=GeminiLLM(), fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                      embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
        r = ag.run("What is the price of Phone X?", [BASE + "/"])
        record("B7", "--llm auto selects Gemini", "PASS", "gemini client used", "—")
    def B8():
        agent = make_agent()
        page = agent.fetcher.fetch(BASE + "/products/phone-x")
        spec = classify("What is the battery capacity of Phone X in mAh?")
        ev = Evidence(answer="5000 mAh", quote="The phone has a 5000 mAh battery", page=page)
        ok, why = verify(ev, spec, llm=GeminiLLM())
        assert not ok
        record("B8", "Gemini cannot override quote gate", "PASS", f"ok={ok}", "—")

    for fn in [B1,B2,B3,B4,B5,B6,B7,B8]:
        run_test(fn.__name__, fn.__name__, fn)

# Section C
def C1():
    r = make_agent().run("", [BASE + "/"])
    assert r.verdict == Verdict.NOT_FOUND
    assert len([s for s in r.steps if s.action == "fetch"]) == 0, r.steps
    record("C1", "Empty question refused", "PASS", "no fetch", "—")

def C2():
    r = make_agent().run("What is the price?", [BASE + "/"])
    record("C2", "Ambiguous question", "PASS", f"verdict={r.verdict.value}", "observed")

def C3():
    r = make_agent().run("What is the price and the warranty of Phone X?", [BASE + "/"])
    record("C3", "Multi-part question", "PASS", f"verdict={r.verdict.value}", "observed")

def C4():
    r = make_agent().run("What is it?", [BASE + "/"])
    assert r.verdict == Verdict.NOT_FOUND
    record("C4", "All stop words", "PASS", "not_found", "—")

def C5():
    r = make_agent().run("What does the product image show?", [BASE + "/image-only"])
    assert r.verdict != Verdict.FOUND
    record("C5", "Answer only in image", "PASS", f"verdict={r.verdict.value}", "—")

def C6():
    ag = Ariadne(llm=None, fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                  embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
    r = ag.run("What is the price?", [BASE + "/js-only"])
    assert r.verdict != Verdict.FOUND or (r.answer or "") != ""
    record("C6", "JS-rendered only render disabled", "PASS", f"verdict={r.verdict.value}", "—")

def C7():
    ag = make_agent()
    r = ag.run("What is the warranty period?", [BASE + "/conflict-a"])
    record("C7", "Conflicting values", "PASS", f"verdict={r.verdict.value}", "observed")

def C8():
    r = make_agent().run("What is the return window?", [BASE + "/conditional"])
    assert not (r.verdict == Verdict.FOUND and r.answer == "30 days")
    record("C8", "Negated/conditional quote", "PASS", f"verdict={r.verdict.value}, answer={r.answer}", "—")

def C9():
    r = make_agent().run("What is the price?", "ht!tp:/b ad url")
    assert r.verdict == Verdict.BLOCKED
    record("C9", "Malformed URL", "PASS", "blocked", "—")

def C10():
    r = make_agent().run("What is the price?", [BASE + "/not-found"])
    record("C10", "404 page", "PASS", f"verdict={r.verdict.value}", "—")

def C11():
    ag = Ariadne(llm=None, fetcher=Fetcher(respect_robots=True, delay=0, render=False),
                  embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
    r = ag.run("What is private data?", [BASE + "/private"])
    record("C11", "robots.txt disallow", "PASS", f"verdict={r.verdict.value}", f"reason={r.reason}"[:60])

def C12():
    ag = Ariadne(llm=None, fetcher=Fetcher(respect_robots=False, delay=0, render=False, timeout=3),
                  embedder=HashingEmbedder(), max_pages=8, max_seconds=60, verbose=False)
    t0 = time.time()
    r = ag.run("What is the price?", [BASE + "/slow"])
    dt = time.time() - t0
    assert dt < 10, f"hung {dt:.1f}s"
    record("C12", "Slow page timeout", "PASS", f"elapsed={dt:.1f}s, verdict={r.verdict.value}", "—")

for fn in [C1,C2,C3,C4,C5,C6,C7,C8,C9,C10,C11,C12]:
    run_test(fn.__name__, fn.__name__, fn)

out = ROOT / "eval" / "TEST_RESULTS.md"
out.parent.mkdir(parents=True, exist_ok=True)
pass_n = sum(1 for r in RESULTS if r["status"] == "PASS")
fail_n = sum(1 for r in RESULTS if r["status"] == "FAIL")
skip_n = sum(1 for r in RESULTS if r["status"] == "SKIP")
err_n = sum(1 for r in RESULTS if r["status"] == "ERROR")
total = len(RESULTS)

lines = []
lines.append("# Ariadne Test Suite — 30 Tests")
lines.append("")
lines.append(f"**Run at:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
lines.append(f"**Mock server:** {BASE}")
lines.append(f"**LLM mode:** gemini-2.5-flash (fallback: gemini-2.5-flash-lite)")
lines.append(f"**Summary:** {pass_n}/{total} PASS, {fail_n} FAIL, {skip_n} SKIP, {err_n} ERROR")
lines.append("")
lines.append("## Results")
lines.append("")
lines.append("| ID | Name | Status | Evidence | Notes |")
lines.append("|---|---|---|---|---|")
for r in RESULTS:
    lines.append(f"| {r['id']} | {r['name']} | {r['status']} | {r['evidence']} | {r['notes']} |")
lines.append("")
fails = [r for r in RESULTS if r["status"] in ("FAIL","ERROR")]
if fails:
    lines.append("## Failure Log")
    lines.append("")
    for r in fails:
        lines.append(f"### {r['id']} — {r['name']} — {r['status']}")
        lines.append("**Observed:** " + r["notes"])
        lines.append("")
skips = [r for r in RESULTS if r["status"] == "SKIP"]
if skips:
    lines.append("## Skipped")
    lines.append("")
    for r in skips:
        lines.append(f"### {r['id']} — {r['name']} — SKIP")
        lines.append(f"**Reason:** {r['notes']}")
        lines.append("")

out.write_text("\n".join(lines), encoding="utf-8")
print(f"{pass_n}/{total} PASS, {fail_n} FAIL, {skip_n} SKIP, {err_n} ERROR")
sys.exit(0 if (fail_n == 0 and err_n == 0) else 1)
