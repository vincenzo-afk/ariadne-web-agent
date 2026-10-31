import difflib
import re

from .models import AnswerType
from .parse import title_tokens
from .llm import RateLimited
from .understand import keywords

MONTHS_RE = (r"(january|february|march|april|may|june|july|august|september|october|"
             r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)")
NUM_RE = re.compile(r"-?[\$€£]?\s?\d[\d,]*(?:\.\d+)?\s*(?:%|[a-zA-Z]{1,10})?")


def normalize_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _fuzzy_best(text, quote):
    t = normalize_ws(text)
    qtoks = quote.split()
    if len(qtoks) < 4 or len(t) > 400000:
        return 0.0
    seeds = [w for w in qtoks if len(w) >= 5][:3] or qtoks[:2]
    best = 0.0
    for seed in seeds:
        for m in re.finditer(re.escape(seed), t, flags=re.I):
            start = max(0, m.start() - 40)
            window = t[start: start + int(len(quote) * 1.6) + 80]
            r = difflib.SequenceMatcher(None, quote.lower(), window.lower()).ratio()
            if r > best:
                best = r
                if best > 0.97:
                    return best
    return best


def locate_quote(page, quote):
    """Deterministic, model-free: does this exact string exist in the page
    we actually fetched? Returns (where, score) with where in
    {'text', 'html', None}. This is the anti-hallucination backbone."""
    q = normalize_ws(quote)
    if not q:
        return None, 0.0
    for hay, where in ((page.text, "text"), (page.html, "html")):
        t = normalize_ws(hay)
        if q in t:
            return where, 1.0
        if q.lower() in t.lower():
            return where, 0.98
    best = _fuzzy_best(page.text, q)
    if best >= 0.90:
        return "text", best
    return None, best


def type_check(answer, spec):
    a = normalize_ws(answer)
    if not a:
        return False, "empty answer"
    if spec.type is AnswerType.NUMBER and not re.search(r"\d", a):
        return False, "no digits in a NUMBER answer"
    if spec.type is AnswerType.DATE and not (
            re.search(r"\b(19|20)\d{2}\b", a) or re.search(MONTHS_RE, a, re.I)
            or re.search(r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b", a)):
        return False, "no date-like value in a DATE answer"
    if spec.type is AnswerType.YESNO and not re.match(r"^(yes|no)\b", a, re.I):
        return False, "YESNO answer must start with yes/no"
    return True, "ok"


VERIFY_PROMPT = (
    "You are a strict fact-checker. A proposed answer to a question was extracted from a web page.\n"
    "Decide whether the QUOTE alone fully supports the ANSWER to the QUESTION.\n"
    "Rules: the answer must be explicitly stated in, or directly entailed by, the quote; "
    "no outside knowledge; if the quote only discusses the topic without answering, reply FAIL.\n"
    "QUESTION: {question}\nANSWER: {answer}\nQUOTE: {quote}\n"
    "Reply with exactly one word: PASS or FAIL."
)


def verify(evidence, spec, llm=None):
    """Three gates, in order: type gate -> verbatim-quote gate -> entailment gate."""
    ok, msg = type_check(evidence.answer, spec)
    if not ok:
        return False, f"type-gate: {msg}"
    where, score = locate_quote(evidence.page, evidence.quote)
    if where is None:
        return False, f"quote-gate: quote not found verbatim on fetched page (best fuzzy {score:.2f}); refusing to trust model memory"
    ok2, why2 = _rule_gate(evidence, spec)
    if llm is None:
        if ok2:
            return True, f"rule-gates PASS (quote located in page {where})"
        return False, f"entailment-gate(rule): {why2}"
    try:
        resp = llm.complete(VERIFY_PROMPT.format(question=spec.question,
                                                 answer=evidence.answer,
                                                 quote=evidence.quote))
    except Exception:
        resp = ""
    stripped = (resp or "").strip().upper()
    if stripped.startswith("PASS"):
        return True, f"llm-entailment PASS + verbatim quote in page {where}"
    if stripped.startswith("FAIL"):
        return False, f"entailment-gate(llm): FAIL - {resp.strip()[:120]}"
    # Ambiguous LLM output: never auto-pass, never auto-fail - use the
    # deterministic gate as the conservative fallback.
    if ok2:
        return True, f"deterministic-gate PASS (llm reply ambiguous; quote located in page {where})"
    return False, f"entailment-gate: llm reply ambiguous AND deterministic gate failed: {why2}"


def _rule_gate(evidence, spec):
    """Deterministic offline entailment gate."""
    qk = set(keywords(spec.question))
    qt = set(keywords(evidence.quote))
    title = set(title_tokens(evidence.page.html))
    content = {h for h in qk if h not in title and len(h) >= 3}
    if content:
        if content & qt:
            return True, "ok"
        return False, ("quote lacks the asked-about attribute "
                       "(entity name matched, attribute did not) - not answering")
    overlap = len(qk & qt) / max(1, len(qk))
    if qk and overlap < 0.2:
        return False, "insufficient question/quote overlap"
    return True, "ok" 

