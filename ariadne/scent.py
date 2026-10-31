import math
import re
from urllib.parse import urlparse


def path_tokens(url):
    return " ".join(t for t in re.split(r"[^a-z0-9]+", urlparse(url).path.lower()) if len(t) > 1)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _keyword_bonus(hints, *blobs):
    blob = " ".join(blobs).lower()
    if not hints:
        return 0.0
    hits = sum(1 for h in hints if h in blob)
    return hits / len(hints)


def scent_score(link, qvec, embedder, spec):
    """Information scent: how strongly a link smells like the answer.
    Semantic cosine + keyword hit bonus on anchor, context and URL path."""
    text = f"{link.anchor} {link.context} {path_tokens(link.url)}"
    sim = cosine(qvec, embedder.embed([text])[0])
    bonus = 0.25 * _keyword_bonus(spec.hints, link.anchor, link.context, path_tokens(link.url))
    return sim + bonus


def score_links(links, qvec, embedder, spec):
    """Batch form of scent_score: one embedding call for the whole page of links."""
    if not links:
        return []
    vecs = embedder.embed([f"{lk.anchor} {lk.context} {path_tokens(lk.url)}" for lk in links])
    return [cosine(qvec, v) + 0.25 * _keyword_bonus(spec.hints, lk.anchor, lk.context, path_tokens(lk.url))
            + (0.3 if lk.context == "next-page" else 0.0)
            for lk, v in zip(links, vecs)]
