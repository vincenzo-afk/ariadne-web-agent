import json
import re

from .models import AnswerType, Evidence
from .parse import first_type, json_ld, title_tokens
from .scent import cosine
from .understand import keywords
from .llm import RateLimited
from .verify import MONTHS_RE, NUM_RE


def _overlap(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if 20 <= len(s) <= 420]


def _visible_quote(page, needle):
    for s in _sentences(page.text):
        if needle in s:
            return s
    return None


def _raw_json_quote(page, needle):
    i = (page.html or "").find(needle)
    if i < 0:
        return None
    return page.html[max(0, i - 60): i + 140].strip()


def _faq_candidates(node, spec, page):
    out = []
    items = node.get("mainEntity") or []
    if isinstance(items, dict):
        items = [items]
    for item in items:
        if not isinstance(item, dict):
            continue
        q = item.get("name") or (item.get("question") or {}).get("name")
        ans = item.get("acceptedAnswer") or {}
        a = ans.get("text") if isinstance(ans, dict) else ans
        if isinstance(a, list):
            a = " ".join(str(x) for x in a)
        if not q or not a:
            continue
        if _overlap(keywords(spec.question), keywords(str(q))) >= 0.5:
            a = str(a).strip()
            out.append(Evidence(answer=a, quote=a, page=page, source="json-ld:FAQ"))
    return out


def _product_candidates(node, spec, page):
    out = []
    name = str(node.get("name") or "")
    ql = spec.question.lower()
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}
    price, currency = offers.get("price"), offers.get("priceCurrency") or ""
    if price and ("price" in ql or "cost" in ql):
        ans = f"{price} {currency}".strip()
        q = _visible_quote(page, str(price)) or _raw_json_quote(page, str(price)) or ans
        out.append(Evidence(answer=ans, quote=q, page=page, source="json-ld:Product"))
    if spec.type is AnswerType.YESNO and ("stock" in ql or "availab" in ql):
        avail = str(offers.get("availability") or "")
        if avail:
            a = "yes" if "instock" in avail.lower() else "no"
            q = _visible_quote(page, "stock") or _visible_quote(page, "availab") or _raw_json_quote(page, "availability") or avail
            out.append(Evidence(answer=a, quote=q, page=page, source="json-ld:Product"))
    if "brand" in ql or "manufactur" in ql or "who makes" in ql:
        b = node.get("brand")
        b = b.get("name") if isinstance(b, dict) else b
        if b:
            q = _visible_quote(page, str(b)) or _raw_json_quote(page, str(b)) or str(b)
            out.append(Evidence(answer=str(b), quote=q, page=page, source="json-ld:Product"))
    return out


def _article_candidates(node, spec, page):
    out = []
    ql = spec.question.lower()
    dp = node.get("datePublished")
    if dp and spec.type is AnswerType.DATE:
        q = _visible_quote(page, str(dp)[:4]) or _raw_json_quote(page, str(dp)) or str(dp)
        out.append(Evidence(answer=str(dp), quote=q, page=page, source="json-ld:Article"))
    if "who" in ql and ("write" in ql or "author" in ql):
        auth = node.get("author")
        names = []
        for a in (auth if isinstance(auth, list) else [auth]):
            if isinstance(a, dict):
                names.append(a.get("name", ""))
            elif a:
                names.append(str(a))
        names = [n for n in names if n]
        if names:
            ans = ", ".join(names)
            q = _visible_quote(page, names[0]) or _raw_json_quote(page, names[0]) or ans
            out.append(Evidence(answer=ans, quote=q, page=page, source="json-ld:Article"))
    return out


def structured_candidates(page, spec):
    """Deterministic extraction from schema.org JSON-LD: FAQ, Product, Article.
    Zero LLM involvement, zero hallucination surface."""
    cands = []
    for node in json_ld(page.html):
        t = first_type(node)
        if "faqpage" in t or "qapage" in t:
            cands.extend(_faq_candidates(node, spec, page))
        elif "product" in t:
            cands.extend(_product_candidates(node, spec, page))
        elif any(k in t for k in ("article", "newsarticle", "blogposting", "report")):
            cands.extend(_article_candidates(node, spec, page))
    return cands


