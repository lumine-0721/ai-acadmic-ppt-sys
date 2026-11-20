"""Test doubles. Used ONLY by unit tests; the package itself contains no fake client."""

from __future__ import annotations

import json
from typing import Any


class FakeClient:
    model_id = "fake-model-for-unit-tests"

    def set_stage(self, stage):
        self.stage = stage

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        r = self._responses.pop(0)
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)

    def describe(self):
        return {"backend": "fake", "provider_host": "fake", "model": self.model_id}


class FakeResearch:
    """Scripted research provider for unit tests ONLY. Its record says so in `provider`."""

    def __init__(self, notes=None, status=None):
        self._notes = notes or []
        self._status = status

    def research(self, plan):
        from datetime import datetime, timezone
        from pptv01.contracts import ResearchNote, ResearchRecord, ResearchStatus
        now = datetime.now(timezone.utc)
        notes = [ResearchNote(note_id=f"R{i:03d}", kind="encyclopedia", title=t, text=x,
                              source_url=f"https://example.invalid/{i}", source_name="FAKE (unit test)",
                              backend="fake", query="q", retrieved_at=now)
                 for i, (t, x) in enumerate(self._notes, 1)]
        return ResearchRecord(run_id=plan.run_id, provider="FakeResearch(unit-test only)",
                              status=self._status or (ResearchStatus.ok if notes else ResearchStatus.no_results),
                              notes=notes, summary="fake")
