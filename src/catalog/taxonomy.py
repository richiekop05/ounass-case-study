"""Read the facet dictionary workbook (Register + Matrix_long), the supplied policy file and the
optional definitions file into plain records.

Column names are looked up from each header row, never assumed by position. A missing column
the seed needs stops the load with a TaxonomyError.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

REGISTER_SHEET = "Register"
MATRIX_LONG_SHEET = "Matrix_long"
REGISTER_COLUMNS = ("facet_no", "facet_id", "status", "group", "data_type", "cardinality", "allowed_values",
                    "source_of_truth", "unknown_policy")
MATRIX_LONG_COLUMNS = ("node_id", "facet_id", "status")
POLICY_COLUMNS = ("facet_id", "status", "source_policy", "on_missing", "auto_threshold", "min_values",
                  "max_values", "none_is_exclusive")
DEFINITION_COLUMNS = ("facet_id", "value", "definition")

DATA_TYPES = {"enum": "enum", "enum (ordinal)": "ordinal", "boolean": "boolean", "text": "text"}
VALUE_TYPES = ("enum", "ordinal", "boolean")
SOURCE_POLICIES = ("text_only", "text_first", "judge", "derived", "not_tagged")
ON_MISSING = ("unknown", "review_if_required", "default")
UNITS = ("cm", "mm", "ct", "ml", "kg", "g", "l")
APPLICABILITY = {"required": True, "optional": False}
# Values removed by the facet review of 12 Nov 2026 (PRD v3 section 4).
DROPPED_VALUES = {"price_band": {"ultra"}}
# Values the Register does not list yet, added after its own (label, exclusive). 13 Nov 2026:
# season gains all-season, which cannot sit beside warm, transitional or cold;
# colour_primary gains cream. Added from the golden review the same day: beige and mud grey (colour_primary),
# cream, beige and mud grey (colour_secondary), mesh (finish), nickel plate (home_material).
ADDED_VALUES = {"season": [("all-season", True)],
                "colour_primary": [("cream", False), ("beige", False), ("mud grey", False)],
                "colour_secondary": [("cream", False), ("beige", False), ("mud grey", False)],
                "finish": [("mesh", False)],
                "home_material": [("nickel plate", False)]}


class TaxonomyError(ValueError):
    pass


@dataclass
class Value:
    value: str
    label: str
    definition: str | None
    sort_order: int
    definition_version: str | None = None
    is_exclusive: bool = False          # cannot be saved beside another value of the same facet


@dataclass
class Facet:
    facet_id: str
    name: str
    facet_group: str | None
    data_type: str              # enum | ordinal | boolean | number | text
    unit: str | None
    cardinality: str            # single | multi
    kind: str                   # objective | subjective
    source_rule: str | None
    unknown_policy: str | None
    register_status: str        # status in the Register (the policy file's status wins)
    sort_order: int
    values: list[Value] = field(default_factory=list)
    # from config/facet_policy.csv
    status: str = ""
    source_policy: str = ""
    on_missing: str = ""
    auto_threshold: float | None = None
    min_values: int | None = None
    max_values: int | None = None
    none_is_exclusive: bool = False
    default_value: str | None = None
    # from config/facet_definitions.csv
    definition: str | None = None
    definition_version: str | None = None


@dataclass
class Taxonomy:
    source: str
    facets: list[Facet]
    applicability: list[tuple[str, str, bool]]          # (node_id, facet_id, is_required) to load
    skipped_rows: list[tuple[str, str, str]]            # (node_id, facet_id, reason)
    no_value_list: list[str]                            # list facets whose cell describes, not lists
    warnings: list[str]
    unlisted_facets: list[tuple[str, str]] = field(default_factory=list)   # (facet_id, register status)
    definitions_loaded: dict[str, int] = field(default_factory=dict)
    definitions_unmatched: list[tuple[str, str]] = field(default_factory=list)
    definitions_version: str | None = None


# ---------------------------------------------------------------- parsing helpers

def slugify(text: str) -> str:
    t = text.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


_ORDINAL = re.compile(r"^(\d+)\s+(.+)$")                                    # "1 casual"
_NOTE = re.compile(r"^(.*?)\s*\((.+)\)$")                                    # "ultra (relative to category)"
_MEASURE = re.compile(r"^([A-Za-z][A-Za-z\- ]*?)\s+([<>]?\d[\d.\-]*\s*(?:cm|mm)?\+?)$")  # "choker 35-40cm"


def parse_values(raw: str, data_type: str) -> list[Value]:
    """Split allowed_values on ';'. The text is the label; the slug is generated.
    Ordinal facets take sort_order from the leading number."""
    items = [s.strip() for s in str(raw or "").split(";") if s.strip()]
    out: list[Value] = []
    for pos, item in enumerate(items, start=1):
        label, sort = item, pos
        if data_type == "ordinal" and (m := _ORDINAL.match(label)):
            sort, label = int(m.group(1)), m.group(2)
        slug_source = label
        if m := _NOTE.match(slug_source):                        # "(relative to category)" is not part of the slug
            slug_source = m.group(1)
        if m := _MEASURE.match(slug_source):                     # slug from the name; the range stays in the label
            slug_source = m.group(1)
        slug = slugify(slug_source)
        if not slug:
            raise TaxonomyError(f"value {item!r} gives an empty slug")
        out.append(Value(slug, label, None, sort))       # definitions come from facet_definitions.csv
    slugs = [v.value for v in out]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupes:
        raise TaxonomyError(f"values collide after slugging: {dupes} from {raw!r}")
    return out


def is_description(raw: str) -> bool:
    """True when the cell explains the values rather than listing them ("slug of the deepest node
    (L4 ...; L5 ...) e.g. t-shirts"): an example marker, or an item with unbalanced brackets."""
    if "e.g." in raw.lower():
        return True
    return any(item.count("(") != item.count(")") for item in raw.split(";"))


def parse_cardinality(raw: str) -> str:
    low = (raw or "").strip().lower()
    if low.startswith("single"):
        return "single"
    if low.startswith("multi"):
        return "multi"
    raise TaxonomyError(f"cardinality {raw!r} is neither single nor multi")


def parse_data_type(raw: str) -> str:
    low = (raw or "").strip().lower()
    if low.startswith("numeric"):
        return "number"
    if low in DATA_TYPES:
        return DATA_TYPES[low]
    raise TaxonomyError(f"data_type {raw!r} is not enum, enum (ordinal), boolean, numeric or text")


def parse_unit(allowed: str) -> str | None:
    words = re.findall(r"[a-z]+", (allowed or "").lower())
    return next((w for w in words if w in UNITS), None)


def parse_default(unknown_policy: str | None) -> str | None:
    """'defaults false' -> 'false'; 'defaults none' -> 'none'."""
    m = re.search(r"\bdefaults?\s+(?:to\s+)?([\w-]+)", unknown_policy or "", re.I)
    return slugify(m.group(1)) if m else None


def _rows(ws) -> tuple[dict[str, int], list[tuple]]:
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    return {h: i for i, h in enumerate(header) if h}, [r for r in rows[1:] if any(c is not None for c in r)]


# ---------------------------------------------------------------- workbook

def load_taxonomy(path: Path) -> Taxonomy:
    """Register and Matrix_long only. Call apply_policy next; nothing is loadable before it."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sheet in (REGISTER_SHEET, MATRIX_LONG_SHEET):
        if sheet not in wb.sheetnames:
            raise TaxonomyError(f"{path.name}: sheet {sheet!r} not found (have {wb.sheetnames})")

    cols, rows = _rows(wb[REGISTER_SHEET])
    missing = [c for c in REGISTER_COLUMNS if c not in cols]
    if missing:
        raise TaxonomyError(f"Register sheet is missing columns: {missing}")

    def get(row, col):
        i = cols.get(col)
        v = row[i] if i is not None and i < len(row) else None
        return v.strip() if isinstance(v, str) else v

    facets: list[Facet] = []
    no_value_list: list[str] = []
    for row in rows:
        fid = get(row, "facet_id")
        if not fid:
            continue
        data_type = parse_data_type(get(row, "data_type"))
        source = get(row, "source_of_truth")
        allowed = str(get(row, "allowed_values") or "")
        facet = Facet(
            facet_id=fid, name=fid.replace("_", " ").title(), facet_group=get(row, "group"),
            data_type=data_type, unit=parse_unit(allowed) if data_type == "number" else None,
            cardinality=parse_cardinality(get(row, "cardinality")),
            kind="subjective" if "rubric" in (source or "").lower() else "objective",
            source_rule=source, unknown_policy=get(row, "unknown_policy"),
            register_status=(get(row, "status") or "").lower(), sort_order=int(get(row, "facet_no")),
        )
        if data_type in VALUE_TYPES:
            values = [] if is_description(allowed) else parse_values(allowed, data_type)
            dropped = DROPPED_VALUES.get(fid, set())
            values = [v for v in values if v.value not in dropped]
            for label, exclusive in ADDED_VALUES.get(fid, []):
                if slugify(label) not in {v.value for v in values}:
                    values.append(Value(slugify(label), label, None, max((v.sort_order for v in values), default=0) + 1,
                                        is_exclusive=exclusive))
            if len(values) < 2:
                no_value_list.append(fid)
            else:
                facet.values = values
        facets.append(facet)

    known = {f.facet_id for f in facets}
    lcols, lrows = _rows(wb[MATRIX_LONG_SHEET])
    missing = [c for c in MATRIX_LONG_COLUMNS if c not in lcols]
    if missing:
        raise TaxonomyError(f"Matrix_long sheet is missing columns: {missing}")
    applicability: list[tuple[str, str, bool]] = []
    skipped: list[tuple[str, str, str]] = []
    for row in lrows:
        node, fid = str(row[lcols["node_id"]] or "").strip(), str(row[lcols["facet_id"]] or "").strip()
        status = str(row[lcols["status"]] or "").strip().lower()
        if not node or not fid:
            continue
        if status not in APPLICABILITY:
            raise TaxonomyError(f"Matrix_long {node}/{fid}: status {status!r} is not required/optional")
        if fid not in known:
            skipped.append((node, fid, "facet not in Register"))
            continue
        applicability.append((node, fid, APPLICABILITY[status]))

    return Taxonomy(source=path.name, facets=facets, applicability=applicability, skipped_rows=skipped,
                    no_value_list=no_value_list, warnings=[])


# ---------------------------------------------------------------- policy file (supplied, never generated)

def _num(text: str | None, cast):
    text = (text or "").strip()
    return cast(text) if text else None


def _bool(text: str | None) -> bool:
    return (text or "").strip().lower() in ("true", "yes", "1", "y")


def apply_policy(tax: Taxonomy, path: Path, skip_unlisted: bool = False) -> None:
    """Take status, source policy, on_missing, threshold, value limits and none-is-exclusive from
    config/facet_policy.csv. A Register facet with no row stops the load unless skip_unlisted;
    then it is left out entirely and its Matrix_long rows are skipped."""
    if not path.exists():
        raise TaxonomyError(f"{path} not found. It is supplied with the PRD; it is not generated.")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in POLICY_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise TaxonomyError(f"{path.name} is missing columns: {missing}")
        rows: dict[str, dict] = {}
        for r in reader:
            fid = (r.get("facet_id") or "").strip()
            if not fid:
                continue
            if fid in rows:
                raise TaxonomyError(f"{path.name}: {fid} appears twice")
            rows[fid] = r

    by_id = {f.facet_id: f for f in tax.facets}
    extra = sorted(set(rows) - set(by_id))
    if extra:
        raise TaxonomyError(f"{path.name} has facets that are not in the Register: {extra}")
    unlisted = [(f.facet_id, f.register_status) for f in tax.facets if f.facet_id not in rows]
    if unlisted and not skip_unlisted:
        raise TaxonomyError(
            f"{len(unlisted)} Register facets have no row in {path.name} "
            f"(pass --skip-facets-without-policy to leave them out): "
            + ", ".join(f"{fid} ({st})" for fid, st in unlisted))
    tax.unlisted_facets = unlisted

    for f in tax.facets:
        r = rows.get(f.facet_id)
        if r is None:
            continue
        f.status = r["status"].strip().lower()
        f.source_policy = r["source_policy"].strip()
        f.on_missing = r["on_missing"].strip()
        if f.status not in ("active", "retired"):
            raise TaxonomyError(f"{path.name}: {f.facet_id} has status {f.status!r}")
        if f.source_policy not in SOURCE_POLICIES:
            raise TaxonomyError(f"{path.name}: {f.facet_id} has source_policy {f.source_policy!r}; "
                                f"use one of {SOURCE_POLICIES}")
        if f.on_missing not in ON_MISSING:
            raise TaxonomyError(f"{path.name}: {f.facet_id} has on_missing {f.on_missing!r}; use one of {ON_MISSING}")
        try:
            f.auto_threshold = _num(r["auto_threshold"], float)
            f.min_values = _num(r["min_values"], int)
            f.max_values = _num(r["max_values"], int)
        except ValueError as e:
            raise TaxonomyError(f"{path.name}: {f.facet_id}: {e}") from None
        f.none_is_exclusive = _bool(r["none_is_exclusive"])
        for v in f.values:                  # none_is_exclusive marks the facet's "none" value
            if v.value == "none" and f.none_is_exclusive:
                v.is_exclusive = True
        if f.on_missing == "default":
            f.default_value = parse_default(f.unknown_policy)
            slugs = {v.value for v in f.values}
            if f.default_value is None or (slugs and f.default_value not in slugs):
                raise TaxonomyError(f"{f.facet_id}: on_missing is default but the Register's unknown_policy "
                                    f"({f.unknown_policy!r}) names no allowed value")

    skipped_ids = {fid for fid, _ in unlisted}
    tax.facets = [f for f in tax.facets if f.facet_id not in skipped_ids]
    status = {f.facet_id: f.status for f in tax.facets}
    keep: list[tuple[str, str, bool]] = []
    for node, fid, req in tax.applicability:
        if fid in skipped_ids:
            tax.skipped_rows.append((node, fid, "facet has no row in the policy file"))
        elif status[fid] != "active":
            tax.skipped_rows.append((node, fid, "facet is retired"))
        else:
            keep.append((node, fid, req))
    tax.applicability = keep


# ---------------------------------------------------------------- definitions file (optional)

def apply_definitions(tax: Taxonomy, path: Path) -> None:
    """Load rubrics: one row per facet (value empty) or per value. Facets and values with no row
    keep a null definition. Rows that match nothing are reported."""
    if not path.exists():
        tax.warnings.append(f"{path.name} not found; definitions left empty.")
        return
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = [c.strip().lower() for c in (reader.fieldnames or [])]
        missing = [c for c in DEFINITION_COLUMNS if c not in fields]
        if missing:
            raise TaxonomyError(f"{path.name} is missing columns: {missing}")
        version_col = next((c for c in ("version", "rubric_version", "definition_version") if c in fields), None)
        if version_col is None:
            raise TaxonomyError(f"{path.name} has no version column (version / rubric_version)")
        rows = [{k.strip().lower(): (v or "").strip() for k, v in r.items() if k} for r in reader]

    by_id = {f.facet_id: f for f in tax.facets}
    versions = set()
    loaded = {"facets": 0, "values": 0}
    for r in rows:
        fid, value, text = r["facet_id"], r["value"], r["definition"]
        if not fid or not text:
            continue
        version = r[version_col] or None
        versions.add(version)
        facet = by_id.get(fid)
        if facet is None:
            tax.definitions_unmatched.append((fid, value))
            continue
        if not value:
            facet.definition, facet.definition_version = text, version
            loaded["facets"] += 1
            continue
        bare = m.group(2) if (m := _ORDINAL.match(value)) else value      # "1 casual" as written in the Register
        match = next((v for v in facet.values
                      if v.value in (slugify(value), slugify(bare)) or v.label.lower() in (value.lower(), bare.lower())),
                     None)
        if match is None:
            tax.definitions_unmatched.append((fid, value))
            continue
        match.definition, match.definition_version = text, version
        loaded["values"] += 1
    tax.definitions_loaded = loaded
    versions.discard(None)
    if len(versions) > 1:
        tax.warnings.append(f"{path.name} mixes version labels: {sorted(versions)}")
    tax.definitions_version = ", ".join(sorted(versions)) or None