def chunks(text, size=900, overlap=120):
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def top_chunks(page, spec, embedder, qvec, k=3):
    cs = chunks(page.text)
    if not cs:
        return []
    vecs = embedder.embed(cs)
    ranked = sorted(zip(cs, vecs), key=lambda cv: cosine(qvec, cv[1]), reverse=True)
    return [c for c, _ in ranked[:k]]


EXTRACT_PROMPT = (
    "You are extracting an answer from ONE web page. Be exact: never invent.\n"
    "Question: {question}\nExpected answer type: {atype}\nPage URL: {url}\n"
    "PAGE EXCERPT:\n{chunk}\n\n"
    "If the excerpt contains the exact answer, reply with ONLY JSON: "
    '{{"answer": "<short exact answer>", "quote": "<verbatim substring of the excerpt that proves it>"}}. '
    "The quote must be copied character-for-character from the excerpt. "
    'If the excerpt does not contain the answer, reply {{"answer": null}}.'
)


def llm_candidates(page, spec, llm, embedder, qvec):
    out = []
    for ch in top_chunks(page, spec, embedder, qvec):
        try:
            resp = llm.complete(EXTRACT_PROMPT.format(
                question=spec.question, atype=spec.type.value,
                url=page.final_url, chunk=ch[:4000]),
                response_format={"type": "json_object"})
        except RateLimited:
            raise
        except Exception:
            continue
        m = re.search(r"\{.*\}", resp, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("answer"):
            continue
        out.append(Evidence(answer=str(data["answer"]).strip(),
                            quote=str(data.get("quote") or data["answer"]).strip(),
                            page=page, source="llm-extract"))
    return out


def _content_hints(spec, page):
    qk = set(keywords(spec.question))
    title = set(title_tokens(page.html))
    return {h for h in qk if h not in title and len(h) >= 3}


def rule_candidates(page, spec):
    """Offline fallback extractor. Deliberately conservative: a NUMBER/DATE is
    only taken from a sentence that mentions the asked-about attribute, so a
    product's price can never be mistaken for its battery capacity."""
    sens = _sentences(page.text)
    if not sens:
        return []
    qwords = set(keywords(spec.question))
    content = _content_hints(spec, page)

    def score(s):
        return len(qwords & set(keywords(s)))

    ranked = sorted(sens, key=score, reverse=True)
    best = ranked[0]
    if score(best) == 0:
        return []
    btoks = set(keywords(best))
    if spec.type in (AnswerType.NUMBER, AnswerType.DATE) and content and not (content & btoks):
        return []
    ql = spec.question.lower()
    if spec.type is AnswerType.YESNO:
        low = best.lower()
        neg = ("no,", "not ", "does not", "isn't", "won't", "never", "unsupported", "unavailable")
        pos = ("yes", "true", "available", "supported", "included")
        if any(n in low for n in neg):
            ans = "no"
        elif any(p in low for p in pos):
            ans = "yes"
        else:
            ans = best[:200]
        return [Evidence(answer=ans, quote=best, page=page, source="rule-extract")]
    if spec.type is AnswerType.NUMBER:
        m = NUM_RE.search(best)
        if m:
            return [Evidence(answer=m.group(0).strip(), quote=best, page=page, source="rule-extract")]
        return []
    if spec.type is AnswerType.DATE:
        m = re.search(r"\b(19|20)\d{2}\b", best) or re.search(MONTHS_RE, best, re.I)
        if m:
            return [Evidence(answer=m.group(0), quote=best, page=page, source="rule-extract")]
        return []
    return [Evidence(answer=best[:240], quote=best, page=page, source="rule-extract")]

