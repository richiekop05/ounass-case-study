"""Facets tab (Design PRD 9, Build PRD 3 and 6): each facet's rules and values, edited in the database.

The pipeline reads facets, values and their definitions from the database for every product it tags, so a
save shapes the next product tagged. Decided 14 Nov 2026: the facet and value definitions (the rubric sent
to the model) are editable; any save that changes a rubric gives the facet a new rubric version (v6 -> v6.1;
none -> v1) and stamps the values it changed with it. Every save writes a facet_changes row with the
facet before and after, in the same transaction, and raises app_settings.taxonomy_version, which each run
records. A save that would leave a stored tag breaking the catalog rules is refused with the reasons.
"""
from __future__ import annotations

import re
import time

from psycopg.types.json import Jsonb

from . import jobs
from .db import commit, end, get_db, read

MODEL_POLICIES = ("text_only", "text_first", "judge")
POLICY_TEXT = {"text_only": "Product text only", "text_first": "Product text first, then photos",
               "judge": "Judge from text and photos", "derived": "Set by rule", "not_tagged": "Not tagged"}


class FacetError(Exception):
    def __init__(self, message: str, status: int = 400, problems: list[str] | None = None,
                 fields: dict | None = None):
        super().__init__(message)
        self.message, self.status, self.problems, self.fields = message, status, problems or [], fields or {}


def _group(policy: str, status: str) -> str:
    if status == "retired":
        return "retired"
    return "ai" if policy in MODEL_POLICIES else "rule"


def next_version(v: str | None) -> str:
    """v6 -> v6.1 -> v6.2; v1 -> v1.1; none -> v1."""
    if not v:
        return "v1"
    m = re.fullmatch(r"(.*?)(?:\.(\d+))?", v)
    base, minor = m.group(1), m.group(2)
    return f"{base}.{int(minor) + 1}" if minor else f"{v}.1"


# ------------------------------------------------------------------ list and detail

def facet_list(category: str = "", q: str = "") -> list[dict]:
    rows = read("""
        select f.facet_id, f.name, f.source_policy, f.status, f.edited_in_app,
               (select count(*) from facet_values v where v.facet_id = f.facet_id and v.is_active),
               (select count(*) from facet_values v where v.facet_id = f.facet_id)
          from facets f
         where (%(cat)s::text = '' or exists (
                  select 1 from category_facets cf join categories c on c.node_id = cf.category_id
                    join categories n on n.node_id = %(cat)s::text
                   where cf.facet_id = f.facet_id and (c.path = n.path or c.path like n.path || ' / %%')))
           and (%(q)s::text = '' or f.name ilike %(like)s or f.facet_id ilike %(like)s)
         order by f.sort_order nulls last, f.name""", {"cat": category, "q": q.strip(), "like": f"%{q.strip()}%"})
    return [{"id": i, "name": n, "group": _group(p, st), "values": a, "allValues": t, "edited": bool(e)}
            for i, n, p, st, e, a, t in rows]


def _snapshot(conn, fid: str) -> dict | None:
    """The editable parts of a facet and its values, as stored (for History and Restore)."""
    f = conn.execute("""select facet_id, name, definition, definition_version, source_policy, on_missing,
                               max_values, cardinality, data_type, status, default_value
                          from facets where facet_id = %s""", (fid,)).fetchone()
    if not f:
        return None
    req = conn.execute("""select count(*) filter (where is_required), count(*) from category_facets
                           where facet_id = %s""", (fid,)).fetchone()
    vals = conn.execute("""select facet_value_id, value, label, definition, definition_version, is_active,
                                  is_exclusive, sort_order
                             from facet_values where facet_id = %s order by sort_order nulls last, facet_value_id""",
                        (fid,)).fetchall()
    return {"id": f[0], "name": f[1], "definition": f[2], "version": f[3], "policy": f[4], "onMissing": f[5],
            "maxValues": f[6], "cardinality": f[7], "dataType": f[8], "status": f[9], "defaultValue": f[10],
            "required": req[0] > 0 and req[0] == req[1], "requiredIn": req[0], "categoriesCount": req[1],
            "values": [{"id": v[0], "value": v[1], "label": v[2], "definition": v[3], "version": v[4],
                        "active": v[5], "exclusive": v[6], "sort": v[7]} for v in vals]}


