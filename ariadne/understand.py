import re
import unicodedata

from .models import AnswerSpec, AnswerType

STOP = set((
    "a an the is are was were be been of on in at to for with and or as by it its this that these those "
    "what which who whom whose when where why how do does did can could will would should shall has have had "
    "than then so such into over after before between per about from there me my i we our us you your please "
    "tell show give find get need want know much many s"
).split())

# Words that describe the asking rather than the thing asked about.
GENERIC = set("current latest exact official main recommend recommended actual value number "
              "function method class command page website site".split())

POLITE = re.compile(
    r"^(?:(?:hi|hello|hey)\b[,!.]?\s+)?(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?(?=tell|show|give|find|look|get|check))?"
    r"(?:(?:tell|show|give)\s+me\s+|find(?:\s+out)?\s+|look\s+up\s+|get\s+me\s+|check\s+|"
    r"i\s+(?:need|want)(?:\s+to\s+know)?\s+|i'?d\s+like\s+to\s+know\s+)?", re.I)

CANON = {
    "cost": "price", "fee": "price", "pricing": "price", "charge": "price",
    "establish": "found", "founder": "found", "start": "found",
    "wrote": "author", "written": "author", "writer": "author", "write": "author",
    "licence": "license", "telephone": "phone", "mail": "email",
    "refund": "return",
}

NUMBER_RE = re.compile(r"\b(how (many|much|long|old|tall|far|big)|prices?|costs?|fees?|weight|capacity|size|height"
                       r"|width|length|temperature|salary|rating|version)\b")


def clean_question(question):
    q = unicodedata.normalize("NFKC", question or "").replace("’", "'").strip()
    if q.isupper():
        q = q.lower()
    q = re.sub(r"\bwhat'?s\b", "what is", q, flags=re.I)
    q = re.sub(r"\bwho'?s\b", "who is", q, flags=re.I)
    return POLITE.sub("", q).strip()


def keywords(question):
    toks = re.findall(r"[^\W_]+", (question or "").lower())
    return [t for t in toks if t not in STOP and (len(t) > 1 or t.isdigit() or not t.isascii())]


def stem(word):
    w = word.lower()
    if w in CANON:
        return CANON[w]
    if not w.isascii() or w.isdigit():
        return w
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith("ss"):
            w = w[: len(w) - len(suf)] + rep
            break
    return CANON.get(w, w)


def tokens(text):
    """Comparable word set: lowercased, stemmed, synonyms folded."""
    return {stem(t) for t in re.findall(r"[^\W_]+", (text or "").lower())}


def subject_terms(core):
    """Named things in the question (capitalised after the first word, 'PEP 8').
    An answer about a different named thing is not an answer."""
    words = re.findall(r"[^\W_]+", core)
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if i and low not in STOP and w[0].isupper():
            out.append(low)
        elif i and w.isdigit() and words[i - 1][0].isupper():
            out.append(low)
    return out


def classify(question):
    core = clean_question(question)
    q = core.lower().rstrip("?!. ")
    first = q.split()[0] if q.split() else ""
    if first in ("is", "are", "does", "do", "did", "can", "could", "has", "have", "had", "was", "were", "will"):
        t = AnswerType.YESNO
    elif first == "when" or q.startswith(("what date", "what year", "what time", "on what day", "the date")):
        t = AnswerType.DATE
    elif NUMBER_RE.search(q):
        t = AnswerType.NUMBER
    elif first == "list" or (first == "name" and q.split()[-1].endswith("s")) or q.startswith("what are"):
        t = AnswerType.LIST
    elif first == "what" and (", " in q or " all " in f" {q} "):
        t = AnswerType.LIST
    elif first in ("who", "where", "which", "what", "name"):
        t = AnswerType.ENTITY
    else:
        t = AnswerType.TEXT
    hints = keywords(core)
    subject = subject_terms(core)
    # "the UPC of X": what precedes the preposition is the attribute, however it is capitalised.
    m = re.match(r"(?:what|which|who|where|when)\s+(?:is|are|was|were)\s+the\s+(.+?)\s+(?:of|for|on|in|at)\b", core, re.I)
    if m:
        asked = set(keywords(m.group(1)))
        subject = [w for w in subject if w not in asked]
    attrs = [h for h in hints if h not in subject and h not in GENERIC and h not in ("list", "name")] or list(subject)
    return AnswerSpec(question=question, type=t, hints=hints, subject=subject, attributes=attrs)
