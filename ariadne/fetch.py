import ipaddress
import re
import socket
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx

from .models import Page
from .parse import same_site, visible_text

ROBOT_NAME = "AriadneBot"
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 10
READABLE = ("text/", "html", "xml", "json")
# Identifies itself honestly; sites such as Wikipedia refuse browser look-alikes from scripts.
USER_AGENT = "AriadneBot/1.1 (+https://github.com/vincenzo-afk/ariadne-web-agent)"


def detect_block(status, url, html):
    low = (html or "").lower()
    if status == 401:
        return True, "HTTP 401 login required"
    if status == 403:
        return True, "HTTP 403 forbidden"
    if status == 429:
        return True, "HTTP 429 rate limited"
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


def is_private_host(url):
    """True when the host resolves to loopback, link-local or private space.
    Keeps the agent from being walked into internal services or cloud metadata."""
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def decode_body(raw, header_encoding):
    head = raw[:2048].decode("ascii", "ignore")
    m = re.search(r"""<meta[^>]+charset=["']?([\w-]+)""", head, re.I)
    for enc in (header_encoding, m.group(1) if m else None, "utf-8"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("cp1252", "replace")


class Fetcher:
    """Polite dual-mode fetcher: fast HTTP first, headless Chromium only when
    the page looks JS-rendered. Respects robots.txt and per-host rate limits,
    refuses private addresses, off-site redirects and oversized bodies."""

    def __init__(self, respect_robots=True, delay=1.0, timeout=20, render=True,
                 user_agent=USER_AGENT, allow_private=False, max_bytes=MAX_BYTES):
        self.respect_robots = respect_robots
        self.delay = delay
        self.render = render
        self.allow_private = allow_private
        self.max_bytes = max_bytes
        self.client = httpx.Client(follow_redirects=False, timeout=timeout,
                                   headers={"User-Agent": user_agent,
                                            "Accept-Language": "en-US,en;q=0.9"})
        self._robots = {}
        self._last = {}

    def _robot_parser(self, url):
        host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if host in self._robots:
            return self._robots[host]
        rp = robotparser.RobotFileParser()
        try:
            r = self.client.get(urljoin(host, "/robots.txt"), follow_redirects=True)
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code < 400:
                rp.parse(r.text.splitlines())
            else:
                rp.allow_all = True
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
            return rp.can_fetch(ROBOT_NAME, url)
        except Exception:
            return True

    def _throttle(self, url):
        host = urlparse(url).netloc
        wait = self.delay - (time.time() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def _refused(self, url, final, reason, status=0):
        return Page(url=url, final_url=final, status=status, text="", html="", blocked=True, block_reason=reason)

    def _read(self, url):
        """GET one hop, never buffering more than max_bytes. Returns (response, body, readable)."""
        with self.client.stream("GET", url) as r:
            ctype = r.headers.get("content-type", "").lower()
            if ctype and not any(k in ctype for k in READABLE):
                return r, b"", False
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) >= self.max_bytes:
                    del buf[self.max_bytes:]
                    break
            return r, bytes(buf), True

    def fetch(self, url):
        if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
            return self._refused(url, url, "unsupported or invalid URL")
        current = url
        try:
            for _ in range(MAX_REDIRECTS + 1):
                if not self.allow_private and is_private_host(current):
                    return self._refused(url, current, "private or local address refused")
                if not same_site(current, url) and not same_site(url, current):
                    return self._refused(url, current, "off-site redirect")
                if not self.allowed(current):
                    return self._refused(url, current, "disallowed by robots.txt")
                self._throttle(current)
                r, raw, readable = self._read(current)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                break
            else:
                return self._refused(url, current, "fetch error: TooManyRedirects")
        except Exception as e:
            return self._refused(url, current, f"fetch error: {e.__class__.__name__}")
        if not readable:
            return Page(url=url, final_url=current, status=r.status_code, text="", html="")
        html = decode_body(raw, r.charset_encoding)
        blocked, reason = detect_block(r.status_code, current, html)
        if blocked:
            return Page(url=url, final_url=current, status=r.status_code, text="",
                        html=html, blocked=True, block_reason=reason)
        if self.render and needs_render(html, r.status_code):
            rendered = self._render(current)
            if rendered is not None:
                return rendered
        return Page(url=url, final_url=current, status=r.status_code,
                    text=visible_text(html), html=html)

    def _render(self, url):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                try:
                    pg = b.new_page()
                    resp = pg.goto(url, wait_until="networkidle", timeout=30000)
                    final = pg.url
                    html = pg.content()
                    status = resp.status if resp else 200
                finally:
                    b.close()
            if not same_site(final, url) and not same_site(url, final):
                return None
            if not self.allow_private and is_private_host(final):
                return None
            return Page(url=url, final_url=final, status=status,
                        text=visible_text(html), html=html, rendered=True)
        except Exception:
            return None
