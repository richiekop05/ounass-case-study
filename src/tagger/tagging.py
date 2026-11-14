"""Step 2 logic with no I/O: the reply schema, the checks in PRD 4.4 and the status table in 4.5."""
from __future__ import annotations

from dataclasses import dataclass, field

import jsonschema

from .facets import FacetSpec, FacetValue
from .products import quote_found
from .providers import SchemaError

STATES = ("value", "unknown", "not_applicable")
EVIDENCE = ("text", "image", "both")


# ------------------------------------------------------------------ schema (4.3)

def facet_schema(spec: FacetSpec, value_enums: bool = True) -> dict:
    if spec.has_value_list:
        item = {"type": "string", "enum": [v.label for v in spec.values]} if value_enums else {"type": "string"}
        values = {"type": "array", "items": item}
        if spec.max_values and value_enums:
            values["maxItems"] = spec.max_values
    elif spec.data_type == "number":
        values = {"type": "array", "items": {"type": "number"}, **({"maxItems": 1} if value_enums else {})}
    else:
        values = {"type": "array", "items": {"type": "string"}, **({"maxItems": 1} if value_enums else {})}
    return {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": list(STATES)},
            "values": values,
            # No nullable fields: the structured-output API allows only 16 union-typed fields per
            # schema, and a product has up to ~20 facets. "No value" is 0 / "none" / "" instead.
            "confidence": {"type": "number"},
            "evidence": {"type": "string", "enum": [*EVIDENCE, "none"]},
            "quote": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["state", "values", "confidence", "evidence", "quote", "reason"],
        "additionalProperties": False,
    }


def reply_schema(specs: list[FacetSpec], value_enums: bool = True) -> dict:
    """One property per facet, all required (PRD 4.3). Where the PRD says "or null", the reply uses
    confidence 0, evidence "none" and quote "" instead (see facet_schema).

    value_enums=True is the full schema: values limited to each facet's allowed labels.
    value_enums=False is what is sent with a request. Providers compile the schema into a grammar,
    and ~20 facets each with their own answer object and value list is too large to compile.
    So the request schema defines the answer object once ($defs: one for list / text facets, one
    for number facets) and every facet refers to it; allowed values are given in the prompt and
    enforced by check 4.4."""
    if value_enums:
        props = {s.facet_id: facet_schema(s, True) for s in specs}
        defs = {}
    else:
        defs = {"answer": facet_schema(_TEXTLIKE, False), "number_answer": facet_schema(_NUMBER, False)}
        props = {s.facet_id: {"$ref": "#/$defs/number_answer" if s.data_type == "number" else "#/$defs/answer"}
                 for s in specs}
    out = {"type": "object", "properties": props, "required": [s.facet_id for s in specs],
           "additionalProperties": False}
    if defs:
        out["$defs"] = defs
    return out


_TEXTLIKE = FacetSpec("_", "_", None, "judge", "enum", "multi", None, None, None, False, None, "unknown", None, None)
_NUMBER = FacetSpec("_", "_", None, "judge", "number", "single", None, None, None, False, None, "unknown", None, None)


def _without(schema, keys):
    if isinstance(schema, dict):
        return {k: _without(v, keys) for k, v in schema.items() if k not in keys}
    if isinstance(schema, list):
        return [_without(x, keys) for x in schema]
    return schema


def validate_reply(schema: dict, reply) -> None:
    """Raise SchemaError if the reply does not match. Too many values is not a schema failure:
    check 4.4 trims them, so maxItems is not enforced here."""
    v = jsonschema.Draft202012Validator(_without(schema, {"maxItems"}))
    errors = sorted(v.iter_errors(reply), key=lambda e: list(e.absolute_path))
    if errors:
        msgs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors[:5]]
        raise SchemaError("reply does not match the schema: " + "; ".join(msgs))


def build_request_text(product_text: str, category_path: str, specs: list[FacetSpec]) -> str:
    return (f"{product_text}\n\nCATEGORY:\n{category_path}\n\n"
            f"FACETS ({len(specs)}):\n\n" + "\n\n".join(s.block() for s in specs))


# ------------------------------------------------------------------ checks (4.4)

@dataclass
class Answer:
    facet_id: str
    state: str
    values: list = field(default_factory=list)      # FacetValue, float or str
    confidence: float | None = None
    evidence: str | None = None
    quote: str | None = None
    reason: str = ""
    force_review: bool = False
    notes: list[str] = field(default_factory=list)


def _unknown(a: Answer, why: str) -> Answer:
    a.state, a.values, a.confidence, a.evidence, a.quote = "unknown", [], None, None, None
    a.notes.append(why)
    return a