def facet_detail(fid: str) -> dict:
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            snap = _snapshot(conn, fid)
            if snap is None:
                raise FacetError("That facet is not known.", 404)
            extra = conn.execute("""
                select f.source_rule, f.unknown_policy, f.edited_in_app, f.updated_at, f.review_threshold,
                       (select default_review_threshold from app_settings),
                       (select taxonomy_version from app_settings)
                  from facets f where f.facet_id = %s""", (fid,)).fetchone()
            uses = dict(conn.execute("""
                select t.facet_value_id, count(distinct t.product_id)
                  from product_facet_tags t join catalog_products cp on cp.product_id = t.product_id
                   and t.tagging_run_id in (cp.ai_run_id, cp.current_run_id)
                 where t.facet_id = %s and t.facet_value_id is not null group by 1""", (fid,)).fetchall())
            cats = conn.execute("""select c.node_id, c.path, cf.is_required from category_facets cf
                                     join categories c on c.node_id = cf.category_id
                                    where cf.facet_id = %s order by c.path""", (fid,)).fetchall()
            history = conn.execute("""select change_id, changed_at, changed_by, summary,
                                             before ->> 'version', after ->> 'version'
                                        from facet_changes where facet_id = %s
                                       order by changed_at desc, change_id desc limit 5""", (fid,)).fetchall()
            tagged = _tagged_with(conn, fid)
        finally:
            end(db)
    rule, unknown_policy, edited, updated, own, dflt, tv = extra
    for v in snap["values"]:
        v["uses"] = uses.get(v["id"], 0)
    return {
        **snap, "group": _group(snap["policy"], snap["status"]), "policyText": POLICY_TEXT.get(snap["policy"]),
        "rule": rule if snap["policy"] not in MODEL_POLICIES else None, "unknownPolicy": unknown_policy,
        "edited": bool(edited), "updatedAt": updated.isoformat() if updated else None,
        "level": float(own if own is not None else dflt), "levelIsDefault": own is None,
        "categories": [{"id": i, "path": p, "required": r} for i, p, r in cats],
        "history": [{"id": i, "at": at.isoformat(), "by": by, "summary": s, "versionBefore": vb, "versionAfter": va}
                    for i, at, by, s, vb, va in history],
        "taggedProducts": tagged, "taxonomyVersion": tv,
    }


def _tagged_with(conn, fid: str) -> int:
    """Products whose latest tagger answer covers this facet (tagged with the rules in force before a save)."""
    return conn.execute("""select count(distinct t.product_id) from product_facet_tags t
                             join catalog_products cp on cp.product_id = t.product_id and t.tagging_run_id = cp.ai_run_id
                            where t.facet_id = %s""", (fid,)).fetchone()[0]


# ------------------------------------------------------------------ save

