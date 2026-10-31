import hashlib
import json

from .models import Step


class Ledger:
    """Append-only, hash-chained evidence trail: Ariadne's thread.

    Every action the agent takes is chained to the previous one, so the
    run log cannot be altered after the fact without breaking the chain.
    """

    def __init__(self):
        self.steps = []
        self._prev = "GENESIS"

    def log(self, action, url="", detail=""):
        s = Step(n=len(self.steps) + 1, action=action, url=url, detail=detail[:400])
        s.prev_hash = self._prev
        payload = json.dumps([s.n, s.action, s.url, s.detail, s.ts, s.prev_hash], sort_keys=True)
        s.hash = hashlib.sha256(payload.encode()).hexdigest()
        self._prev = s.hash
        self.steps.append(s)
        return s

    def verify_chain(self):
        prev = "GENESIS"
        for s in self.steps:
            payload = json.dumps([s.n, s.action, s.url, s.detail, s.ts, s.prev_hash], sort_keys=True)
            if s.prev_hash != prev or s.hash != hashlib.sha256(payload.encode()).hexdigest():
                return False
            prev = s.hash
        return True
