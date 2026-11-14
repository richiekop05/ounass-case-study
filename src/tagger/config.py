"""Model profiles (config/models.toml) and pipeline settings (config/pipeline.toml).

No model name or price lives in code: everything here is read from those two files.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    provider: str
    model_id: str
    accepts_images: bool
    input_price: float
    output_price: float
    cached_input_price: float | None = None
    cache_write_price: float | None = None
    batch_input_price: float | None = None
    batch_output_price: float | None = None
    effort: str | None = None
    max_tokens: int = 16000
    fallbacks: str | None = None
    # Connection: which .env variable holds the key ("" = no key, e.g. a local server) and,
    # for providers that need one, the API base URL.
    api_key_env: str | None = None
    base_url: str | None = None
    # openai_compatible only: how to ask for JSON ("json_schema", "json_object" or "none"),
    # the name of the reply-length field, and extra body fields / headers sent as they are.
    json_mode: str = "json_schema"
    max_tokens_field: str = "max_tokens"
    extra: dict = field(default_factory=dict)
    extra_headers: dict = field(default_factory=dict)
    prices_checked: str | None = None
    notes: str | None = None

    def cost(self, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0,
             cache_write_tokens: int = 0) -> float:
        return price_tokens(input_tokens, output_tokens, cached_input_tokens, cache_write_tokens,
                            self.input_price, self.output_price, self.cached_input_price,
                            self.cache_write_price)


def price_tokens(input_tokens, output_tokens, cached_input_tokens, cache_write_tokens,
                 input_price, output_price, cached_input_price, cache_write_price) -> float:
    """USD for one call. Prices are per million tokens; missing cache prices fall back to the input price."""
    ip = float(input_price)
    cached = float(cached_input_price) if cached_input_price is not None else ip
    write = float(cache_write_price) if cache_write_price is not None else ip
    return (input_tokens * ip + cached_input_tokens * cached + cache_write_tokens * write
            + output_tokens * float(output_price)) / 1_000_000


@dataclass
class DeriveSettings:
    price_band_min_department: int = 9
    recency_new_in_days: int = 30
    recency_current_days: int = 180
    metallic_colours: list[str] = field(default_factory=list)
    case_size_small_below_mm: float = 34
    case_size_large_above_mm: float = 40
    gift_text_keyword: str = "gift"
    giftable_categories_file: str = "config/giftable_categories.csv"
    gift_occasion_rules_file: str = "config/gift_occasion_rules.csv"


@dataclass
class Settings:
    categorise_model: str
    tag_model: str
    category_auto_threshold: float = 0.80
    max_images: int = 4
    concurrency: int = 4
    function_categories: dict[str, str] = field(default_factory=dict)
    derive: DeriveSettings = field(default_factory=DeriveSettings)
    profiles: dict[str, Profile] = field(default_factory=dict)
    monitor: dict = field(default_factory=dict)
    golden_prefill_model: str | None = None

    def profile(self, name: str) -> Profile:
        if name not in self.profiles:
            raise ConfigError(f"unknown model profile {name!r}; valid names: {', '.join(sorted(self.profiles))}")
        return self.profiles[name]


PROFILE_FIELDS = {f for f in Profile.__dataclass_fields__} - {"name"}
REQUIRED_FIELDS = ("provider", "model_id", "accepts_images", "input_price", "output_price")


def profile_from_dict(name: str, p: dict, source: str = "models.toml") -> Profile:
    missing = [k for k in REQUIRED_FIELDS if k not in p]
    if missing:
        raise ConfigError(f"profile {name!r} in {source} is missing {missing}")
    unknown = sorted(set(p) - PROFILE_FIELDS)
    if unknown:
        raise ConfigError(f"profile {name!r} in {source} has unknown fields {unknown}; "
                          f"allowed: {sorted(PROFILE_FIELDS)}")
    values = {k: (str(v) if k == "prices_checked" else v) for k, v in p.items()}
    values["extra"] = dict(values.get("extra") or {})
    values["extra_headers"] = dict(values.get("extra_headers") or {})
    return Profile(name=name, **values)


def load_profiles(path: Path) -> dict[str, Profile]:
    if not path.exists():
        raise ConfigError(f"{path} not found")
    data = tomllib.loads(path.read_text())
    out = {}
    for name, p in data.get("profiles", {}).items():
        out[name] = profile_from_dict(name, p, path.name)
    if not out:
        raise ConfigError(f"{path} has no [profiles.<name>] entries")
    return out


def load_settings(config_dir: Path) -> Settings:
    path = config_dir / "pipeline.toml"
    if not path.exists():
        raise ConfigError(f"{path} not found")
    data = tomllib.loads(path.read_text())
    derive = DeriveSettings(**data.get("derive", {}))
    s = Settings(categorise_model=data["categorise_model"], tag_model=data["tag_model"],
                 category_auto_threshold=float(data.get("category_auto_threshold", 0.80)),
                 max_images=int(data.get("max_images", 4)), concurrency=int(data.get("concurrency", 4)),
                 function_categories=dict(data.get("function_categories", {})), derive=derive,
                 monitor=dict(data.get("monitor", {})),
                 golden_prefill_model=data.get("golden_prefill_model"),
                 profiles=load_profiles(config_dir / "models.toml"))
    for step in ("categorise_model", "tag_model"):
        s.profile(getattr(s, step))   # a bad default stops here with the valid names
    return s
