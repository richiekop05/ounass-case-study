"""Anthropic (Claude) adapter: Messages API with structured outputs (`output_config.format`).

The instructions go in the system prompt with a cache breakpoint, so the skill file is cached
across products. Images go first in the user turn, then the text. The SDK's own retries are off:
the runner retries and logs every attempt.
"""
from __future__ import annotations

import base64
import json
import os
import time

import anthropic

from .base import (FatalError, ModelRequest, ModelResult, RateLimited, RetryableError, SchemaError,  # noqa: F401
                   api_schema)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_KEY_ENV = "ANTHROPIC_API_KEY"
INFO = {"description": "Anthropic Claude models (Messages API, structured outputs).",
        "default_key_env": DEFAULT_KEY_ENV, "needs_base_url": False,
        "optional_fields": ["api_key_env", "base_url", "effort", "max_tokens", "fallbacks", "extra",
                            "extra_headers"]}


class Adapter:
    def __init__(self, profile):
        self.profile = profile
        key_env = profile.api_key_env or DEFAULT_KEY_ENV
        key = os.environ.get(key_env, "").strip()
        if not key:
            raise FatalError(f"{key_env} is not set. Add it to .env, or run `tagger models set-key {key_env}`.")
        self.client = anthropic.Anthropic(api_key=key, base_url=profile.base_url or None, max_retries=0,
                                          timeout=600.0, default_headers=profile.extra_headers or None)

    def complete(self, request: ModelRequest) -> ModelResult:
        p = self.profile
        content = []
        if p.accepts_images:
            for img in request.images:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("ascii")}})
        content.append({"type": "text", "text": request.text})
        output_config = {"format": {"type": "json_schema", "schema": api_schema(request.schema)}}
        if p.effort:
            output_config["effort"] = p.effort
        kwargs = dict(model=p.model_id, max_tokens=p.max_tokens,
                      system=[{"type": "text", "text": request.instructions,
                               "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": content}],
                      output_config=output_config)
        if p.extra:
            kwargs["extra_body"] = dict(p.extra)
        started = time.monotonic()
        try:
            if p.fallbacks:
                resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks=p.fallbacks, **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            raise RateLimited(f"rate limited: {e.message}", retry_after=_retry_after(e)) from e
        except (anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            raise RetryableError(f"{type(e).__name__}: {e}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 or e.status_code in (408, 409):
                raise RetryableError(f"HTTP {e.status_code}: {e.message}") from e
            raise FatalError(f"HTTP {e.status_code}: {e.message}") from e
        duration = int((time.monotonic() - started) * 1000)

        u = resp.usage
        text = next((b.text for b in resp.content if b.type == "text"), "")
        result = ModelResult(parsed=None, raw=text, input_tokens=u.input_tokens or 0,
                             cached_input_tokens=u.cache_read_input_tokens or 0,
                             cache_write_tokens=u.cache_creation_input_tokens or 0,
                             output_tokens=u.output_tokens or 0, duration_ms=duration,
                             served_model=resp.model, stop_reason=resp.stop_reason,
                             extra={"request_id": getattr(resp, "_request_id", None)})
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise FatalError(f"refused ({getattr(details, 'category', None)})", result=result)
        if resp.stop_reason == "max_tokens":
            raise SchemaError(f"reply cut off at max_tokens={p.max_tokens}", result=result)
        try:
            result.parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise SchemaError(f"reply is not JSON: {e}", result=result) from e
        return result


def _retry_after(e) -> float | None:
    try:
        return float(e.response.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None
