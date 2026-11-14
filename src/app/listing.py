"""Products and Approved tabs: rows, counts, and the data for their filters (Build PRD 4 and 6).

Everything is read from v_product_review_state, so the Ready / Needs review rule lives in one place.
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import read
from .photos import sign

PAGE = 50
MODEL_POLICIES = ("text_only", "text_first", "judge")


@dataclass
class Filters:
    tab: str = "products"          # products | approved
    state: str = ""                # "" | ready | review
    q: str = ""
    category: str = ""             # a node at any level; includes everything beneath it
    facet: str = ""
    value: str = ""                # a facet_value_id, or "has" / "unknown"
    flagged: bool = False          # facet needs review
    ids: str = ""                  # comma-separated product IDs (Live tag "Open in Products")
    sort: str = "review"           # review | brand
    page: int = 1


def _where(f: Filters, with_state: bool = True) -> tuple[str, list]:
    """SQL conditions on v_product_review_state `s` for the filters (state optional, for the counts)."""
    w, p = [], []
    w.append("s.state = 'approved'" if f.tab == "approved" else "s.state <> 'approved'")
    if with_state and f.tab != "approved" and f.state in ("ready", "review"):
        w.append("s.state = %s")
        p.append("ready" if f.state == "ready" else "needs_review")
    if f.q.strip():
        w.append("(s.name ilike %s or s.brand ilike %s or s.product_id ilike %s)")
        like = f"%{f.q.strip()}%"
        p += [like, like, like]
    if f.category:
        w.append("""exists (select 1 from categories n where n.node_id = %s
                            and (s.category_path = n.path or s.category_path like n.path || ' / %%'))""")
        p.append(f.category)
    if f.ids:
        w.append("s.product_id = any(%s)")
        p.append([x for x in f.ids.split(",") if x])
    if f.facet:
        # the run whose answers the reviewer is looking at: the review run once approved, else the tagger's
        run = "case when s.is_published then s.current_run_id else s.ai_run_id end"
        if f.flagged:
            w.append("%s = any(s.flagged_facets)")
            p.append(f.facet)
        elif f.value == "has":
            w.append(f"""exists (select 1 from product_facet_tags t where t.product_id = s.product_id
                         and t.tagging_run_id = {run} and t.facet_id = %s
                         and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0)""")
            p.append(f.facet)
        elif f.value == "unknown":
            w.append(f"""not exists (select 1 from product_facet_tags t where t.product_id = s.product_id
                         and t.tagging_run_id = {run} and t.facet_id = %s
                         and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0)""")
            p.append(f.facet)
        elif f.value.isdigit():
            w.append(f"""exists (select 1 from product_facet_tags t where t.product_id = s.product_id
                         and t.tagging_run_id = {run} and t.facet_id = %s and t.facet_value_id = %s)""")
            p += [f.facet, int(f.value)]
    return " and ".join(w), p


def counts(f: Filters) -> dict:
    where, p = _where(Filters(**{**f.__dict__, "tab": "products"}), with_state=False)
    ready, review = read(f"""select count(*) filter (where s.state = 'ready'),
                                    count(*) filter (where s.state = 'needs_review')
                             from v_product_review_state s where {where}""", p)[0]
    where_a, pa = _where(Filters(**{**f.__dict__, "tab": "approved"}), with_state=False)
    approved = read(f"select count(*) from v_product_review_state s where {where_a}", pa)[0][0]
    return {"ready": ready, "needsReview": review, "approved": approved}


def rows(f: Filters) -> dict:
    where, p = _where(f)
    order = ("s.brand, s.name" if f.sort == "brand" else
             "(s.state = 'ready'), s.lowest_confidence nulls first, s.name")
    total = read(f"select count(*) from v_product_review_state s where {where}", p)[0][0]
    data = read(f"""
        select s.product_id, s.name, s.brand, s.category_path, s.category_flagged, s.state, s.n_facets,
               s.n_flagged, s.flagged_facets, s.lowest_confidence, low.lvl, s.approved_at, s.url,
               s.primary_image_path, s.n_resolved
          from v_product_review_state s
          left join lateral (
                select coalesce(f.review_threshold, (select default_review_threshold from app_settings)) as lvl
                  from product_facet_tags t join facets f using (facet_id)
                 where t.product_id = s.product_id and t.tagging_run_id = s.ai_run_id
                   and t.evidence is distinct from 'rule' and t.confidence is not null
                   and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0
                 order by t.confidence limit 1) low on true
         where {where}
         order by {order}
         limit %s offset %s""", p + [PAGE, (max(f.page, 1) - 1) * PAGE])
    names = dict(read("select facet_id, name from facets"))
    photos = sign([r[13] for r in data if r[13]])
    resolved = {r[0]: r[14] for r in data}
    out = []
    for (pid, name, brand, path, cat_flag, state, n_facets, n_flagged, flagged, low, low_lvl, approved_at, url,
         img, _res) in data:
        out.append({
            "id": pid, "title": name, "brand": brand,
            "categoryShort": " › ".join((path or "").split(" / ")[-2:]),
            "categoryFlagged": bool(cat_flag), "state": state,
            "nFacets": n_facets, "nFlagged": n_flagged, "nResolved": resolved.get(pid, 0),
            "flaggedNames": [names.get(x, x) for x in flagged],
            "lowestConfidence": float(low) if low is not None else None,
            "lowestIsLow": bool(low is not None and low_lvl is not None and low < low_lvl),
            "approvedAt": approved_at.isoformat() if approved_at else None,
            "url": url, "thumb": photos.get(img) if img else None,
        })
    return {"rows": out, "total": total, "page": max(f.page, 1), "pageSize": PAGE}


def summary(f: Filters) -> dict:
    c = counts(f)
    tagged = read("select count(*) from catalog_products where ai_run_id is not null and source <> 'sample'")[0][0]
    scraped = read("select count(*) from products p where not exists (select 1 from catalog_products c "
                   "where c.product_id = p.product_id and c.source = 'sample')")[0][0]
    st = read("""select default_review_threshold, category_review_threshold, categorise_model, tag_model
                 from app_settings""")[0]
    run = read("""select max(tagging_run_id) from tagging_runs where stage = 'tag' and not is_drill
                    and purpose = 'tagger'""")[0][0]
    return {**c, "tagged": tagged, "scraped": scraped, "defaultLevel": float(st[0]),
            "categoryLevel": float(st[1]), "categoriseModel": st[2], "tagModel": st[3], "latestRunId": run}


def facet_options() -> list[dict]:
    rows_ = read(f"""select f.facet_id, f.name, f.data_type, v.facet_value_id, v.label
                     from facets f left join facet_values v on v.facet_id = f.facet_id and v.is_active
                     where f.status = 'active' and f.source_policy = any(%s)
                     order by f.sort_order nulls last, f.name, v.sort_order nulls last""", [list(MODEL_POLICIES)])
    out: dict[str, dict] = {}
    for fid, name, dtype, vid, label in rows_:
        f = out.setdefault(fid, {"id": fid, "name": name, "type": dtype, "values": []})
        if vid is not None:
            f["values"].append({"id": vid, "label": label})
    return list(out.values())


def category_tree() -> list[dict]:
    """Every category node that has categorised products in or beneath it, with that count."""
    rows_ = read("""
        select n.node_id, n.path, n.level, count(cp.product_id)
          from categories n
          join catalog_products cp on true
          join categories c on c.node_id = cp.category_id
         where c.path = n.path or c.path like n.path || ' / %%'
         group by n.node_id, n.path, n.level
         order by n.path""")
    return [{"id": i, "path": p, "name": p.split(" / ")[-1], "depth": p.count(" / "), "count": n}
            for i, p, _lvl, n in rows_]