def check_answer(spec: FacetSpec, raw: dict | None, searchable: str) -> Answer:
    """Apply the PRD 4.4 checks in order. A failed check changes this one answer, never the product."""
    if not isinstance(raw, dict):
        return _unknown(Answer(spec.facet_id, "unknown"), "no answer returned")
    conf = raw.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
    if conf is not None and not 0 <= conf <= 1:
        conf = None
    a = Answer(spec.facet_id, raw.get("state") if raw.get("state") in STATES else "unknown",
               list(raw.get("values") or []), conf,
               raw.get("evidence") if raw.get("evidence") in EVIDENCE else None,
               raw.get("quote"), str(raw.get("reason") or ""))
    if a.state != "value":
        a.values, a.confidence = [], None
        if a.state == "not_applicable":
            a.evidence, a.quote = None, None
        return a if raw.get("values") in (None, []) else _unknown(a, f"state {a.state} but values given")
    if not a.values:
        return _unknown(a, "state value but no values")

    # Values must be allowed (enum) or well-formed (number / text).
    if spec.has_value_list:
        kept, dropped = [], []
        for x in a.values:
            fv = spec.lookup(x)
            (kept if fv and fv not in kept else dropped).append(fv or x)
        if dropped:
            a.notes.append(f"dropped values not allowed: {[str(d) for d in dropped if not isinstance(d, FacetValue)]}"
                           if any(not isinstance(d, FacetValue) for d in dropped) else "dropped duplicate values")
        a.values = kept
        if not kept:
            return _unknown(a, "no allowed value given")
        # Limits: an exclusive value beside others, then keep the first max_values.
        # "none" beside real values is dropped (PRD 4.4); any other exclusive value, such as
        # season's all-season, wins and is kept on its own.
        exclusive = [v for v in a.values if v.is_exclusive]
        if exclusive and len(a.values) > 1:
            winners = [v for v in exclusive if v.value != "none"]
            if winners:
                a.values = [winners[0]]
                a.notes.append(f'kept only "{winners[0].label}" (it cannot sit beside other values)')
            else:
                a.values = [v for v in a.values if v.value != "none"]
                a.notes.append('dropped "none" beside other values')
        if spec.max_values and len(a.values) > spec.max_values:
            a.notes.append(f"kept the first {spec.max_values} of {len(a.values)} values")
            a.values = a.values[:spec.max_values]

    if spec.source_policy == "text_only" and a.evidence in ("image", "both"):
        return _unknown(a, "photo used on a text-only facet")

    if is_default_answer(spec, a.values):
        pass                    # the facet's default value needs no quote (13 Nov 2026)
    elif a.evidence in ("text", "both") and not quote_found(a.quote, searchable):
        a.force_review = True
        a.notes.append("quote not found in text")

    if spec.data_type == "number":
        x = a.values[0]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0:
            return _unknown(a, "number is not positive")
        a.values = [float(x)]
    elif spec.data_type == "text":
        x = str(a.values[0]).strip()
        if not x:
            return _unknown(a, "empty text value")
        a.values = [x]
    return a


# ------------------------------------------------------------------ statuses (4.5)

@dataclass
class TagRow:
    facet_id: str
    status: str
    facet_value_id: int | None = None
    value_number: float | None = None
    value_text: str | None = None
    confidence: float | None = None
    evidence: str | None = None
    quote: str | None = None
    reason: str | None = None
    label: str | None = None        # for display only
    review_reason: str | None = None

    def as_db(self, product_id: str, run_id: int) -> tuple:
        return (product_id, run_id, self.facet_id, self.facet_value_id, self.value_number, self.value_text,
                self.status, self.confidence, self.evidence, self.quote, self.reason, self.review_reason)


def is_default_answer(spec: FacetSpec, values: list) -> bool:
    """The answer is exactly the facet's default value (exclusivity none, is_set false)."""
    if spec.default_value is None or len(values) != 1:
        return False
    default = spec.lookup(spec.default_value)
    v = values[0]
    return default is not None and getattr(v, "facet_value_id", None) == default.facet_value_id


def review_reason(spec: FacetSpec, a: Answer) -> str | None:
    """Why a value answer goes to review, or None when it is accepted (Build PRD 3, review_reason).

    The level is applied to every model-written value answer (decision of 14 Nov 2026); `auto_threshold`
    holds the facet's level from the database (review_threshold, else the default in app_settings)."""
    if is_default_answer(spec, a.values):
        return None             # an answer equal to the default needs no quote and is accepted
    if a.force_review:
        return "quote_not_found"
    if spec.source_policy == "text_only" and a.evidence != "text":
        return "text_policy"
    if spec.auto_threshold is None or a.confidence is None or a.confidence < spec.auto_threshold:
        return "low_confidence"
    return None


def value_status(spec: FacetSpec, a: Answer) -> str:
    return "review" if review_reason(spec, a) else "auto"


def rows_for(spec: FacetSpec, a: Answer) -> list[TagRow]:
    """Turn a checked answer into product_facet_tags rows (PRD 4.5). One row per value."""
    reason = "; ".join([a.reason] + a.notes if a.reason else a.notes) or None
    if a.state == "not_applicable":
        return [TagRow(spec.facet_id, "not_applicable", reason=reason)]
    if a.state == "unknown":
        if spec.on_missing == "default" and spec.default_value is not None:
            fv = spec.lookup(spec.default_value)
            if fv:
                return [TagRow(spec.facet_id, "auto", facet_value_id=fv.facet_value_id, evidence="rule",
                               reason=f"default: {spec.default_value} when unknown ({reason or 'no value found'})",
                               label=fv.label)]
        if spec.on_missing == "review_if_required" and spec.is_required:
            return [TagRow(spec.facet_id, "review", reason=reason, review_reason="missing_required")]
        return [TagRow(spec.facet_id, "abstained", reason=reason)]

    status = value_status(spec, a)
    quote = a.quote if a.evidence in ("text", "both") else None
    rows = []
    for v in a.values:
        row = TagRow(spec.facet_id, status, confidence=a.confidence, evidence=a.evidence, quote=quote, reason=reason,
                     review_reason=review_reason(spec, a))
        if isinstance(v, FacetValue):
            row.facet_value_id, row.label = v.facet_value_id, v.label
        elif spec.data_type == "number":
            row.value_number, row.label = v, str(v)
        else:
            row.value_text, row.label = v, v
        rows.append(row)
    return rows


def rows_from_reply(specs: list[FacetSpec], reply: dict, searchable: str) -> list[TagRow]:
    rows = []
    for spec in specs:
        rows += rows_for(spec, check_answer(spec, reply.get(spec.facet_id), searchable))
    return rows
