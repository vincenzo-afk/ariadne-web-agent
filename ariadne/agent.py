import time
from urllib.parse import quote_plus, urljoin

from .extract import llm_candidates, rule_candidates, structured_candidates
from .fetch import Fetcher
from .llm import RateLimited
from .ledger import Ledger
from .models import AgentReport, Link, Verdict
from .parse import SKIP_EXT, extract_links, find_site_search, normalize_url, parse_sitemap_urls, same_site
from .scent import score_links
from .understand import classify
from .verify import verify


class Ariadne:
    """Grounded website-QA agent.

    Invariants (what makes it strong):
      1. A quote must be found VERBATIM in a page the agent actually fetched.
      2. The answer must pass type, grounding, subject and entailment gates.
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
        seeds = [u for u in (normalize_url(s, s) for s in (start_urls or [])) if u]
        ledger.log("start", seeds[0] if seeds else "",
                   f"q='{(question or '')[:120]}' type={spec.type.value} hints={','.join(spec.hints[:8])}")
        if not seeds:
            ledger.log("give-up", "", "no valid http(s) start URL")
            return AgentReport(Verdict.BLOCKED, steps=ledger.steps, reason="No valid http(s) start URL was given.")
        if not spec.hints:
            ledger.log("give-up", seeds[0], "question names nothing to look for")
            return AgentReport(Verdict.NOT_FOUND, steps=ledger.steps,
                               reason="The question names nothing to look for; please rephrase it.")
        self._say(f"question type={spec.type.value}, hints={spec.hints[:8]}")
        qvec = self.embedder.embed([spec.question + " " + " ".join(spec.hints)])[0]

        visited, queue = set(), []
        pages_fetched = 0

        def on_site(url):
            return any(same_site(url, s) for s in seeds)

        def is_subdomain(url):
            return not any(same_site(s, url) for s in seeds)

        def push(url, score, reason):
            if url in visited or any(u == url for _, u, _ in queue):
                return
            queue.append((score, url, reason))
            queue.sort(key=lambda x: -x[0])

        def push_links(links, reason, floor=None, boost=0.0):
            links = [lk for lk in links if on_site(lk.url)]
            for lk, sc in zip(links, score_links(links, qvec, self.embedder, spec)):
                # A subdomain (blog., forum.) is further from the site's own statement of fact.
                sc = sc + boost - (0.1 if is_subdomain(lk.url) else 0.0)
                if floor is None or sc >= floor or lk.context == "next-page":
                    push(lk.url, sc, reason)

        def budget_left():
            return pages_fetched < self.max_pages and (time.time() - t0) < self.max_seconds

        def attempt(page):
            if page.status >= 400 or not page.html:
                return None
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
                        except Exception as e:
                            ledger.log("llm-error", page.final_url, repr(e)[:200])
                        finally:
                            self.llm.model = primary
                    else:
                        ledger.log("llm-unavailable", page.final_url, "no fallback configured")
                except Exception as e:
                    ledger.log("llm-error", page.final_url, repr(e)[:200])
                    self._say(f"LLM error, using rules only: {e.__class__.__name__}")
            try:
                cands.extend(rule_candidates(page, spec))
            except Exception as e:
                ledger.log("extract-error", page.final_url, repr(e)[:120])
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

        # ---- seed pages ----
        seed_pages, block_reasons = [], []
        for start in seeds:
            if not budget_left():
                break
            page = self.fetcher.fetch(start)
            pages_fetched += 1
            visited.update((start, page.final_url))
            if page.blocked:
                ledger.log("blocked", start, page.block_reason)
                block_reasons.append(page.block_reason)
                continue
            ledger.log("fetch", page.final_url,
                       f"status={page.status} bytes={len(page.html)} rendered={page.rendered}")
            ev = attempt(page)
            if ev:
                return self._found(ev, ledger, t0)
            seed_pages.append(page)
        if not seed_pages:
            return AgentReport(Verdict.BLOCKED, steps=ledger.steps,
                               reason=f"Start page blocked: {'; '.join(block_reasons) or 'no budget'}")

        # ---- reconnaissance: site search, sitemap, outbound links ----
        page = seed_pages[0]
        search_tpl = find_site_search(page.html, page.final_url)
        if search_tpl and budget_left():
            q = " ".join(spec.hints[:8])
            sp = self.fetcher.fetch(search_tpl.replace("{q}", quote_plus(q)))
            pages_fetched += 1
            if not sp.blocked:
                ledger.log("site-search", sp.final_url, f"query='{q}'")
                self._say(f"site-search '{q}' -> {sp.final_url}")
                push_links(extract_links(sp.html, sp.final_url)[:30], "site-search", boost=0.5)
            visited.add(sp.final_url)

        if budget_left():
            sm = self.fetcher.fetch(urljoin(page.final_url, "/sitemap.xml"))
            pages_fetched += 1
            visited.add(sm.final_url)
            urls = []
            if not sm.blocked and sm.status == 200 and "<loc>" in sm.html:
                urls = parse_sitemap_urls(sm.html)
                if "<sitemapindex" in sm.html.lower():
                    children = urls[:2]
                    urls = []
                    for child in children:
                        if not budget_left():
                            break
                        cp = self.fetcher.fetch(child)
                        pages_fetched += 1
                        if not cp.blocked:
                            urls.extend(parse_sitemap_urls(cp.html))
                ledger.log("sitemap", sm.final_url, f"{len(urls)} urls")
            links = [Link(url=u, anchor="", context="sitemap") for u in urls[:3000]
                     if on_site(u) and not u.lower().endswith(SKIP_EXT + (".xml", ".gz"))]
            if links:
                ranked = sorted(zip(score_links(links, qvec, self.embedder, spec), links),
                                key=lambda x: -x[0])
                for sc, lk in ranked[: self.sitemap_top]:
                    if sc >= self.scent_threshold - 0.10 or len(queue) < 3:
                        push(lk.url, sc, "sitemap")

        for sp in seed_pages:
            push_links(extract_links(sp.html, sp.final_url)[:500], "link")

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
            if page.final_url in visited and page.final_url != url:
                continue
            visited.add(page.final_url)
            ledger.log("fetch", page.final_url, f"status={page.status} score={score:.2f} via={why}")
            self._say(f"[{pages_fetched}/{self.max_pages}] {page.final_url} (scent {score:.2f}, {why})")
            ev = attempt(page)
            if ev:
                return self._found(ev, ledger, t0)
            if page.status < 400:
                push_links(extract_links(page.html, page.final_url)[:500], "link", floor=self.scent_threshold)

        unexplored = [u for _, u, _ in queue if u not in visited]
        if unexplored:
            verdict = Verdict.OUT_OF_BUDGET
            reason = (f"Budget exhausted after {pages_fetched} pages and {time.time() - t0:.1f}s "
                      f"with {len(unexplored)} promising links unexplored.")
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

