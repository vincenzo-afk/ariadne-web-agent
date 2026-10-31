import json
import re
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .models import Link, Unit

SKIP_EXT = (".pdf", ".zip", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
            ".mp4", ".webm", ".mp3", ".wav", ".css", ".ico", ".json")
# Following a link is a GET, but on badly built sites a GET can still change state.
UNSAFE_LINK = re.compile(r"(^|[/_?&=-])(log-?out|sign-?out|log-?in|sign-?in|sign-?up|register|delete|remove|unsubscribe"
                         r"|add-to-cart|checkout|cart)($|[/_?&=.-])", re.I)
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "mc_cid", "mc_eid", "igshid"}


def same_site(url, base):
    a = urlparse(url).netloc.lower()
    b = urlparse(base).netloc.lower()
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b)


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
        if UNSAFE_LINK.search(urlparse(u).path + "?" + urlparse(u).query):
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
        if "next" in (a.get("rel") or []) or re.fullmatch(r"(next|older|more)\W*( page)?\W*", anchor, re.I):
            ctx = "next-page"
        out.append(Link(url=u, anchor=anchor, context=ctx))
    return out


HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
HIDDEN_CLASS = {"hidden", "sr-only", "visually-hidden", "d-none"}
DROP_TAGS = ["script", "style", "noscript", "template", "svg", "form", "nav", "aside", "s", "del", "strike", "head"]
BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "tr", "dt", "dd", "div",
              "section", "article", "main", "header", "footer", "td", "th", "figcaption", "caption", "ul", "ol",
              "table", "dl", "body"]


def _is_hidden(tag):
    attrs = getattr(tag, "attrs", None) or {}
    if "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    if HIDDEN_STYLE.search(str(attrs.get("style", ""))):
        return True
    classes = attrs.get("class") or []
    return bool(HIDDEN_CLASS & {str(c).lower() for c in classes})


def _clean_soup(html):
    """What a human reader sees. Hidden text is the classic price-spoofing and
    prompt-injection channel, so it never reaches extraction or verification."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(DROP_TAGS):
        tag.decompose()
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        if not tag.decomposed:
            tag.decompose()
    return soup


def _txt(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def visible_text(html):
    try:
        return re.sub(r"\s+", " ", _clean_soup(html).get_text(" ", strip=True))
    except RecursionError:
        return re.sub(r"\s+", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>|<[^>]+>", " ", html or "")).strip()


def _table_units(table, heading):
    rows = table.find_all("tr")
    out = []
    header = []
    if rows and len(rows[0].find_all("th")) > 1 and not rows[0].find("td"):
        header = [_txt(c) for c in rows[0].find_all("th")]
        rows = rows[1:]
    for tr in rows:
        cells = [_txt(c) for c in tr.find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        row = " ".join(cells)
        if header:
            for h, c in list(zip(header, cells))[1:]:
                out.append(Unit(text=row, kind="kv", label=h, value=c, heading=heading, row=True))
        else:
            out.append(Unit(text=row, kind="kv", label=cells[0], value=" ".join(cells[1:]), heading=heading))
    return out


def page_units(html):
    """Split a page into atomic units so a table cell or spec row can be read on
    its own instead of being lost in one flattened run of text."""
    try:
        soup = _clean_soup(html)
        out, heading, fresh = [], "", False
        for el in soup.find_all(BLOCK_TAGS):
            name = el.name
            if name == "table":
                out.extend(_table_units(el, heading))
            elif name == "dl":
                for dt in el.find_all("dt"):
                    dd = dt.find_next_sibling("dd")
                    if dd is not None:
                        out.append(Unit(text=f"{_txt(dt)} {_txt(dd)}", kind="kv", label=_txt(dt).rstrip(": "),
                                        value=_txt(dd), heading=heading))
            elif name in ("ul", "ol"):
                items = [_txt(li) for li in el.find_all("li", recursive=False)]
                items = [i for i in items if i]
                if len(items) >= 2:
                    out.append(Unit(text=" ".join(items), kind="list", value="\n".join(items), heading=heading))
            elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                heading = _txt(el)
                fresh = True
                if heading:
                    out.append(Unit(text=heading, kind="heading", heading=heading))
            elif not el.find_parent(["table", "dl"]) and not el.find(BLOCK_TAGS):
                t = _txt(el)
                if t:
                    m = re.match(r"^([^:]{2,40}):\s+(.{1,300})$", t)
                    if m:
                        out.append(Unit(text=t, kind="kv", label=m.group(1), value=m.group(2), heading=heading))
                    else:
                        out.append(Unit(text=t, kind="block", heading=heading, lead=fresh and len(t) <= 120))
                    fresh = False
        if not out:
            t = _txt(soup)
            if t:
                out.append(Unit(text=t))
        return out
    except RecursionError:
        return [Unit(text=visible_text(html))]


def headline(html):
    """What the page says it is about: its title and first headings."""
    soup = BeautifulSoup(html or "", "html.parser")
    parts = [soup.find("title"), soup.find("h1"), soup.find("h2"), soup.find("h3")]
    return " ".join(_txt(x) for x in parts if x is not None)


def metadata(html):
    """Page-level declarations that are not body text: (kind, value)."""
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    t = soup.find("title")
    if t is not None and _txt(t):
        out.append(("title", _txt(t)))
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or "").lower()
        if key in ("description", "og:description", "og:title") and m.get("content"):
            out.append((key, re.sub(r"\s+", " ", m["content"]).strip()))
    return out


def _strings(node):
    if isinstance(node, (str, int, float)) and not isinstance(node, bool):
        yield str(node)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def declared_text(html):
    """The only non-body text a quote may cite: title, description metas and
    JSON-LD values. Never raw HTML, so comments and scripts cannot be evidence."""
    parts = [v for _, v in metadata(html)]
    for node in json_ld(html):
        parts.extend(_strings(node))
    return " \n ".join(parts)


def json_ld(html):
    soup = BeautifulSoup(html or "", "html.parser")
    nodes = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                nodes.extend(x for x in graph if isinstance(x, dict))
            else:
                nodes.append(item)
    return nodes


def first_type(node):
    t = node.get("@type", "")
    if isinstance(t, list):
        t = " ".join(str(x) for x in t)
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
