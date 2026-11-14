"""Facets as the tagger sees them, read from the database on every run (nothing hard-coded)."""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from catalog.taxonomy import slugify

MODEL_POLICIES = ("text_only", "text_first", "judge")


@dataclass
class FacetValue:
    facet_value_id: int
    value: str                  # slug
    label: str                  # what the model sees and returns
    definition: str | None = None
    is_exclusive: bool = False  # cannot sit beside another value of the facet


@dataclass
class FacetSpec:
    facet_id: str
    name: str
    definition: str | None
    source_policy: str
    data_type: str
    cardinality: str
    unit: str | None
    min_values: int | None
    max_values: int | None
    none_is_exclusive: bool
    auto_threshold: float | None
    on_missing: str
    default_value: str | None
    sort_order: int | None
    is_required: bool = False
    values: list[FacetValue] = field(default_factory=list)

    @property
    def has_value_list(self) -> bool:
        return self.data_type in ("enum", "ordinal", "boolean")

    def lookup(self, text) -> FacetValue | None:
        """A value by its label (as shown to the model), its slug, or its label in any case."""
        if text is None:
            return None
        s = str(text).strip()
        for v in self.values:
            if v.label == s or v.value == s:
                return v
        low, slug = s.lower(), slugify(s)
        hit = next((v for v in self.values if v.label.lower() == low or v.value == slug), None)
        if hit is None:          # one part of a slash label: "bold" for "bold/statement", if only one fits
            parts = [v for v in self.values if "/" in v.label
                     and low in (x.strip().lower() for x in v.label.split("/"))]
            hit = parts[0] if len(parts) == 1 else None
        if hit is None:          # spacing and hyphens ignored: "nickelplate" for "nickel plate"
            def squash(x: str) -> str:
                return x.replace("-", "").replace("_", "")
            close = [v for v in self.values if squash(v.value) == squash(slug)]
            hit = close[0] if len(close) == 1 else None
        return hit

    def limit_text(self) -> str:
        if self.data_type == "number":
            return f"Takes one number{f', in {self.unit}' if self.unit else ''}."
        if self.data_type == "text":
            return "Takes one text value."
        if self.max_values == 1 or (self.max_values is None and self.cardinality == "single"):
            return "Takes one value."
        n = f"up to {self.max_values} values" if self.max_values else "one or more values"
        excl = [v.label for v in self.values if v.is_exclusive]
        tail = "".join(f' "{label}" cannot sit beside another value.' for label in excl)
        return f"Takes {n}.{tail}"

    @property
    def exclusive_values(self) -> list[FacetValue]:
        return [v for v in self.values if v.is_exclusive]

    def block(self) -> str:
        """The facet as it appears in a tag request (PRD 4.2)."""
        lines = [f"FACET: {self.facet_id}", f"Policy: {self.source_policy}. {self.limit_text()}"]
        if self.definition:
            lines.append(f"Rule: {self.definition}")
        if self.values:
            lines.append("Values:")
            for v in self.values:
                lines.append(f"  - {v.label}: {v.definition}" if v.definition else f"  - {v.label}")
        return "\n".join(lines)


FACET_SQL = """select f.facet_id, f.name, f.definition, f.source_policy, f.data_type, f.cardinality, f.unit,
                      f.min_values, f.max_values, f.none_is_exclusive,
                      coalesce(f.review_threshold, (select default_review_threshold from app_settings)),
                      f.on_missing,
                      f.default_value, f.sort_order, {required}
               from facets f {join}
               where f.status = 'active' {where}
               order by f.sort_order nulls last, f.facet_id"""


def _specs(conn: psycopg.Connection, sql: str, params, active_values_only: bool = False) -> list[FacetSpec]:
    specs = [FacetSpec(*r[:14], is_required=bool(r[14])) for r in conn.execute(sql, params)]
    if specs:
        by_id = {s.facet_id: s for s in specs}
        for fid, vid, value, label, definition, excl in conn.execute(
                "select facet_id, facet_value_id, value, label, definition, is_exclusive from facet_values "
                "where facet_id = any(%s)" + (" and is_active" if active_values_only else "") +
                " order by facet_id, sort_order nulls last, facet_value_id",
                (list(by_id),)):
            by_id[fid].values.append(FacetValue(vid, value, label, definition, bool(excl)))
    for s in specs:
        s.auto_threshold = float(s.auto_threshold) if s.auto_threshold is not None else None
    return specs


def function_category(path: str, function_categories: dict[str, str]) -> str | None:
    """The function label for a product under a functional category (config/pipeline.toml), else None."""
    segments = [p.strip().lower() for p in (path or "").split("/")]
    for name, label in function_categories.items():
        if name.lower() in segments:
            return label
    return None


def facets_for_tagging(conn: psycopg.Connection, category_id: str, category_path: str,
                       function_categories: dict[str, str]) -> list[FacetSpec]:
    """PRD 4.1: active facets mapped to the category with a model-facing policy.
    `function` is left out under functional categories; step 3 sets it."""
    sql = FACET_SQL.format(required="cf.is_required",
                           join="join category_facets cf on cf.facet_id = f.facet_id and cf.category_id = %s",
                           where="and f.source_policy = any(%s)")
    # an inactive value is never offered to the model; existing tags that use it are kept (Build PRD 3)
    specs = _specs(conn, sql, (category_id, list(MODEL_POLICIES)), active_values_only=True)
    if function_category(category_path, function_categories):
        specs = [s for s in specs if s.facet_id != "function"]
    return specs


def facets_for_category(conn: psycopg.Connection, category_id: str) -> dict[str, FacetSpec]:
    """Every active facet mapped to the category, any policy (step 3, golden sheet)."""
    sql = FACET_SQL.format(required="cf.is_required",
                           join="join category_facets cf on cf.facet_id = f.facet_id and cf.category_id = %s",
                           where="")
    return {s.facet_id: s for s in _specs(conn, sql, (category_id,))}


def all_facets(conn: psycopg.Connection) -> dict[str, FacetSpec]:
    return {s.facet_id: s for s in _specs(conn, FACET_SQL.format(required="false", join="", where=""), ())}


def rubric_version(conn: psycopg.Connection) -> str | None:
    rows = [r[0] for r in conn.execute(
        "select distinct definition_version from facets where definition_version is not null "
        "union select distinct definition_version from facet_values where definition_version is not null")]
    return ",".join(sorted(rows)) or None
