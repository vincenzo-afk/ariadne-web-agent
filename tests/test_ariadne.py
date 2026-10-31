"""Offline end-to-end tests for Ariadne against a mock website.
Runs with plain `python tests/test_ariadne.py` (no pytest required),
and also works under pytest."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

HOME = """<!doctype html><html><head><title>Acme Store</title></head><body>
<nav><a href="/">Home</a><a href="/products/phone-x">Phone X</a>
<a href="/support">Support</a><a href="/about">About</a></nav>
<h1>Welcome to Acme</h1>
<form action="/search" method="get"><input name="q" type="text"><button>Go</button></form>
<p>We sell fine phones and accessories.</p></body></html>"""

PHONE = """<!doctype html><html><head><title>Phone X - Acme Store</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Phone X","brand":{"@type":"Brand","name":"Acme"},
 "offers":{"@type":"Offer","price":"999","priceCurrency":"USD","availability":"https://schema.org/InStock"}}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
 {"@type":"Question","name":"Does Phone X support wireless charging?",
  "acceptedAnswer":{"@type":"Answer","text":"Yes, Phone X supports 15W wireless charging."}}]}
</script></head>
<body><h1>Phone X</h1>
<p>The Phone X price is $999 and it ships free.</p>
<p>Phone X features a 6.7 inch display and dual cameras.</p>
<p>Yes, Phone X supports 15W wireless charging.</p>
</body></html>"""

SUPPORT = """<html><head><title>Support - Acme</title></head><body><h1>Support</h1>
<p>Returns are accepted within 30 days of delivery.</p>
<p>Support hours are 9am to 6pm on weekdays.</p></body></html>"""

ABOUT = """<html><head><title>About</title></head><body><h1>About Acme</h1>
<p>Acme was founded in 2001 and sells electronics worldwide.</p></body></html>"""


def search_page(base, q):
    if "wireless" in q or "phone" in q or "return" in q:
        return f'<html><body><h1>Results</h1><a href="{base}/products/phone-x">Phone X</a></body></html>'
    return f'<html><body><h1>Results</h1><a href="{base}/support">Support</a></body></html>'


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        parsed = urlparse(self.path)
        path, q = parsed.path, parse_qs(parsed.query).get("q", [""])[0]
        if path == "/login":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="members"')
            self.end_headers()
            self.wfile.write(b"login required")
            return
        if path == "/slow":
            time.sleep(10)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><p>slow page</p></body></html>")
            return
        if path == "/not-found":
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>404 Not Found</h1></body></html>")
            return
        if path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow: /private\n")
            return
        body = {
            "/": HOME,
            "/products/phone-x": PHONE,
            "/support": SUPPORT,
            "/about": ABOUT,
            "/sitemap.xml": ("<?xml version='1.0'?><urlset>"
                             f"<url><loc>{base}/</loc></url>"
                             f"<url><loc>{base}/products/phone-x</loc></url>"
                             f"<url><loc>{base}/support</loc></url>"
                             f"<url><loc>{base}/about</loc></url></urlset>"),
            "/search": search_page(base, q),
            "/image-only": "<html><body><img src=\"answer.png\"></body></html>",
            "/js-only": "<html><body><div id=\"root\"></div>" + "<script></script>"*8 + "</body></html>",
            "/conflict-a": "<html><body><p>The warranty is 2 years.</p></body></html>",
            "/conflict-b": "<html><body><p>The warranty is 3 years.</p></body></html>",
            "/conditional": "<html><body><p>If you return within 30 days, you get a refund.</p></body></html>",
            "/private": "<html><body><p>private data</p></body></html>",
            "/empty-question-page": "<html><body></body></html>",
        }.get(path, ABOUT)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


_server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
threading.Thread(target=_server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{_server.server_port}"


def make_agent():
    from ariadne.agent import Ariadne
    from ariadne.embed import HashingEmbedder
    from ariadne.fetch import Fetcher
    from ariadne.llm import StubLLM
    return Ariadne(llm=StubLLM(),
                   fetcher=Fetcher(respect_robots=False, delay=0, render=False),
                   embedder=HashingEmbedder(),
                   max_pages=8, max_seconds=60, verbose=False)


# ---------------- the tests ----------------

def test_price_question_found_with_proof():
    r = make_agent().run("What is the price of Phone X?", [BASE + "/"])
    assert r.verdict.value == "found", r.reason
    assert "999" in r.answer
    assert "999" in r.evidence.quote
    assert r.evidence.page.final_url.endswith("/products/phone-x")


def test_yesno_question_from_faq_schema():
    r = make_agent().run("Does Phone X support wireless charging?", [BASE + "/"])
    assert r.verdict.value == "found", r.reason
    assert r.answer.lower().startswith("yes")


def test_returns_question_via_site_search():
    r = make_agent().run("How many days do customers have to return an item?", [BASE + "/"])
    assert r.verdict.value == "found", r.reason
    assert "30" in r.answer


def test_not_found_instead_of_guessing():
    # The site has a price ($999) on the Phone X page - a weak agent would
    # grab that number. The correct behaviour is to abstain: battery capacity
    # is nowhere on the site.
    r = make_agent().run("What is the battery capacity of Phone X in mAh?", [BASE + "/"])
    assert r.verdict.value == "not_found", f"agent guessed: {r.answer!r}"
    assert r.answer is None
    assert r.evidence is None


def test_quote_integrity_fabricated_quote_rejected():
    from ariadne.extract import structured_candidates
    from ariadne.models import Evidence
    from ariadne.understand import classify
    from ariadne.verify import verify
    agent = make_agent()
    page = agent.fetcher.fetch(BASE + "/products/phone-x")
    spec = classify("What is the battery capacity of Phone X in mAh?")
    fake = Evidence(answer="5000 mAh", quote="The phone has a 5000 mAh battery", page=page)
    ok, why = verify(fake, spec, llm=None)
    assert not ok
    assert "quote" in why.lower()
    # structured extraction must not produce anything for this question either
    assert structured_candidates(page, spec) == []


def test_blocked_verdict_on_login_wall():
    r = make_agent().run("What is on the members page?", [BASE + "/login"])
    assert r.verdict.value == "blocked"
    assert "401" in r.reason


def test_ledger_is_tamper_evident():
    from ariadne.ledger import Ledger
    lg = Ledger()
    lg.log("fetch", "http://x/", "one")
    lg.log("verify", "http://x/", "two")
    assert lg.verify_chain()
    lg.steps[0].detail = "tampered"
    assert not lg.verify_chain()


def test_trail_records_every_step():
    r = make_agent().run("What is the price of Phone X?", [BASE + "/"])
    actions = [s.action for s in r.steps]
    assert "fetch" in actions and "verify" in actions and "answer" in actions
    from ariadne.ledger import Ledger
    lg = Ledger()
    lg.steps = r.steps
    assert lg.verify_chain()


def test_gemini_offline_fallback_still_passes():
    # With no LLM_API_KEY (StubLLM), the deterministic path must still find
    # the price question. This proves Gemini is optional, not required.
    r = make_agent().run("What is the price of Phone X?", [BASE + "/"])
    assert r.verdict.value == "found"
    assert "999" in r.answer


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {e.__class__.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} tests passed")
    return passed == len(fns)


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)