def _validate(before: dict, body: dict) -> tuple[dict, list[dict], dict]:
    """The edited facet and values, or FacetError with a problem per field."""
    fields: dict[str, str] = {}
    # the ID, type and categories are read-only: refused even if a request sends them changed
    if "id" in body and body["id"] != before["id"]:
        fields["id"] = "The facet ID cannot be changed."
    for k in ("dataType", "cardinality"):
        if k in body and body[k] != before[k]:
            fields[k] = "The facet's type cannot be changed."
    if "categories" in body:
        fields["categories"] = "The categories a facet applies to cannot be changed here."
    model = before["policy"] in MODEL_POLICIES
    if not model:
        raise FacetError("This facet is set by a rule and is read-only.", 422)
    name = (body.get("name") or "").strip()
    definition = (body.get("definition") or "").strip()
    if not name:
        fields["name"] = "Give the facet a name."
    if not definition:
        fields["definition"] = "Give the facet a definition."
    policy = body.get("policy", before["policy"])
    if policy not in MODEL_POLICIES:
        fields["policy"] = "Choose one of the three evidence sources."
    on_missing = body.get("onMissing", before["onMissing"])
    if on_missing not in ("unknown", "review_if_required") and on_missing != before["onMissing"]:
        fields["onMissing"] = "Choose leave unknown or send to review."
    max_values = body.get("maxValues", before["maxValues"])
    if before["cardinality"] == "single":
        max_values = before["maxValues"]
    elif not isinstance(max_values, int) or max_values < 1:
        fields["maxValues"] = "The maximum number of values must be at least 1."
    required = bool(body.get("required", before["required"]))

    old = {v["id"]: v for v in before["values"]}
    vals, labels = [], {}
    for i, v in enumerate(body.get("values") or []):
        label = (v.get("label") or "").strip()
        vid = v.get("id")
        if vid is not None and vid not in old:
            fields[f"values.{i}"] = "Not a value of this facet."
            continue
        if not label:
            fields[f"values.{i}.label"] = "Give the value a label."
            continue
        active = bool(v.get("active", True))
        if active:
            key = label.lower()
            if key in labels:
                fields[f"values.{i}.label"] = f"Another active value is already called {label}."
            labels[key] = i
        vals.append({"id": vid, "label": label, "definition": (v.get("definition") or "").strip() or None,
                     "active": active})
    sent = {v["id"] for v in vals if v["id"] is not None}
    missing = [o for o in old if o not in sent]
    if missing:
        fields["values"] = "Values cannot be deleted; switch them to inactive instead."
    if before["dataType"] in ("enum", "ordinal", "boolean") and old and not any(v["active"] for v in vals):
        fields["values"] = "At least one value must stay active."
    if fields:
        raise FacetError("Some fields need attention.", 422, list(fields.values()), fields)
    facet = {"name": name, "definition": definition, "policy": policy, "onMissing": on_missing,
             "maxValues": max_values, "required": required}
    return facet, vals, old


def _summary(before: dict, facet: dict, vals: list[dict], old: dict) -> tuple[str, bool]:
    parts, rubric = [], False
    if facet["name"] != before["name"]:
        parts.append(f"name “{before['name']}” → “{facet['name']}”")
    if facet["definition"] != (before["definition"] or ""):
        parts.append("definition")
        rubric = True
    if facet["policy"] != before["policy"]:
        parts.append(f"evidence {POLICY_TEXT[before['policy']]} → {POLICY_TEXT[facet['policy']]}")
    if facet["onMissing"] != before["onMissing"]:
        parts.append("no-answer behaviour")
    if facet["maxValues"] != before["maxValues"]:
        parts.append(f"maximum values {before['maxValues']} → {facet['maxValues']}")
    if facet["required"] != before["required"]:
        parts.append("required" if facet["required"] else "not required")
    for v in vals:
        o = old.get(v["id"]) if v["id"] is not None else None
        if o is None:
            parts.append(f"added “{v['label']}”")
            rubric = rubric or bool(v["definition"])
            continue
        if v["label"] != o["label"]:
            parts.append(f"“{o['label']}” renamed “{v['label']}”")
        if v["definition"] != o["definition"]:
            parts.append(f"definition of “{v['label']}”")
            rubric = True
        if v["active"] != o["active"]:
            parts.append(f"“{v['label']}” {'active' if v['active'] else 'inactive'}")
    return "; ".join(parts), rubric


