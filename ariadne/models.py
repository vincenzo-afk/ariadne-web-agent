from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, asdict
from enum import Enum


class Verdict(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    OUT_OF_BUDGET = "out_of_budget"


class AnswerType(str, Enum):
    ENTITY = "entity"
    NUMBER = "number"
    DATE = "date"
    YESNO = "yesno"
    TEXT = "text"
    LIST = "list"


@dataclass
class AnswerSpec:
    question: str
    type: AnswerType = AnswerType.TEXT
    hints: list = field(default_factory=list)


@dataclass
class Link:
    url: str
    anchor: str = ""
    context: str = ""


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    text: str
    html: str
    rendered: bool = False
    blocked: bool = False
    block_reason: str = ""
    fetched_at: float = field(default_factory=time.time)

    @property
    def content_sha256(self):
        return hashlib.sha256(self.text.encode("utf-8", "ignore")).hexdigest()


@dataclass
class Evidence:
    answer: str
    quote: str
    page: Page = None
    source: str = ""
    verified_by: str = ""


@dataclass
class Step:
    n: int
    action: str
    url: str
    detail: str
    ts: float = field(default_factory=time.time)
    prev_hash: str = ""
    hash: str = ""


@dataclass
class AgentReport:
    verdict: Verdict
    answer: object = None
    evidence: object = None
    steps: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self):
        d = {"verdict": self.verdict.value, "answer": self.answer,
             "reason": self.reason, "steps": [asdict(s) for s in self.steps]}
        if self.evidence:
            ev = self.evidence
            d["evidence"] = {
                "answer": ev.answer, "quote": ev.quote,
                "url": ev.page.final_url, "fetched_at": ev.page.fetched_at,
                "content_sha256": ev.page.content_sha256,
                "verified_by": ev.verified_by, "source": ev.source,
            }
        return d

    def pretty(self):
        lines = ["", "=== ARIADNE REPORT ===", f"verdict : {self.verdict.value.upper()}"]
        if self.answer is not None:
            lines.append(f"answer  : {self.answer}")
        if self.evidence:
            ev = self.evidence
            import datetime
            lines.append(f"source  : {ev.page.final_url}")
            lines.append(f"fetched : {datetime.datetime.fromtimestamp(ev.page.fetched_at).isoformat(timespec='seconds')}")
            lines.append(f"sha256  : {ev.page.content_sha256[:32]}...")
            lines.append(f"quote   : \"{ev.quote}\"")
            lines.append(f"verified: {ev.verified_by}")
        lines.append(f"reason  : {self.reason}")
        lines.append("")
        lines.append("--- evidence trail (hash-chained, tamper-evident) ---")
        for s in self.steps:
            lines.append(f"[{s.n:02d}] {s.action:<13} {s.url:<60.60} {s.detail[:70]}  #{s.hash[:10]}")
        return "\n".join(lines)
