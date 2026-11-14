"""Managing model profiles: list, add, remove, choose the model for each step, store a key, test.

Every function takes plain values and returns plain, JSON-ready data, so the CLI uses them now
and a front end (or an HTTP API in front of it) can call the same functions later.

Profiles stay in config/models.toml and the step defaults in config/pipeline.toml; both files are
edited with tomlkit so their comments survive. API keys are written only to .env and are never
returned or printed: callers see `key_present`, never the key.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import re
import struct
import time
import zlib
from datetime import date
from pathlib import Path

import tomlkit

from . import providers as providers_pkg
from .config import ConfigError, Profile, load_settings, profile_from_dict
from .providers import Image, ModelRequest, ProviderError, get_provider
from .tagging import validate_reply

STEPS = {"categorise": "categorise_model", "tag": "tag_model", "golden_prefill": "golden_prefill_model"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
PRICE_FIELDS = ("input_price", "output_price", "cached_input_price", "cache_write_price", "batch_input_price",
                "batch_output_price")
HIDDEN_PROVIDERS = {"base", "fake"}


class AdminError(ValueError):
    pass


# ------------------------------------------------------------------ reading

def providers() -> list[dict]:
    """Adapters available in src/tagger/providers, with what each needs in a profile."""
    out = []
    for mod in pkgutil.iter_modules(providers_pkg.__path__):
        if mod.name in HIDDEN_PROVIDERS:
            continue
        try:
            m = importlib.import_module(f"{providers_pkg.__name__}.{mod.name}")
        except ImportError as e:
            out.append({"name": mod.name, "available": False, "error": str(e)})
            continue
        if hasattr(m, "Adapter"):
            info = getattr(m, "INFO", {})
            out.append({"name": mod.name, "available": True, **info})
    return out


def _profile_view(p: Profile, settings, env) -> dict:
    key_env = p.api_key_env if p.api_key_env is not None else _default_key_env(p.provider)
    d = {k: getattr(p, k) for k in Profile.__dataclass_fields__}
    d["api_key_env"] = key_env
    d["key_present"] = (not key_env) or bool(env.get(key_env, "").strip())
    d["default_for"] = [step for step, attr in STEPS.items() if getattr(settings, attr) == p.name]
    return d


def list_models(config_dir: Path, env: dict | None = None) -> dict:
    settings = load_settings(config_dir)
    env = os.environ if env is None else env
    return {"defaults": {step: getattr(settings, attr) for step, attr in STEPS.items()},
            "models": [_profile_view(p, settings, env) for p in settings.profiles.values()]}


def _default_key_env(provider: str) -> str | None:
    try:
        m = importlib.import_module(f"{providers_pkg.__name__}.{provider}")
    except ImportError:
        return None
    return getattr(m, "INFO", {}).get("default_key_env")


# ------------------------------------------------------------------ writing

def _load_doc(path: Path) -> tomlkit.TOMLDocument:
    if not path.exists():
        raise AdminError(f"{path} not found")
    return tomlkit.parse(path.read_text())


def _write(path: Path, doc) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(tomlkit.dumps(doc))
    tmp.replace(path)


def validate_fields(name: str, fields: dict) -> Profile:
    """Check a new profile without writing it. Raises AdminError with every problem found."""
    problems = []
    if not NAME_RE.match(name or ""):
        problems.append("name must be 1-40 characters: lower-case letters, digits, '.', '_' or '-'")
    known = {p["name"] for p in providers() if p.get("available")}
    if fields.get("provider") not in known:
        problems.append(f"provider must be one of {sorted(known)}")
    for k in PRICE_FIELDS:
        v = fields.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            problems.append(f"{k} must be a number >= 0 (USD per million tokens)")
    env_name = fields.get("api_key_env")
    if env_name and not ENV_RE.match(env_name):
        problems.append("api_key_env must look like OPENAI_API_KEY (capitals, digits, underscores)")
    if fields.get("provider") == "openai_compatible" and not fields.get("base_url"):
        problems.append("provider openai_compatible needs base_url")
    if fields.get("json_mode") not in (None, "json_schema", "json_object", "none"):
        problems.append("json_mode must be json_schema, json_object or none")
    if problems:
        raise AdminError("; ".join(problems))
    try:
        return profile_from_dict(name, fields, "the new profile")
    except (ConfigError, TypeError) as e:
        raise AdminError(str(e)) from e


def add_model(config_dir: Path, name: str, fields: dict, replace: bool = False) -> dict:
    """Add (or with replace=True, overwrite) a profile in models.toml."""
    fields = {k: v for k, v in fields.items() if v is not None and v != {}}
    for k in PRICE_FIELDS:
        if isinstance(fields.get(k), int) and not isinstance(fields.get(k), bool):
            fields[k] = float(fields[k])
    fields.setdefault("prices_checked", date.today().isoformat())
    validate_fields(name, fields)
    path = config_dir / "models.toml"
    doc = _load_doc(path)
    profiles = doc.setdefault("profiles", tomlkit.table(is_super_table=True))
    if name in profiles and not replace:
        raise AdminError(f"profile {name!r} already exists; pass replace=True (--replace) to overwrite it")
    table = tomlkit.table()
    for k, v in fields.items():
        if k == "prices_checked":
            table[k] = tomlkit.date(v) if re.match(r"^\d{4}-\d{2}-\d{2}$", str(v)) else str(v)
        elif isinstance(v, dict):
            inline = tomlkit.inline_table()
            inline.update(v)
            table[k] = inline
        else:
            table[k] = v
    profiles[name] = table
    _write(path, doc)
    load_settings(config_dir)                        # the file still loads
    return next(m for m in list_models(config_dir)["models"] if m["name"] == name)


def remove_model(config_dir: Path, name: str) -> dict:
    settings = load_settings(config_dir)
    if name not in settings.profiles:
        raise AdminError(f"no profile {name!r}; valid names: {', '.join(sorted(settings.profiles))}")
    using = [step for step, attr in STEPS.items() if getattr(settings, attr) == name]
    if using:
        raise AdminError(f"{name!r} is the default for {using}; choose another model for that step first")
    path = config_dir / "models.toml"
    doc = _load_doc(path)
    del doc["profiles"][name]
    _write(path, doc)
    return {"removed": name}


def set_default(config_dir: Path, step: str, name: str) -> dict:
    """Choose the model a step uses by default (a run can still override it with a flag)."""
    if step not in STEPS:
        raise AdminError(f"step must be one of {sorted(STEPS)}")
    settings = load_settings(config_dir)
    if name not in settings.profiles:
        raise AdminError(f"no profile {name!r}; valid names: {', '.join(sorted(settings.profiles))}")
    path = config_dir / "pipeline.toml"
    doc = _load_doc(path)
    doc[STEPS[step]] = name
    _write(path, doc)
    return list_models(config_dir)["defaults"]


def set_key(env_path: Path, env_name: str, value: str) -> dict:
    """Store an API key in .env (replacing an existing line). The value is never returned."""
    if not ENV_RE.match(env_name or ""):
        raise AdminError("the variable name must look like OPENAI_API_KEY")
    value = (value or "").strip()
    if not value or "\n" in value or "\r" in value:
        raise AdminError("the key is empty or has a line break")
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(env_name)}\s*=")
    new_line = f"{env_name}={value}"
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i], replaced = new_line, True
    if not replaced:
        lines.append(new_line)
    env_path.write_text("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)
    os.environ[env_name] = value
    return {"env": env_name, "replaced": replaced, "key_present": True}


# ------------------------------------------------------------------ testing

TEST_SCHEMA = {"type": "object",
               "properties": {"answer": {"type": "string", "enum": ["ready"]},
                              "colour": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
               "required": ["answer", "colour"], "additionalProperties": False}


def _red_png(size: int = 16) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xdc\x14\x3c" * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def check_model(config_dir: Path, name: str) -> dict:
    """One small structured request: checks the key, the model ID, JSON replies and (if the profile
    accepts images) image input. Costs a fraction of a cent."""
    settings = load_settings(config_dir)
    if name not in settings.profiles:
        raise AdminError(f"no profile {name!r}; valid names: {', '.join(sorted(settings.profiles))}")
    p = settings.profiles[name]
    images = [Image(_red_png(), "image/png", "test/red.png")] if p.accepts_images else []
    text = ("This is a connection test. Set answer to \"ready\". "
            + ("Set colour to the colour of the attached square, in one word." if images
               else "Set colour to null."))
    started = time.monotonic()
    try:
        adapter = get_provider(p.provider, p)
        result = adapter.complete(ModelRequest("test", "You reply only with JSON.", text, images, TEST_SCHEMA))
        validate_reply(TEST_SCHEMA, result.parsed)
    except ProviderError as e:
        return {"name": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                "seconds": round(time.monotonic() - started, 2)}
    return {"name": name, "ok": True, "reply": result.parsed, "served_model": result.served_model,
            "images_checked": bool(images),
            "image_ok": (str(result.parsed.get("colour") or "").lower().find("red") >= 0) if images else None,
            "input_tokens": result.input_tokens + result.cached_input_tokens + result.cache_write_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": round(p.cost(result.input_tokens, result.output_tokens, result.cached_input_tokens,
                                     result.cache_write_tokens), 6),
            "seconds": round(time.monotonic() - started, 2)}
