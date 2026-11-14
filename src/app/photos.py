"""Short-lived signed URLs for product photos in the private bucket (Build PRD 2).

The browser only ever sees signed URLs; never the service key or an unsigned storage path. Links last an
hour and are cached for 50 minutes so list pages do not re-sign on every request.
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

import httpx

from tagger.products import BUCKET

from .db import ROOT

EXPIRES = 3600
_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()
_client: httpx.Client | None = None
_base = ""


def _http() -> httpx.Client | None:
    global _client, _base
    if _client is None:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not key:
            return None
        parts = urlsplit(url)
        _base = f"{parts.scheme}://{parts.netloc}/storage/v1"
        headers = {"apikey": key}
        if not key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {key}"
        _client = httpx.Client(base_url=_base, headers=headers, timeout=20.0)
    return _client


def sign(paths: list[str]) -> dict[str, str | None]:
    """Signed URL per storage path; None when a photo cannot be signed (the page shows its placeholder)."""
    now = time.time()
    out: dict[str, str | None] = {}
    todo = []
    with _lock:
        for p in paths:
            hit = _cache.get(p)
            if hit and hit[0] > now:
                out[p] = hit[1]
            elif p:
                todo.append(p)
    if not todo:
        return out
    client = _http()
    if client is None:
        return {**out, **{p: None for p in todo}}
    try:
        r = client.post(f"/object/sign/{BUCKET}", json={"expiresIn": EXPIRES, "paths": todo})
        r.raise_for_status()
        items = r.json()
    except (httpx.HTTPError, ValueError):
        return {**out, **{p: None for p in todo}}
    with _lock:
        for it in items:
            p, signed = it.get("path"), it.get("signedURL")
            if p and signed and not it.get("error"):
                full = _base + signed
                _cache[p] = (now + EXPIRES - 600, full)
                out[p] = full
            elif p:
                out[p] = None
    for p in todo:
        out.setdefault(p, None)
    return out
