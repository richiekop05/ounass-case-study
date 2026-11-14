"""A provider with no network, for tests and dry runs.

`Adapter.responder` is a function (ModelRequest) -> dict | Exception. Exceptions are raised as they
are, so tests can simulate rate limits and bad replies. Token counts are fixed so cost is predictable.
"""
from __future__ import annotations

from .base import ModelRequest, ModelResult


class Adapter:
    responder = None            # set by tests: Adapter.responder = lambda req: {...}
    calls: list = []

    def __init__(self, profile):
        self.profile = profile

    def complete(self, request: ModelRequest) -> ModelResult:
        type(self).calls.append(request)
        reply = type(self).responder(request) if type(self).responder else {}
        if isinstance(reply, Exception):
            raise reply
        return ModelResult(parsed=reply, raw=str(reply), input_tokens=1000, output_tokens=100,
                           duration_ms=5, served_model=self.profile.model_id, stop_reason="end_turn")
