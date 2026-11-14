"""Confidence and Models tabs (Design PRD 8 and 10, Build PRD 3 and 6).

Confidence: one query per request works out, for every facet and the category step, the figures at the
current levels and at any proposed ones (the preview), over every tagged product at its latest tagger answer.
Applying writes only `facets.review_threshold` and `app_settings`; it never touches a tag or an approved
product. The flag rule is v_tag_flags' rule: flagged for a reason other than confidence, or a value (not the
facet's default) whose confidence is below the level.

Lowering the category level (decided 14 Nov 2026): held products whose category now clears it are accepted
as the categoriser would have accepted them at that level, and tagged at once in a background job.

Models: the profiles in config/models.toml (never edited), the two choices in `app_settings`.
"""
from __future__ import annotations

import json
from decimal import Decimal

from tagger.config import load_settings

from . import jobs
from .db import ROOT, commit, end, get_db, read

MODEL_POLICIES = ("text_only", "text_first", "judge")


class SettingsError(Exception):
    def __init__(self, message: str, status: int = 400, problems: list[str] | None = None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or []


def _level(v, what: str) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        raise SettingsError(f"{what}: the level must be a number from 0 to 1.", 422)
    if not Decimal(0) <= d <= Decimal(1):
        raise SettingsError(f"{what}: the level must be from 0.00 to 1.00.", 422)
    return d


# ------------------------------------------------------------------ confidence figures (one query)

FIGURES_SQL = """
with st as (
    select default_review_threshold as dold, coalesce(%(d)s::numeric, default_review_threshold) as dnew,
           category_review_threshold as cold, coalesce(%(c)s::numeric, category_review_threshold) as cnew
      from app_settings),
lv as (
    select f.facet_id, f.name, f.definition, f.sort_order, f.source_policy in ('text_only', 'text_first', 'judge') as model,
           f.review_threshold as own_old,
           case when %(L)s::jsonb ? f.facet_id then (%(L)s::jsonb ->> f.facet_id)::numeric
                else f.review_threshold end as own_new,
           exists (select 1 from category_facets cf where cf.facet_id = f.facet_id and cf.is_required)
             and f.on_missing = 'review_if_required' as required
      from facets f where f.status = 'active' and f.source_policy <> 'not_tagged'),
lvl as (select lv.*, coalesce(own_old, st.dold) as lold, coalesce(own_new, st.dnew) as lnew from lv, st),
scope as (
    select cp.product_id, cp.ai_run_id, cp.category_id, cp.category_confidence, cp.category_status,
           cp.category_reviewed_at, cp.is_published
      from catalog_products cp join categories c on c.node_id = cp.category_id
     where cp.source <> 'sample' and (%(cat)s::text = '' or exists (select 1 from categories n where n.node_id = %(cat)s::text
                                          and (c.path = n.path or c.path like n.path || ' / %%')))),
app as (   -- every tagged product and each facet that applies to its category
    select s.product_id, s.ai_run_id, s.is_published, cf.facet_id
      from scope s join category_facets cf on cf.category_id = s.category_id
      join lvl on lvl.facet_id = cf.facet_id
     where s.ai_run_id is not null),
r as (
    select t.product_id, t.facet_id, t.status, t.confidence, t.review_reason, t.evidence,
           num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0 as has_value,
           (f.default_value is not null and fv.facet_value_id is not null
            and f.default_value in (fv.value, fv.label)) as is_default
      from app a join product_facet_tags t on t.product_id = a.product_id and t.tagging_run_id = a.ai_run_id
                                          and t.facet_id = a.facet_id
      join facets f on f.facet_id = t.facet_id
      left join facet_values fv on fv.facet_value_id = t.facet_value_id),
pf as (    -- the tagger's answer per product and facet
    select a.product_id, a.facet_id, a.is_published,
           coalesce(bool_or(r.has_value), false) as has_value,
           min(r.confidence) filter (where r.has_value and not r.is_default and r.confidence is not null
                                       and r.evidence is distinct from 'rule') as mconf,
           coalesce(bool_or(r.status = 'review' and coalesce(r.review_reason, 'low_confidence') <> 'low_confidence'
                            and r.evidence is distinct from 'rule'), false) as other,
           sum(r.confidence) filter (where r.has_value and r.confidence is not null) as csum,
           count(r.confidence) filter (where r.has_value and r.confidence is not null) as cn
      from app a left join r on r.product_id = a.product_id and r.facet_id = a.facet_id
     group by a.product_id, a.facet_id, a.is_published),
pfl as (
    select pf.*, lvl.model, lvl.lold, lvl.lnew,
           lvl.model and (pf.other or (pf.mconf is not null and pf.mconf < lvl.lold)) as flag_old,
           lvl.model and (pf.other or (pf.mconf is not null and pf.mconf < lvl.lnew)) as flag_new,
           pf.has_value and (pf.mconf is null or pf.mconf >= lvl.lold) as conf_old,
           pf.has_value and (pf.mconf is null or pf.mconf >= lvl.lnew) as conf_new
      from pf join lvl using (facet_id)),
rr as (    -- a product's latest review run while it is not approved: saved decisions resolve flags (019)
    select distinct on (r.product_id) r.product_id, r.tagging_run_id
      from tagging_runs r join scope s on s.product_id = r.product_id and not s.is_published
     where r.stage = 'review' order by r.product_id, r.tagging_run_id desc),
decided as (
    select distinct t.product_id, t.facet_id
      from rr join product_facet_tags t on t.tagging_run_id = rr.tagging_run_id and t.product_id = rr.product_id
     where t.review_outcome is distinct from 'not_reviewed'),
prod as (  -- product state now and with the proposed levels (unapproved products only)
    select s.product_id, s.ai_run_id is null as untagged,
           (s.category_reviewed_at is null and (s.category_status = 'review' or s.category_confidence < st.cold)) as cat_old,
           (s.category_reviewed_at is null and (s.category_status = 'review' or s.category_confidence < st.cnew)) as cat_new,
           (s.category_reviewed_at is null and s.category_status = 'review' and s.ai_run_id is null
            and s.category_confidence >= st.cnew) as held_clears,
           exists (select 1 from pfl left join decided d using (product_id, facet_id)
                    where pfl.product_id = s.product_id and pfl.flag_old and d.facet_id is null) as tags_old,
           exists (select 1 from pfl left join decided d using (product_id, facet_id)
                    where pfl.product_id = s.product_id and pfl.flag_new and d.facet_id is null) as tags_new
      from scope s, st where not s.is_published),
moves as (
    select count(*) filter (where (untagged or cat_old or tags_old) and not (untagged or cat_new or tags_new)) as to_ready,
           count(*) filter (where not (untagged or cat_old or tags_old) and (untagged or cat_new or tags_new)) as to_review,
           count(*) filter (where untagged or cat_old or tags_old) as needs_old,
           count(*) filter (where not (untagged or cat_old or tags_old)) as ready_old,
           count(*) filter (where held_clears) as held_to_tag
      from prod),
oc as (    -- review outcomes of approved products in scope
    select o.facet_id,
           count(*) filter (where o.was_flagged and o.outcome <> 'not_reviewed') as reviewed,
           count(*) filter (where o.was_flagged and o.outcome = 'approved_unchanged') as unchanged,
           count(*) filter (where o.was_flagged and o.outcome in ('changed', 'set_unknown')) as changed,
           count(*) filter (where not o.was_flagged and o.outcome in ('changed', 'set_unknown')) as changed_confident
      from v_review_outcome_rows o join scope s on s.product_id = o.product_id
     group by o.facet_id),
facet_rows as (
    select lvl.facet_id, lvl.name, lvl.definition, lvl.sort_order, lvl.model, lvl.required,
           lvl.own_old is null as default_old, lvl.own_new is null as default_new, lvl.lold, lvl.lnew,
           count(pfl.product_id) as products,
           count(*) filter (where pfl.conf_old) as conf_old, count(*) filter (where pfl.conf_new) as conf_new,
           count(*) filter (where pfl.product_id is not null and not pfl.has_value) as unknown,
           sum(pfl.csum) as csum, sum(pfl.cn) as cn,
           count(*) filter (where pfl.flag_old and not pfl.is_published
                              and not exists (select 1 from decided d where d.product_id = pfl.product_id
                                                and d.facet_id = pfl.facet_id)) as needs_review
      from lvl left join pfl using (facet_id)
     group by lvl.facet_id, lvl.name, lvl.definition, lvl.sort_order, lvl.model, lvl.required, lvl.own_old,
              lvl.own_new, lvl.lold, lvl.lnew),
cat as (
    select count(*) as products, count(*) filter (where s.category_confidence >= st.cold) as conf_old,
           count(*) filter (where s.category_confidence >= st.cnew) as conf_new,
           avg(s.category_confidence) as mean,
           count(*) filter (where not s.is_published and s.category_reviewed_at is null
                              and (s.category_status = 'review' or s.category_confidence < st.cold)) as needs_review
      from scope s, st)
select json_build_object(
    'default', json_build_object('old', (select dold from st), 'new', (select dnew from st)),
    'category', (select json_build_object('old', st.cold, 'new', st.cnew, 'products', cat.products,
                     'confOld', cat.conf_old, 'confNew', cat.conf_new, 'mean', cat.mean,
                     'needsReview', cat.needs_review) from cat, st),
    'facets', (select coalesce(json_agg(json_build_object(
                     'id', f.facet_id, 'name', f.name, 'definition', f.definition, 'model', f.model,
                     'required', f.required, 'defaultOld', f.default_old, 'defaultNew', f.default_new,
                     'old', f.lold, 'new', f.lnew, 'products', f.products, 'confOld', f.conf_old,
                     'confNew', f.conf_new, 'unknown', f.unknown,
                     'mean', case when f.cn > 0 then f.csum / f.cn end, 'needsReview', f.needs_review)
                     order by f.sort_order nulls last, f.facet_id), '[]') from facet_rows f),
    'moves', (select row_to_json(moves) from moves),
    'outcomes', (select coalesce(json_object_agg(facet_id, json_build_object(
                     'reviewed', reviewed, 'unchanged', unchanged, 'changed', changed,
                     'changedConfident', changed_confident)), '{}') from oc),
    'tagCost', (select avg(r.cost_usd / nullif(r.products_succeeded, 0)) from tagging_runs r, app_settings a
                 where r.stage = 'tag' and not r.is_drill and r.purpose = 'tagger' and r.products_succeeded > 0
                   and r.model_profile = a.tag_model))
"""


def _figures(category: str = "", levels: dict | None = None, default=None, category_level=None) -> dict:
    return read(FIGURES_SQL, {"cat": category or "", "L": json.dumps(levels or {}), "d": default,
                              "c": category_level})[0][0]


def _share(n, d):
    return {"value": n / d if d else None, "num": n, "den": d}


def _row(f: dict, oc: dict, side: str) -> dict:
    o = oc.get(f["id"]) or {}
    conf = f["confOld"] if side == "old" else f["confNew"]
    return {
        "id": f["id"], "name": f["name"], "definition": f["definition"], "rule": not f["model"],
        "required": f["required"], "level": None if not f["model"] else float(f[side]),
        "isDefault": f["defaultOld"] if side == "old" else f["defaultNew"],
        "confident": _share(conf, f["products"]) if f["model"] else None,
        "meanConfidence": float(f["mean"]) if f["mean"] is not None else None,
        "unknown": _share(f["unknown"], f["products"]),
        "needsReview": f["needsReview"] if f["model"] else None,
        "reviewed": {"reviewed": o.get("reviewed", 0), "unchanged": o.get("unchanged", 0),
                     "changed": o.get("changed", 0), "changedConfident": o.get("changedConfident", 0)},
    }


def confidence(category: str = "") -> dict:
    """GET /api/confidence: one row per facet plus Category, at the levels in force."""
    fig = _figures(category)
    oc = fig["outcomes"]
    rows = [_row(f, oc, "old") for f in fig["facets"]]
    rows.sort(key=lambda r: (r["rule"], r["confident"]["value"] if r["confident"] and r["confident"]["value"]
                             is not None else 2, r["name"]))
    c, o = fig["category"], oc.get("category") or {}
    tot = {k: sum((v or {}).get(k, 0) for v in oc.values()) for k in ("reviewed", "unchanged", "changed",
                                                                      "changedConfident")}
    return {
        "default": float(fig["default"]["old"]),
        "category": {"level": float(c["old"]), "confident": _share(c["confOld"], c["products"]),
                     "meanConfidence": float(c["mean"]) if c["mean"] is not None else None,
                     "needsReview": c["needsReview"],
                     "reviewed": {k: o.get(k, 0) for k in ("reviewed", "unchanged", "changed", "changedConfident")}},
        "facets": rows, "outcomes": tot,
        "products": {"ready": fig["moves"]["ready_old"], "needsReview": fig["moves"]["needs_old"]},
    }


def _validate(body: dict) -> tuple[dict, Decimal | None, Decimal | None]:
    levels_in = body.get("levels") or {}
    if not isinstance(levels_in, dict):
        raise SettingsError("levels must be an object of facet: level.", 422)
    known = {f: m for f, m in read("""select facet_id, source_policy in ('text_only', 'text_first', 'judge')
                                        from facets where status = 'active'""")}
    problems, levels = [], {}
    for fid, v in levels_in.items():
        if fid not in known:
            problems.append(f"{fid}: not an active facet")
        elif not known[fid]:
            problems.append(f"{fid}: is written by a rule and has no confidence level")
        else:
            try:
                d = _level(v, fid)
            except SettingsError as e:
                problems.append(e.message)
                continue
            levels[fid] = None if d is None else str(d)          # None: follow the default
    if problems:
        raise SettingsError("Some levels could not be used.", 422, problems)
    default = _level(body.get("default"), "Default level")
    cat = _level(body.get("category"), "Category level")
    return levels, default, cat


def preview(body: dict, category: str = "") -> dict:
    """POST /api/confidence/preview: the figures with the proposed levels. Writes nothing."""
    levels, default, cat = _validate(body)
    fig = _figures(category, levels, default, cat)
    oc = fig["outcomes"]
    changed_ids = set(levels) | ({f["id"] for f in fig["facets"] if f["defaultNew"]} if default is not None else set())
    rows = []
    for f in fig["facets"]:
        if f["id"] in changed_ids and f["model"]:
            a, b = _row(f, oc, "old"), _row(f, oc, "new")
            rows.append({"id": f["id"], "name": f["name"], "from": a["level"], "to": b["level"],
                         "confidentFrom": a["confident"], "confidentTo": b["confident"]})
    c = fig["category"]
    held = fig["moves"]["held_to_tag"]
    per = float(fig["tagCost"]) if fig["tagCost"] is not None else 0.05
    return {
        "facets": rows,
        "category": None if cat is None else {"from": float(c["old"]), "to": float(c["new"]),
                                              "confidentFrom": _share(c["confOld"], c["products"]),
                                              "confidentTo": _share(c["confNew"], c["products"])},
        "toReady": fig["moves"]["to_ready"], "toReview": fig["moves"]["to_review"],
        "heldToTag": held, "tagCostUsd": round(held * per, 2),
    }


# ------------------------------------------------------------------ apply

def apply(body: dict) -> dict:
    """PUT /api/confidence: the levels in one transaction. Returns the previous values (for Undo) and, when
    the category level came down, the job tagging the held products that now clear it."""
    levels, default, cat = _validate(body)
    if not levels and default is None and cat is None:
        raise SettingsError("Nothing to apply.", 422)
    db = get_db()
    held: list[str] = []
    with db.lock:
        conn = db.conn
        try:
            prev_levels = {f: (float(v) if v is not None else None) for f, v in conn.execute(
                "select facet_id, review_threshold from facets where facet_id = any(%s)", (list(levels),))}
            pd, pc = conn.execute("select default_review_threshold, category_review_threshold "
                                  "from app_settings").fetchone()
            if cat is not None:
                held = [r[0] for r in conn.execute(
                    """select product_id from catalog_products
                        where category_status = 'review' and category_reviewed_at is null and ai_run_id is null
                          and source <> 'sample'
                          and category_confidence >= %s order by product_id""", (cat,))]
                if held and jobs.busy():
                    raise SettingsError("Another job is still running, and this level would start tagging "
                                        f"{len(held)} held products. Wait for it to finish, then apply.", 409)
            with conn.transaction():
                for fid, v in levels.items():
                    conn.execute("update facets set review_threshold = %s where facet_id = %s", (v, fid))
                if default is not None:
                    conn.execute("update app_settings set default_review_threshold = %s", (default,))
                if cat is not None:
                    conn.execute("update app_settings set category_review_threshold = %s", (cat,))
                if held:          # accepted as the categoriser accepts a category above the level
                    conn.execute("update catalog_products set category_status = 'auto' where product_id = any(%s)",
                                 (held,))
            commit(db)
        except Exception:
            end(db)
            raise
    job_id = _tag_held(held) if held else None
    return {"previous": {"levels": prev_levels, "default": float(pd), "category": float(pc)},
            "heldTagged": len(held), "jobId": job_id}


def _tag_held(pids: list[str]) -> int | None:
    from . import pipeline

    def work(job: jobs.Job):
        job.items = [{"position": i + 1, "productId": p, "step": "waiting", "error": None}
                     for i, p in enumerate(pids)]
        job.begin("Tagging")
        for it in job.items:
            if job.stop:
                it["step"] = "cancelled"
                continue
            it["step"] = "tagging"
            try:
                pipeline.tag(it["productId"], job)
                it["step"] = "done"
            except Exception as e:  # noqa: BLE001 - one product failing never stops the others
                it.update(step="failed", error=str(e) or type(e).__name__)
        job.end("Tagging", f"{sum(i['step'] == 'done' for i in job.items)} of {len(pids)} tagged")
    try:
        return jobs.start("retag-held", work, None, steps=["Tagging"]).id
    except jobs.Busy:
        return None


# ------------------------------------------------------------------ models

def models() -> dict:
    """GET /api/models: every profile (no key, no address), the two selected, and results so far per step."""
    s = load_settings(ROOT / "config")
    cm, tm = read("select categorise_model, tag_model from app_settings")[0]
    results = {}
    for stage, prof, n, cost, secs, acc, acc_n in read("""
        with r as (
            select r.stage, r.model_profile, r.products_succeeded as n, r.cost_usd,
                   extract(epoch from r.finished_at - r.started_at) as secs, r.tagging_run_id
              from tagging_runs r
             where r.stage in ('categorise', 'tag') and not r.is_drill and r.purpose = 'tagger'
               and r.products_succeeded > 0
               and r.finished_at is not null),
        scored as (   -- golden scores are stored on the tag run; its category scores belong to the model
                      -- that categorised those products
            select 'tag' as stage, r.model_profile, e.value, e.product_count, e.tagging_run_id
              from evaluation_results e join tagging_runs r using (tagging_run_id)
             where e.kind = 'evaluate' and e.scope = 'overall' and e.metric = 'accuracy'
            union all
            select 'categorise', c.model_profile, e.value, e.product_count, e.tagging_run_id
              from evaluation_results e
              join lateral (select cr.model_profile from tagging_run_products p
                              join tagging_runs cr on cr.tagging_run_id = p.category_run_id
                             where p.tagging_run_id = e.tagging_run_id limit 1) c on true
             where e.kind = 'evaluate' and e.scope = 'category' and e.metric = 'accuracy_exact'),
        acc as (   -- the latest scored run per step and profile
            select distinct on (stage, model_profile) stage, model_profile, value, product_count
              from scored order by stage, model_profile, tagging_run_id desc)
        select r.stage, r.model_profile, sum(r.n), sum(r.cost_usd), sum(r.secs), max(acc.value), max(acc.product_count)
          from r left join acc using (stage, model_profile)
         group by r.stage, r.model_profile"""):
        results[(stage, prof)] = {"products": int(n), "costPerProduct": float(cost) / int(n),
                                  "secondsPerProduct": float(secs) / int(n),
                                  "accuracy": float(acc) if acc is not None else None,
                                  "accuracyAnswers": acc_n}
    out = []
    for name, p in s.profiles.items():
        out.append({"name": name, "modelId": p.model_id, "provider": p.provider, "readsPhotos": p.accepts_images,
                    "inputPrice": p.input_price, "outputPrice": p.output_price,
                    "results": {"categorise": results.get(("categorise", name)), "tag": results.get(("tag", name))}})
    return {"profiles": out, "selected": {"categorise": cm, "tag": tm}}


def save_models(body: dict) -> dict:
    """PUT /api/models: either or both choices; each must be a profile in the profiles file."""
    names = set(load_settings(ROOT / "config").profiles)
    cm, tm = body.get("categoriseModel"), body.get("tagModel")
    if cm is None and tm is None:
        raise SettingsError("Choose a model to save.", 422)
    bad = [v for v in (cm, tm) if v is not None and v not in names]
    if bad:
        raise SettingsError(f"Unknown model profile: {', '.join(bad)}. Valid: {', '.join(sorted(names))}.", 422)
    db = get_db()
    with db.lock:
        try:
            if cm is not None:
                db.conn.execute("update app_settings set categorise_model = %s", (cm,))
            if tm is not None:
                db.conn.execute("update app_settings set tag_model = %s", (tm,))
            commit(db)
        except Exception:
            end(db)
            raise
    return models()["selected"]
