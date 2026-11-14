"""Pull the page's embedded app state (`window.initialState = {...}`) out of the HTML."""
from __future__ import annotations

import json
import re

_MARKER = re.compile(r"window\.initialState\s*=\s*")


class NoEmbeddedState(ValueError):
    pass


def extract_initial_state(html: str) -> dict:
    m = _MARKER.search(html)
    if not m:
        raise NoEmbeddedState("window.initialState not found in page")
    obj, _ = json.JSONDecoder().raw_decode(html, m.end())
    if not isinstance(obj, dict):
        raise NoEmbeddedState("window.initialState is not an object")
    return obj


def canonical_url(html: str) -> str | None:
    m = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
    return m.group(1) if m else None
