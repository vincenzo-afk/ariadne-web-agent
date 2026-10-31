import re
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx

from .models import Page
from .parse import visible_text

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 AriadneBot/1.0 (+proof-grounded QA agent)")


def detect_block(status, url, html):
    low = (html or "").lower()
    if status == 401:
        return True, "HTTP 401 login required"
    if status == 403:
        return True, "HTTP 403 forbidden"
    tiny = len(low) < 3000
    if tiny and any(k in low for k in ("captcha", "are you a robot", "verify you are human", "unusual traffic")):
        return True, "anti-bot challenge detected"
    return False, ""


def needs_render(html, status):
    if status >= 400 or not html:
        return False
    low = html.lower()
    text = visible_text(html)
    if any(k in low for k in ("__next_data__", "data-reactroot", "ng-version",
                              'id="root"', 'id="app"', 'id="__next"')):
        return len(text) < 400
    if low.count("<script") >= 8 and len(text) < 200:
        return True
    return False


class Fetcher:
    """Polite dual-mode fetcher: fast HTTP first, headless Chromium only when
    the page looks JS-rendered. Respects robots.txt and per-host rate limits."""

    def __init__(self, respect_robots=True, delay=1.0, timeout=20, render=True, user_agent=BROWSER_UA):
        self.respect_robots = respect_robots
        self.delay = delay
        self.render = render
        self.client = httpx.Client(follow_redirects=True, timeout=timeout,
                                   headers={"User-Agent": user_agent,
                                            "Accept-Language": "en-US,en;q=0.9"})
        self._robots = {}
        self._last = {}

    def _robot_parser(self, url):
        host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if host in self._robots:
            return self._robots[host]
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(host, "/robots.txt"))
        try:
            rp.read()
        except Exception:
            rp = None
        self._robots[host] = rp
        return rp

    def allowed(self, url):
        if not self.respect_robots:
            return True
        rp = self._robot_parser(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.client.headers["user-agent"], url)
        except Exception:
            return True

    def _throttle(self, url):
        host = urlparse(url).netloc
        wait = self.delay - (time.time() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def fetch(self, url):
        if not self.allowed(url):
            return Page(url=url, final_url=url, status=0, text="", html="",
                        blocked=True, block_reason="disallowed by robots.txt")
        self._throttle(url)
        try:
            r = self.client.get(url)
        except Exception as e:
            return Page(url=url, final_url=url, status=0, text="", html="",
                        blocked=True, block_reason=f"fetch error: {e.__class__.__name__}")
        html = r.text or ""
        blocked, reason = detect_block(r.status_code, str(r.url), html)
        if blocked:
            return Page(url=url, final_url=str(r.url), status=r.status_code, text="",
                        html=html, blocked=True, block_reason=reason)
        if self.render and needs_render(html, r.status_code):
            rendered = self._render(str(r.url))
            if rendered is not None:
                return rendered
        return Page(url=url, final_url=str(r.url), status=r.status_code,
                    text=visible_text(html), html=html)

    def _render(self, url):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page()
                resp = pg.goto(url, wait_until="networkidle", timeout=30000)
                text = pg.evaluate("() => document.body ? document.body.innerText : ''")
                html = pg.content()
                status = resp.status if resp else 200
                b.close()
            return Page(url=url, final_url=url, status=status,
                        text=re.sub(r"\s+", " ", text or ""), html=html, rendered=True)
        except Exception:
            return None
