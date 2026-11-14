"""Any provider that speaks the OpenAI chat-completions format, configured entirely by the profile.

Covers OpenAI, Google Gemini (OpenAI endpoint), Mistral, Groq, DeepSeek, xAI, Together, OpenRouter,
and local servers such as Ollama, LM Studio or vLLM. The profile gives `base_url`, `api_key_env`
(the .env variable holding the key; "" for none) and `model_id`.

Structured replies (`json_mode`):
  json_schema  response_format with the reply schema, strict (default; most current providers)
  json_object  plain JSON mode; the schema is appended to the instructions
  none         no response_format; the schema is appended to the instructions
Every reply is validated against the schema by the runner either way.

`extra` is merged into the request body as it is (for example reasoning_effort, temperature),
`extra_headers` into the headers, and `max_tokens_field` names the length field the provider expects
(OpenAI's reasoning models want "max_completion_tokens").
"""
from __future__ import annotations

import base64
import json
import os
import re
import time

import httpx

from .base import FatalError, ModelRequest, ModelResult, RateLimited, RetryableError, SchemaError, api_schema

JSON_MODES = ("json_schema", "json_object", "none")
INFO = {"description": "Any OpenAI-style chat API: OpenAI, Gemini, Mistral, Groq, DeepSeek, xAI, OpenRouter, "
                       "Together, Ollama / LM Studio / vLLM.",
        "default_key_env": None, "needs_base_url": True,
        "optional_fields": ["api_key_env", "json_mode", "max_tokens", "max_tokens_field", "effort", "extra",
                            "extra_headers"]}


class Adapter:
    transport: httpx.BaseTransport | None = None        # tests inject a MockTransport here

    def __init__(self, profile):
        self.profile = profile
        if not profile.base_url:
            raise FatalError(f"profile {profile.name!r} needs base_url for provider openai_compatible")
        if profile.json_mode not in JSON_MODES:
            raise FatalError(f"profile {profile.name!r}: json_mode must be one of {JSON_MODES}")
        headers = {"Content-Type": "application/json", **{k: str(v) for k, v in profile.extra_headers.items()}}
        if profile.api_key_env:
            key = os.environ.get(profile.api_key_env, "").strip()
            if not key:
                raise FatalError(f"{profile.api_key_env} is not set. Add it to .env, or run "
                                 f"`tagger models set-key {profile.api_key_env}`.")
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=profile.base_url.rstrip("/"), headers=headers, timeout=600.0,
                                 transport=type(self).transport)

    def body(self, request: ModelRequest) -> dict:
        p = self.profile
        instructions = request.instructions
        if p.json_mode != "json_schema":
            instructions += ("\n\nReply with one JSON object only, no other text, matching this JSON schema:\n"
                             + json.dumps(api_schema(request.schema)))
        content = []
        if p.accepts_images:
            for img in request.images:
                data = base64.standard_b64encode(img.data).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{data}"}})
        content.append({"type": "text", "text": request.text})
        body = {"model": p.model_id,
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": content}],
                p.max_tokens_field: p.max_tokens}
        if p.json_mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": f"{request.stage}_reply", "strict": True, "schema": api_schema(request.schema)}}
        elif p.json_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if p.effort and "reasoning_effort" not in p.extra:
            body["reasoning_effort"] = p.effort
        body.update(p.extra)
        return body

    def complete(self, request: ModelRequest) -> ModelResult:
        started = time.monotonic()
        try:
            r = self.http.post("/chat/completions", json=self.body(request))
        except httpx.TransportError as e:
            raise RetryableError(f"{type(e).__name__}: {e}") from e
        duration = int((time.monotonic() - started) * 1000)
        if r.status_code == 429:
            raise RateLimited(f"rate limited: {r.text[:300]}", retry_after=_retry_after(r))
        if r.status_code >= 500 or r.status_code in (408, 409):
            raise RetryableError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise FatalError(f"HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
            choice = data["choices"][0]
        except (ValueError, KeyError, IndexError) as e:
            raise RetryableError(f"unexpected response: {r.text[:300]}") from e
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        result = ModelResult(parsed=None, raw=msg.get("content") or "", input_tokens=max(prompt - cached, 0),
                             cached_input_tokens=cached, output_tokens=int(usage.get("completion_tokens") or 0),
                             duration_ms=duration, served_model=data.get("model"),
                             stop_reason=choice.get("finish_reason"), extra={"id": data.get("id")})
        if msg.get("refusal"):
            raise FatalError(f"refused: {str(msg['refusal'])[:200]}", result=result)
        if choice.get("finish_reason") == "length":
            raise SchemaError(f"reply cut off at {self.profile.max_tokens_field}={self.profile.max_tokens}",
                              result=result)
        try:
            result.parsed = json.loads(_strip_fences(result.raw))
        except json.JSONDecodeError as e:
            raise SchemaError(f"reply is not JSON: {e}", result=result) from e
        return result


def _strip_fences(text: str) -> str:
    """Some models wrap JSON in ```json fences even in JSON mode."""
    m = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.S)
    return m.group(1) if m else text


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
