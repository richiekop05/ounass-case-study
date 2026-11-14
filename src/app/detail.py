"""Everything the detail page needs for one product (Build PRD 6, `GET /api/products/{id}`).

The tagger's answers come from the product's latest tagger run (`ai_run_id`); flags come from v_tag_flags,
the same definition the list uses. Saved reviewer decisions are added in step 3.
"""
from __future__ import annotations

import re

from tagger.facets import MODEL_POLICIES
from tagger.products import load_product

from .db import commit, end, get_db
from .photos import sign


class NotFound(LookupError):
    pass


def _plain_reason(reason: str | None) -> str | None:
    """The reason as a reader should see it: no internal prefixes ("derive:facet:") or check notes."""
    if not reason:
        return None
    r = re.sub(r"^derive:[a-z_]+:\s*", "", reason)
    r = re.sub(r";?\s*quote not found in text", "", r).strip(" ;")
    return r[:1].upper() + r[1:] if r else None


def product_detail(product_id: str) -> dict:
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            d = _detail(conn, product_id)
            if not d["isPublished"]:   # Build PRD 11: review time starts the first time the page is opened
                conn.execute("""update catalog_products set review_opened_at = now()
                                where product_id = %s and review_opened_at is null""", (product_id,))
                commit(db)
        finally:
            end(db)
    from .review import saved_decisions
    d["saved"] = saved_decisions(product_id)
    return d


def _detail(conn, product_id: str) -> dict:
    st = conn.execute("""select product_id, name, brand, url, state, is_published, approved_at, category_id,
                                category_path, category_confidence, category_status, category_runner_up_id,
                                category_flagged, ai_run_id, current_run_id, n_flagged
                           from v_product_review_state where product_id = %s""", (product_id,)).fetchone()
    if not st:
        raise NotFound(f"Product {product_id} is not categorised yet, so it has no detail page.")
    (pid, name, brand, url, state, published, approved_at, cat_id, cat_path, cat_conf, cat_status, ru,
     cat_flag, ai_run, cur_run, n_flagged) = st
    product = load_product(conn, pid)
    photos = sign(product.image_paths)
    ru_path = conn.execute("select path from categories where node_id = %s", (ru,)).fetchone() if ru else None
    cat_level = float(conn.execute("select category_review_threshold from app_settings").fetchone()[0])

    facets = conn.execute("""
        select f.facet_id, f.name, f.definition, f.data_type, f.cardinality, f.max_values, f.unit,
               f.source_policy, cf.is_required and f.on_missing = 'review_if_required',
               coalesce(f.review_threshold, (select default_review_threshold from app_settings)) as lvl
          from category_facets cf join facets f on f.facet_id = cf.facet_id
         where cf.category_id = %s and f.status = 'active' and f.source_policy <> 'not_tagged'
         order by f.sort_order nulls last, f.facet_id""", (cat_id,)).fetchall()
    ids = [r[0] for r in facets]
    values: dict[str, list[dict]] = {}
    for fid, vid, label, definition, exclusive in conn.execute(
            """select facet_id, facet_value_id, label, definition, is_exclusive from facet_values
               where facet_id = any(%s) and is_active order by facet_id, sort_order nulls last""", (ids,)):
        values.setdefault(fid, []).append({"id": vid, "label": label, "definition": definition,
                                           "exclusive": bool(exclusive)})
    rows: dict[str, list] = {}
    if ai_run:
        for r in conn.execute(
                """select t.facet_id, t.status, t.facet_value_id, fv.label, t.value_number, t.value_text,
                          t.confidence, t.evidence, t.evidence_quote, t.reason, t.review_reason
                     from product_facet_tags t left join facet_values fv using (facet_value_id)
                    where t.product_id = %s and t.tagging_run_id = %s
                    order by t.facet_id, fv.sort_order nulls last""", (pid, ai_run)):
            rows.setdefault(r[0], []).append(r)
    flags = {fid: (bool(fl), reason) for fid, fl, reason in conn.execute(
        """select facet_id, flagged, flag_reason from v_tag_flags where product_id = %s and tagging_run_id = %s""",
        (pid, ai_run))} if ai_run else {}

    out = []
    for fid, fname, fdef, dtype, card, maxv, unit, policy, req, lvl in facets:
        rs = rows.get(fid, [])
        vals = []
        for r in rs:
            if r[2] is not None:
                vals.append({"id": r[2], "label": r[3]})
            elif r[4] is not None:
                vals.append({"number": float(r[4])})
            elif r[5] is not None:
                vals.append({"text": r[5]})
        statuses = {r[1] for r in rs}
        first = rs[0] if rs else None
        answer = None
        if rs:
            state_ = ("value" if vals else
                      "not_applicable" if "not_applicable" in statuses else "unknown")
            confs = [float(r[6]) for r in rs if r[6] is not None]
            answer = {"state": state_, "values": vals,
                      "status": "review" if "review" in statuses else ("auto" if "auto" in statuses else first[1]),
                      "confidence": max(confs) if confs else None,
                      "evidence": first[7], "quote": first[8] or None, "reason": _plain_reason(first[9]),
                      "reviewReason": next((r[10] for r in rs if r[10]), None)}
        fl = flags.get(fid, (False, None))
        out.append({
            "id": fid, "name": fname, "definition": fdef, "type": dtype,
            "multi": card != "single" and (maxv or 2) > 1, "maxValues": maxv, "unit": unit,
            "rule": policy == "derived", "policy": policy if policy in MODEL_POLICIES else "rule",
            "required": bool(req), "level": float(lvl) if lvl is not None and policy != "derived" else None,
            "values": values.get(fid, []), "answer": answer,
            "flagged": fl[0], "flagReason": fl[1],
            "quoteFound": not (answer and answer["reviewReason"] == "quote_not_found"),
        })
    return {
        "id": pid, "title": name, "brand": brand, "url": url, "state": state,
        "isPublished": bool(published), "approvedAt": approved_at.isoformat() if approved_at else None,
        "aiRunId": ai_run, "currentRunId": cur_run, "nFlagged": n_flagged,
        "text": [{"label": label, "text": text} for label, text in product.parts()],
        "photos": [{"n": i + 1, "url": photos.get(p)} for i, p in enumerate(product.image_paths)],
        "category": {
            "id": cat_id, "path": (cat_path or "").split(" / "),
            "confidence": float(cat_conf) if cat_conf is not None else None, "level": cat_level,
            "status": cat_status, "flagged": bool(cat_flag),
            "runnerUp": {"id": ru, "path": ru_path[0].split(" / ")} if ru_path else None,
        },
        "facets": out,
        "saved": None,          # filled by product_detail from the review run
    }


def category_search(q: str) -> list[dict]:
    """Categories a product can sit on, for the Change box (holding nodes excluded)."""
    from tagger.products import ASSIGNABLE_SQL
    db = get_db()
    with db.lock:
        try:
            rows = db.conn.execute(
                f"""select c.node_id, c.path from ({ASSIGNABLE_SQL}) a join categories c on c.node_id = a.node_id
                    where c.path ilike %s order by c.path limit 30""", (f"%{q.strip()}%",)).fetchall()
        finally:
            end(db)
    return [{"id": i, "path": p.split(" / ")} for i, p in rows]
