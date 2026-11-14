"""Provider adapters. One file per provider; each exposes `Adapter`, built from a Profile.

Adding a provider means adding `providers/<name>.py` and a profile with `provider = "<name>"`.
"""
from __future__ import annotations

import importlib

from .base import (FatalError, Image, ModelRequest, ModelResult, Provider, ProviderError,  # noqa: F401
                   RateLimited, RetryableError, SchemaError)


def get_provider(name: str, profile) -> Provider:
    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as e:
        raise FatalError(f"no adapter for provider {name!r} (expected src/tagger/providers/{name}.py)") from e
    return module.Adapter(profile)
