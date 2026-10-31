import time
from urllib.parse import quote_plus, urljoin, urlparse

from .extract import llm_candidates, rule_candidates, structured_candidates
from .fetch import Fetcher
from .llm import RateLimited
from .ledger import Ledger
from .models import AgentReport, Link, Verdict
from .parse import extract_links, find_site_search, parse_sitemap_urls
from .scent import scent_score
from .understand import classify
from .verify import verify


def same_site(url, base):
    a = urlparse(url).netloc.lower()
    b = urlparse(base).netloc.lower()
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b)


class Ariadne:
    """Grounded website-QA agent.

    Invariants (what makes it strong):
      1. A quote must be found VERBATIM in a page the agent actually fetched.
      2. The answer must pass a type gate and an independent entailment gate.
      3. If no candidate passes all gates, the verdict is 'not_found' - never a guess.
      4. Every step is recorded in a hash-chained evidence ledger.
    """

    def __init__(self, llm=None, fetcher=None, embedder=None,
                 max_pages=12, max_seconds=120, scent_threshold=0.28,
                 sitemap_top=10, verbose=True):
        if embedder is None:
            from .embed import default_embedder
            embedder = default_embedder()
        self.llm = llm
        self.fetcher = fetcher or Fetcher()
        self.embedder = embedder
        self.max_pages = max_pages
        self.max_seconds = max_seconds
        self.scent_threshold = scent_threshold
        self.sitemap_top = sitemap_top
        self.verbose = verbose

    def _say(self, msg):
        if self.verbose:
            print(f"  · {msg}")

    def run(self, question, start_urls):
        t0 = time.time()
        if isinstance(start_urls, str):
            start_urls = [start_urls]
        spec = classify(question)
        ledger = Ledger()
        ledger.log("start", start_urls[0],
                   f"q='{question[:120]}' type={spec.type.value} hints={','.join(spec.hints[:8])}")
        self._say(f"question type={spec.type.value}, hints={spec.hints[:8]}")
        qvec = self.embedder.embed([spec.question + " " + " ".join(spec.hints)])[0]

        visited, queue = set(), []
        pages_fetched = 0

        def push(url, score, reason):
            if url in visited or any(u == url for _, u, _ in queue):
                return
            queue.append((score, url, reason))
            queue.sort(key=lambda x: -x[0])

        def budget_left():
            return pages_fetched < self.max_pages and (time.time() - t0) < self.max_seconds

        def attempt(page):
            cands = []
            try:
                cands.extend(structured_candidates(page, spec))
            except Exception as e:
                ledger.log("extract-error", page.final_url, repr(e)[:120])
            if not cands and self.llm is not None:
                try:
                    cands.extend(llm_candidates(page, spec, self.llm, self.embedder, qvec))
                except RateLimited:
                    ledger.log("rate-limited", page.final_url, "primary model exhausted")
                    fb = getattr(self.llm, "fallback_model", None)
                    if fb and fb != getattr(self.llm, "model", None):
                        primary = self.llm.model
                        self.llm.model = fb
                        try:
                            cands.extend(llm_candidates(page, spec, self.llm, self.embedder, qvec))
                        except RateLimited:
                            ledger.log("llm-unavailable", page.final_url, "fallback exhausted")
                        finally:
                            self.llm.model = primary
                    else:
                        ledger.log("llm-unavailable", page.final_url, "no fallback configured")
                except Exception as e:
                    ledger.log("extract-error", page.final_url, repr(e)[:120])
            if not cands:
                cands.extend(rule_candidates(page, spec))
            seen = set()
            for cand in cands:
                key = (cand.quote[:120], cand.answer[:120])
                if key in seen:
                    continue
                seen.add(key)
                ok, why = verify(cand, spec, self.llm)
                ledger.log("verify", page.final_url,
                           f"{'PASS' if ok else 'FAIL'} [{cand.source}] {why[:130]}")
                if ok:
                    cand.verified_by = why
                    return cand
            return None

        # ---- seed page ----
        start = start_urls[0]
        page = self.fetcher.fetch(start)
        pages_fetched += 1
        if page.blocked:
            ledger.log("blocked", start, page.block_reason)
            return AgentReport(Verdict.BLOCKED, steps=ledger.steps,
                               reason=f"Start page blocked: {page.block_reason}")
        ledger.log("fetch", page.final_url,
                   f"status={page.status} bytes={len(page.html)} rendered={page.rendered}")
        ev = attempt(page)
        if ev:
            return self._found(ev, ledger, t0)
        visited.add(start)
        visited.add(page.final_url)

        # ---- reconnaissance: site search, sitemap, outbound links ----
        search_tpl = find_site_search(page.html, page.final_url)
        if search_tpl and budget_left():
            q = " ".join(spec.hints[:8])
            sp = self.fetcher.fetch(search_tpl.replace("{q}", quote_plus(q)))
            pages_fetched += 1
            if not sp.blocked:
                ledger.log("site-search", sp.final_url, f"query='{q}'")
                self._say(f"site-search '{q}' -> {sp.final_url}")
                for lk in extract_links(sp.html, sp.final_url)[:15]:
                    if same_site(lk.url, start):
                        push(lk.url, 1.0, "site-search")
            visited.add(sp.final_url)

        if budget_left():
            sm = self.fetcher.fetch(urljoin(page.final_url, "/sitemap.xml"))
            pages_fetched += 1
            if not sm.blocked and sm.status == 200 and "<loc>" in sm.html:
                urls = parse_sitemap_urls(sm.html)
                ledger.log("sitemap", sm.final_url, f"{len(urls)} urls")
                links = [Link(url=u, anchor="", context="sitemap") for u in urls if same_site(u, start)]
                scored = sorted(links, key=lambda lk: scent_score(lk, qvec, self.embedder, spec), reverse=True)
                for lk in scored[: self.sitemap_top]:
                    sc = scent_score(lk, qvec, self.embedder, spec)
                    if sc >= self.scent_threshold - 0.10 or len(queue) < 3:
                        push(lk.url, sc, "sitemap")
            visited.add(sm.final_url)

        for lk in extract_links(page.html, page.final_url)[:60]:
            if same_site(lk.url, start):
                push(lk.url, scent_score(lk, qvec, self.embedder, spec), "link")

        # ---- scent-guided crawl ----
        while queue and budget_left():
            score, url, why = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            page = self.fetcher.fetch(url)
            pages_fetched += 1
            if page.blocked:
                ledger.log("blocked", url, page.block_reason)
                continue
            ledger.log("fetch", page.final_url, f"status={page.status} score={score:.2f} via={why}")
            self._say(f"[{pages_fetched}/{self.max_pages}] {page.final_url} (scent {score:.2f}, {why})")
            ev = attempt(page)
            if ev:
                return self._found(ev, ledger, t0)
            for lk in extract_links(page.html, page.final_url)[:40]:
                if same_site(lk.url, start):
                    sc = scent_score(lk, qvec, self.embedder, spec)
                    if sc >= self.scent_threshold:
                        push(lk.url, sc, "link")
            visited.add(page.final_url)

        if pages_fetched >= self.max_pages:
            verdict, reason = Verdict.OUT_OF_BUDGET, f"Page budget exhausted ({pages_fetched} pages visited)."
        else:
            verdict = Verdict.NOT_FOUND
            reason = (f"No page within reach contained a verifiable answer "
                      f"({pages_fetched} pages visited in {time.time() - t0:.1f}s).")
        ledger.log("give-up", "", reason)
        self._say(reason)
        return AgentReport(verdict, steps=ledger.steps, reason=reason)

    def _found(self, ev, ledger, t0):
        ledger.log("answer", ev.page.final_url, f"answer='{ev.answer[:100]}' [{ev.source}]")
        self._say(f"FOUND: {ev.answer}  (proof: {ev.page.final_url})")
        return AgentReport(Verdict.FOUND, answer=ev.answer, evidence=ev,
                           steps=ledger.steps, reason=f"Verified answer in {time.time() - t0:.1f}s")

