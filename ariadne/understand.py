import re

from .models import AnswerSpec, AnswerType

STOP = set((
    "a an the is are was were of on in at to for with and or as by it its this that these those "
    "what which who whom whose when where why how do does did can could will would should shall "
    "than then so such into over after before between per about from"
).split())


def keywords(question):
    toks = re.findall(r"[a-z0-9]+", (question or "").lower())
    return [t for t in toks if t not in STOP and len(t) > 1]


def classify(question):
    q = (question or "").lower().strip()
    first = q.split()[0] if q.split() else ""
    if first in ("is", "are", "does", "do", "did", "can", "could", "has", "have", "had", "was", "were", "will"):
        t = AnswerType.YESNO
    elif first == "when" or q.startswith(("what date", "what year", "what time", "on what day")):
        t = AnswerType.DATE
    elif re.search(r"how (many|much)|price|cost|weight|capacity|size|height|width|temperature|salary|rating", q):
        t = AnswerType.NUMBER
    elif first in ("list", "name") or (first == "what" and (", " in q or " all " in f" {q} ")):
        t = AnswerType.LIST
    elif first in ("who", "where", "which", "what"):
        t = AnswerType.ENTITY
    else:
        t = AnswerType.TEXT
    return AnswerSpec(question=question, type=t, hints=keywords(question))
