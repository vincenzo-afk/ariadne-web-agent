import json
import re
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .models import Link

SKIP_EXT = (".pdf", ".zip", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
            ".mp4", ".webm", ".mp3", ".wav", ".css", ".ico", ".json")
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "mc_cid", "mc_eid", "igshid"}


def normalize_url(href, base):
    if not href:
        return ""
    u = urljoin(base, href.strip())
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return ""
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING]
    p = p._replace(fragment="", query=urlencode(query))
    return urlunparse(p)


def extract_links(html, base):
    soup = BeautifulSoup(html or "", "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        u = normalize_url(a.get("href"), base)
        if not u or u in seen:
            continue
        path = urlparse(u).path.lower()
        if any(path.endswith(e) for e in SKIP_EXT):
            continue
        seen.add(u)
        anchor = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:300]
        ctx = ""
        h = a.find_parent(["h1", "h2", "h3", "h4", "h5"])
        if h is not None:
            ctx = re.sub(r"\s+", " ", h.get_text(" ", strip=True))[:300]
        else:
            par = a.find_parent(["section", "article", "li", "td", "div"])
            if par is not None:
                hh = par.find(["h1", "h2", "h3", "h4", "strong", "b", "label"])
                if hh is not None:
                    ctx = re.sub(r"\s+", " ", hh.get_text(" ", strip=True))[:300]
        out.append(Link(url=u, anchor=anchor, context=ctx))
    return out


def visible_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg", "form", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def json_ld(html):
    soup = BeautifulSoup(html or "", "html.parser")
    nodes = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and "@graph" in data:
            nodes.extend(x for x in data["@graph"] if isinstance(x, dict))
        elif isinstance(data, list):
            nodes.extend(x for x in data if isinstance(x, dict))
        elif isinstance(data, dict):
            nodes.append(data)
    return nodes


def first_type(node):
    t = node.get("@type", "")
    if isinstance(t, list):
        t = t[0] if t else ""
    return str(t).lower()


def parse_sitemap_urls(xml):
    return [u.strip() for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml or "", flags=re.I)]


def find_site_search(html, base):
    soup = BeautifulSoup(html or "", "html.parser")
    names = ("q", "s", "search", "query", "keyword", "keywords", "term")
    for form in soup.find_all("form"):
        action = urljoin(base, form.get("action") or base)
        if urlparse(action).scheme not in ("http", "https"):
            continue
        if (form.get("method") or "get").lower() != "get":
            continue
        inp = None
        for nm in names:
            inp = form.find("input", attrs={"name": nm})
            if inp is not None:
                break
        if inp is None:
            cand = [i for i in form.find_all("input")
                    if (i.get("type") or "text").lower() in ("text", "search") and i.get("name")]
            inp = cand[0] if cand else None
        if inp is not None and inp.get("name"):
            sep = "&" if urlparse(action).query else "?"
            return action + sep + inp["name"] + "={q}"
    return None


def title_tokens(html):
    soup = BeautifulSoup(html or "", "html.parser")
    parts = []
    t = soup.find("title")
    if t is not None:
        parts.append(t.get_text(" ", strip=True))
    h1 = soup.find("h1")
    if h1 is not None:
        parts.append(h1.get_text(" ", strip=True))
    return [w for w in re.findall(r"[a-z0-9]+", " ".join(parts).lower()) if len(w) >= 3]
