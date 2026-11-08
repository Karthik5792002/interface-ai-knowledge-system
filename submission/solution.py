"""A knowledge system that knows what's true now. See NOTES.md for how and why."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_harness = next((d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists()), None)
if _harness:
    sys.path.insert(0, str(_harness))
from contract import Answer, Conflict, Snapshot, Source  # noqa: E402

from dupaco_ks import answers  # noqa: E402
from dupaco_ks.conflicts import _overlap, find_conflicts  # noqa: E402
from dupaco_ks.question import parse  # noqa: E402
from dupaco_ks.store import View  # noqa: E402


class KnowledgeBase:
    def __init__(self):
        self.views: list[View] = []
        self._findings = None

    def ingest(self, snapshot: Snapshot) -> None:
        self.views.append(View(snapshot))
        self.views.sort(key=lambda v: v.taken_on)
        self._findings = None

    def view_for(self, as_of: Optional[str]) -> Optional[View]:
        """The newest crawl taken on or before the date. No date means the newest crawl of all."""
        if not as_of:
            return self.views[-1] if self.views else None
        eligible = [v for v in self.views if v.taken_on <= as_of[:10]]
        return eligible[-1] if eligible else None

    def _result(self, question: str, as_of: Optional[str]):
        parsed = parse(question, as_of)
        view = self.view_for(parsed.as_of)
        if view is None:
            return parsed, None, answers.refuse()
        return parsed, view, answers.answer(parsed, view)

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        _, view, result = self._result(question, as_of)
        sid = view.id if view else None
        return Answer(text=result.text, sources=[Source(url=u, snapshot_id=sid) for u in result.urls],
                      confident=result.confident, conflict_note=result.note)

    def findings(self):
        if self._findings is None:
            self._findings = find_conflicts(self.views) if self.views else []
        return self._findings

    def conflicts(self) -> list[Conflict]:
        return [Conflict(topic=f.topic, values=list(f.values),
                         sources=[Source(url=u, snapshot_id=s, quote=v) for u, s, v in f.evidence[:10]],
                         resolution=f.winner, reason=f.reason)
                for f in self.findings()]

    def timeline(self, question: str, as_of: Optional[str] = None):
        """The time machine: how the facts behind an answer moved across every crawl."""
        _, _, result = self._result(question, as_of)
        out = []
        for row in result.rows[:6]:
            line = []
            for view in self.views:
                matches = [o for o in view.current if o.same_fact(row)]
                old = max(matches, key=lambda o: _overlap(o, row), default=None)
                line.append((view.taken_on, old.headline if old else None))
            out.append((row, line))
        return out


def build() -> KnowledgeBase:
    return KnowledgeBase()
