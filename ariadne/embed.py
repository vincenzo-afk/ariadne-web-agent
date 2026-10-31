import hashlib
import math
import re


class HashingEmbedder:
    """Zero-dependency bag-of-tokens embedding via deterministic hashing.
    Surprisingly decent for link 'scent' scoring; swap for
    sentence-transformers in production (see STEmbedder)."""

    DIMS = 256

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.DIMS
            for tok in re.findall(r"[a-z0-9]{2,}", (t or "").lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.DIMS] += 1.0 if h % 2 == 0 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class STEmbedder:
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer(model_name)

    def embed(self, texts):
        return [list(map(float, row)) for row in self.m.encode(list(texts))]


def default_embedder():
    try:
        return STEmbedder()
    except Exception:
        return HashingEmbedder()
