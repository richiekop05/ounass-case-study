"""Settings that live in the database since the review app (Build PRD 3, migration 017).

The category gate and the two default model profiles are read from `app_settings` at the start of every
run and every live tag job, so a change saved in the app is used by the next product tagged. Per-facet
levels are read with the facets themselves (tagger.facets). config/pipeline.toml keeps the other settings;
its gate and model names were only the seed and are no longer read.
"""
from __future__ import annotations

import psycopg

from .config import ConfigError, Settings


def apply(settings: Settings, conn: psycopg.Connection) -> Settings:
    """Overwrite the category gate and default models with the values stored in the database."""
    exists = conn.execute("select to_regclass('public.app_settings')").fetchone()[0]
    if not exists:
        return settings
    row = conn.execute("""select category_review_threshold, categorise_model, tag_model
                          from app_settings""").fetchone()
    if not row:
        raise ConfigError("app_settings has no row: run `catalog migrate` (migration 017)")
    gate, cat_model, tag_model = row
    for step, name in (("categorising", cat_model), ("tagging", tag_model)):
        if name not in settings.profiles:
            raise ConfigError(f"the saved {step} model '{name}' is not a profile in config/models.toml; "
                              f"choose another on the Models tab or add the profile")
    settings.category_auto_threshold = float(gate)
    settings.categorise_model, settings.tag_model = cat_model, tag_model
    return settings


def apply_from_url(settings: Settings, url: str) -> Settings:
    with psycopg.connect(url) as conn:
        return apply(settings, conn)
