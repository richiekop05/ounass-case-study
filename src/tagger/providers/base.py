"""The one interface every provider adapter implements.

Given instructions, text, images and a reply schema, return the parsed reply, the token counts
and the duration. Adapters raise RetryableError (rate limit, server error, network),
SchemaError (the reply is not valid JSON for the schema) or FatalError (anything else).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(RuntimeError):
    def __init__(self, message: str, result: "ModelResult | None" = None, retry_after: float | None = None):
        super().__init__(message)
        self.result = result            # tokens are still billed when the reply is unusable
        self.retry_after = retry_after


class RetryableError(ProviderError):
    """Rate limit, overload, server error or network failure."""


class RateLimited(RetryableError):
    """The provider asked us to slow down."""


class SchemaError(ProviderError):
    """The reply did not parse or did not match the schema."""


class FatalError(ProviderError):
    """Bad request, auth failure, refusal: retrying will not help."""


@dataclass
class Image:
    data: bytes
    media_type: str
    path: str               # storage path; the only thing logged


@dataclass
class ModelRequest:
    stage: str
    instructions: str
    text: str
    images: list[Image]
    schema: dict

    def loggable(self) -> dict:
        """The request as sent, minus image bytes."""
        return {"stage": self.stage, "instructions": self.instructions, "text": self.text,
                "images": [i.path for i in self.images], "schema": self.schema}


@dataclass
class ModelResult:
    parsed: dict | None
    raw: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    served_model: str | None = None
    stop_reason: str | None = None
    extra: dict = field(default_factory=dict)


class Provider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResult: ...


# Schema keywords that structured-output grammars commonly reject. They are removed from the copy
# sent to the API; the runner validates the reply against the full schema locally.
UNSUPPORTED = {"maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength", "pattern"}


def api_schema(schema):
    """Copy of `schema` without keywords the API rejects (minItems is kept only as 0 or 1)."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in UNSUPPORTED or (k == "minItems" and v not in (0, 1)):
                continue
            if k == "properties":
                out[k] = {name: api_schema(sub) for name, sub in v.items()}
            else:
                out[k] = api_schema(v)
        return out
    if isinstance(schema, list):
        return [api_schema(x) for x in schema]
    return copy.deepcopy(schema)