def _slug(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "value"
    s, n = base, 2
    while s in taken:
        s, n = f"{base}_{n}", n + 1
    return s


def save(fid: str, body: dict, who: str = "reviewer", summary_prefix: str = "") -> dict:
    """PUT /api/facets/{id}: validate, write the facet and its values and a facet_changes row in one
    transaction, raise taxonomy_version, and refuse if any stored tag would then break the catalog rules."""
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            before = _snapshot(conn, fid)
            if before is None:
                raise FacetError("That facet is not known.", 404)
            facet, vals, old = _validate(before, body)
            summary, rubric = _summary(before, facet, vals, old)
            if not summary:
                raise FacetError("Nothing changed.", 422)
            version = next_version(before["version"]) if rubric else before["version"]
            with conn.transaction():
                invalid_before = {r[0] for r in conn.execute(
                    "select tag_id from v_invalid_tags where facet_id = %s", (fid,))}
                conn.execute("""update facets set name = %s, definition = %s, source_policy = %s, on_missing = %s,
                                       max_values = %s, definition_version = %s, edited_in_app = true,
                                       updated_at = now()
                                 where facet_id = %s""",
                             (facet["name"], facet["definition"], facet["policy"], facet["onMissing"],
                              facet["maxValues"], version, fid))
                if facet["required"] != before["required"]:
                    conn.execute("update category_facets set is_required = %s where facet_id = %s",
                                 (facet["required"], fid))
                taken = {v["value"] for v in before["values"]}
                sort = max([v["sort"] or 0 for v in before["values"]] + [0])
                for v in vals:
                    if v["id"] is None:
                        sort += 1
                        slug = _slug(v["label"], taken)
                        taken.add(slug)
                        conn.execute("""insert into facet_values (facet_id, value, label, definition, definition_version,
                                                                  sort_order, is_active)
                                        values (%s, %s, %s, %s, %s, %s, %s)""",
                                     (fid, slug, v["label"], v["definition"], version if v["definition"] else None,
                                      sort, v["active"]))
                    else:
                        o = old[v["id"]]
                        vv = version if v["definition"] != o["definition"] else o["version"]
                        conn.execute("""update facet_values set label = %s, definition = %s, is_active = %s,
                                               definition_version = %s where facet_value_id = %s""",
                                     (v["label"], v["definition"], v["active"], vv, v["id"]))
                broken = conn.execute("""select rule, detail, count(*) from v_invalid_tags
                                          where facet_id = %s and tag_id <> all(%s) group by 1, 2""",
                                      (fid, list(invalid_before))).fetchall()
                if broken:
                    raise FacetError("This change would leave stored tags breaking the catalog rules, so nothing "
                                     "was saved.", 422, [f"{d} ({n} tags)" for _, d, n in broken])
                after = _snapshot(conn, fid)
                text = (summary_prefix + summary) + (f" · rubric {before['version'] or 'none'} → {version}"
                                                     if rubric else "")
                conn.execute("""insert into facet_changes (facet_id, changed_by, summary, before, after)
                                values (%s, %s, %s, %s, %s)""", (fid, who, text, Jsonb(before), Jsonb(after)))
                tv = conn.execute("update app_settings set taxonomy_version = taxonomy_version + 1 "
                                  "returning taxonomy_version").fetchone()[0]
                tagged = _tagged_with(conn, fid)
            commit(db)
        except Exception:
            end(db)
            raise
    return {"saved": True, "summary": text, "version": version, "taxonomyVersion": tv, "taggedProducts": tagged}


def restore(fid: str, change_id: int) -> dict:
    """Save a change's `before` as a new change (rules and values; values added since become inactive)."""
    row = read("select before from facet_changes where change_id = %s and facet_id = %s", (change_id, fid))
    if not row:
        raise FacetError("That change is not in this facet's history.", 404)
    b = row[0][0]
    current = facet_detail(fid)
    known = {v["id"] for v in b["values"]}
    values = [{"id": v["id"], "label": v["label"], "definition": v["definition"], "active": v["active"]}
              for v in b["values"]]
    values += [{"id": v["id"], "label": v["label"], "definition": v["definition"], "active": False}
               for v in current["values"] if v["id"] not in known]
    body = {"name": b["name"], "definition": b["definition"], "policy": b["policy"], "onMissing": b["onMissing"],
            "maxValues": b["maxValues"], "required": b["required"], "values": values}
    return save(fid, body, summary_prefix=f"restored change {change_id}: ")


# ------------------------------------------------------------------ test on a product

def _answer(conn, pid: str, run: int | None, fid: str) -> dict | None:
    if not run:
        return None
    rows = conn.execute("""select fv.label, t.value_number, t.value_text, t.status, t.confidence, t.evidence,
                                  t.reason, t.evidence_quote, t.review_reason
                             from product_facet_tags t left join facet_values fv using (facet_value_id)
                            where t.product_id = %s and t.tagging_run_id = %s and t.facet_id = %s
                            order by t.confidence desc nulls last""", (pid, run, fid)).fetchall()
    return {"run": run, "rows": [{"value": r[0] if r[0] is not None else (str(r[1]) if r[1] is not None else r[2]),
                                  "status": r[3], "confidence": float(r[4]) if r[4] is not None else None,
                                  "evidence": r[5], "reason": r[6], "quote": r[7], "reviewReason": r[8]}
                                 for r in rows]}


def test_on_product(fid: str, target: str) -> dict:
    """POST /api/facets/{id}/test: tag one product with the saved rules (fetching and categorising it first
    when it is new) and return a job; the finished job holds this facet's answer before and after."""
    from scraper.live import classify

    from . import pipeline
    from .live import _categorised, _client, _known, _store
    target = (target or "").strip()
    if not target:
        raise FacetError("Pick a product or paste an Ounass product link.", 422)
    c = classify(target)
    if c["kind"] == "product":
        pid, url = c["productId"], target
    elif c["kind"] == "listing":
        raise FacetError("That is a listing page; paste one product's link.", 422)
    else:
        pid, url = target, None
        if not _known(pid):
            raise FacetError("That is not a known product ID or an Ounass product link.", 422)
    if not read("select 1 from facets where facet_id = %s and status = 'active'", (fid,)):
        raise FacetError("That facet is not active.", 422)
    if _known(pid) and _categorised(pid) and not read(
            """select 1 from catalog_products cp join category_facets cf on cf.category_id = cp.category_id
                where cp.product_id = %s and cf.facet_id = %s""", (pid, fid)):
        raise FacetError("This facet does not apply to that product's category.", 422)
    before_run = (read("select ai_run_id from catalog_products where product_id = %s", (pid,)) or [[None]])[0][0]

    def work(job: jobs.Job):
        if not _known(pid):
            job.begin("Fetching product")
            client, store = _client(), _store()
            try:
                from scraper.live import scrape_one
                scrape_one(client, store, url)
            finally:
                client.close()
                store.close()
            job.end("Fetching product")
        if not _categorised(pid):
            job.begin("Categorising")
            cat = pipeline.categorise(pid, job)
            job.end("Categorising")
            if cat["status"] == "review":
                raise RuntimeError("Its category needs a person first: approve it on the product's page, then test.")
        if not read("""select 1 from catalog_products cp join category_facets cf on cf.category_id = cp.category_id
                        where cp.product_id = %s and cf.facet_id = %s""", (pid, fid)):
            raise RuntimeError("This facet does not apply to that product's category.")
        job.begin("Tagging")
        out = pipeline.tag(pid, job)
        job.end("Tagging")
        db = get_db()
        with db.lock:
            try:
                b = _answer(db.conn, pid, before_run, fid)
                a = _answer(db.conn, pid, out["runId"], fid)
                img = db.conn.execute("""select storage_path from product_images where product_id = %s
                                          and position = 1""", (pid,)).fetchone()
                name = db.conn.execute("select name, brand from products where product_id = %s", (pid,)).fetchone()
            finally:
                end(db)
        from .photos import sign
        job.result = {"productId": pid, "facetId": fid, "before": b, "after": a,
                      "name": name[0] if name else None, "brand": name[1] if name else None,
                      "photo": sign([img[0]]).get(img[0]) if img and img[0] else None}
    steps = (["Fetching product"] if not _known(pid) else []) + \
            (["Categorising"] if not _categorised(pid) else []) + ["Tagging"]
    try:
        job = jobs.start("facet-test", work, pid, steps=steps)
    except jobs.Busy as e:
        raise FacetError(str(e), 409)
    return {"jobId": job.id, "productId": pid, "startedAt": time.time()}


def product_search(q: str) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    return [{"id": p, "name": n, "brand": b} for p, n, b in read(
        """select p.product_id, p.name, p.brand from products p
            where p.product_id ilike %(l)s or p.name ilike %(l)s or p.brand ilike %(l)s
            order by p.name limit 12""", {"l": f"%{q}%"})]
