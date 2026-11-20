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
