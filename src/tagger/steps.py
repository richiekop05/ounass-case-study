"""Steps 1 (categorise) and 2 (tag): one model call per product, then code decides."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from psycopg.types.json import Jsonb

from catalog.launch_dates import seed_launch_dates

from .config import Settings
from .facets import facets_for_tagging, rubric_version
from .products import Candidates, ImageStore, Product, candidates_for, load_product
from .providers import ModelRequest
from .runs import Db, RateGate, Run, call_model, finish_run, make_provider, process, record_product, start_run
from .tagging import build_request_text, reply_schema, rows_from_reply

SKILLS = Path.cwd() / "skills"
CATEGORISING_SKILL = "categorising_skill.md"
TAGGING_SKILL = "tagging_skill.md"


def read_skill(name: str, skills_dir: Path = SKILLS) -> str:
    path = skills_dir / name
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: the skill files are supplied with the PRD")
    return path.read_text(encoding="utf-8")       # loaded as it is: never edited or shortened


@dataclass
class Context:
    db: Db
    settings: Settings
    images: ImageStore | None
    gate: RateGate
    log: callable = print
    skills_dir: Path = SKILLS


# ------------------------------------------------------------------ step 1

def category_schema(ids: list[str]) -> dict:
    return {"type": "object",
            "properties": {"category_id": {"type": "string", "enum": ids},
                           "runner_up_id": {"anyOf": [{"type": "string", "enum": ids}, {"type": "null"}]},
                           "confidence": {"type": "number"},
                           "reason": {"type": "string"}},
            "required": ["category_id", "runner_up_id", "confidence", "reason"],
            "additionalProperties": False}


def categorise_request(product: Product, cands: Candidates, images, instructions: str) -> ModelRequest:
    text = product.text_block() + "\n\n" + cands.text_block()
    return ModelRequest("categorise", instructions, text, images, category_schema(cands.ids))


def decide_category(reply: dict, ids: list[str], threshold: float, same_facets=None) -> dict:
    """PRD 3.5-3.6: check the IDs in code, then apply the gate. Below the gate, when the top pick and the
    runner-up have identical facet lists (`same_facets(top, runner)`), the top pick is 'flagged': tagged
    anyway, with only the category waiting for a person (13 Nov 2026)."""
    cat, runner = reply.get("category_id"), reply.get("runner_up_id")
    if cat not in ids:
        raise ValueError(f"category_id {cat!r} is not a candidate")
    notes = []
    if runner is not None and (runner not in ids or runner == cat):
        notes.append(f"runner-up {runner!r} dropped ({'same as choice' if runner == cat else 'not a candidate'})")
        runner = None
    conf = float(reply.get("confidence") or 0)
    conf = min(max(conf, 0.0), 1.0)
    if conf >= threshold:
        status = "auto"
    elif runner is not None and same_facets is not None and same_facets(cat, runner):
        status = "flagged"
        notes.append(f"below the gate; {cat} and runner-up {runner} have the same facets: tagged, category flagged")
    else:
        status = "review"
    return {"category_id": cat, "category_runner_up_id": runner, "category_confidence": conf,
            "category_status": status,
            "reason": reply.get("reason"), "notes": notes}


UPSERT_CATALOG = """
insert into catalog_products (product_id, category_id, category_confidence, category_reason, category_status,
    category_runner_up_id, category_run_id, name, brand, price, original_price, currency, url, description,
    details, size_and_fit, primary_image_path)
values (%(product_id)s, %(category_id)s, %(category_confidence)s, %(reason)s, %(category_status)s,
    %(category_runner_up_id)s, %(run_id)s, %(name)s, %(brand)s, %(price)s, %(original_price)s, %(currency)s,
    %(url)s, %(description)s, %(details)s, %(size_and_fit)s, %(image)s)
on conflict (product_id) do update set
    category_id = excluded.category_id, category_confidence = excluded.category_confidence,
    category_reason = excluded.category_reason, category_status = excluded.category_status,
    category_runner_up_id = excluded.category_runner_up_id, category_run_id = excluded.category_run_id,
    name = excluded.name, brand = excluded.brand, price = excluded.price,
    original_price = excluded.original_price, currency = excluded.currency, url = excluded.url,
    description = excluded.description, details = excluded.details, size_and_fit = excluded.size_and_fit,
    primary_image_path = excluded.primary_image_path"""


def categorise_one(ctx: Context, run: Run, provider, instructions: str, product_id: str):
    t0 = time.monotonic()
    db = ctx.db
    with db.lock:
        product = load_product(db.conn, product_id)
        cands = candidates_for(db.conn, product)
    if not cands.nodes:
        raise ValueError("no candidate categories for this product")
    images = []
    if run.profile.accepts_images and product.image_paths and ctx.images:
        images = [ctx.images.get(product.image_paths[0])]
    result = call_model(db, run, provider, categorise_request(product, cands, images, instructions), product_id,
                        ctx.gate)
    def same_facets(a: str, b: str) -> bool:
        with db.lock:
            fa, fb = ({r[0] for r in db.conn.execute("select facet_id from category_facets where category_id = %s",
                                                    (n,))} for n in (a, b))
        return bool(fa) and fa == fb
    d = decide_category(result.parsed, cands.ids, ctx.settings.category_auto_threshold, same_facets)
    note = "; ".join(filter(None, [cands.note, *d["notes"]])) or None
    with db.lock, db.conn.transaction():
        db.conn.execute("insert into brands (brand) values (%s) on conflict do nothing", (product.brand,))
        db.conn.execute(UPSERT_CATALOG, {
            **d, "product_id": product.product_id, "run_id": run.run_id, "name": product.name,
            "brand": product.brand, "price": product.price, "original_price": product.original_price,
            "currency": product.currency, "url": product.url, "description": product.description,
            "details": Jsonb(product.details), "size_and_fit": Jsonb(product.size_and_fit),
            "image": product.image_paths[0] if product.image_paths else None})
        record_product(db, run.run_id, product_id, "done", int((time.monotonic() - t0) * 1000), note=note,
                       category_id=d["category_id"], category_runner_up_id=d["category_runner_up_id"],
                       category_confidence=d["category_confidence"], category_status=d["category_status"],
                       category_run_id=run.run_id)
    db.commit()
    ctx.log(f"    {product_id}: {d['category_id']} ({d['category_status']}, {d['category_confidence']:.2f})")


def products_to_categorise(db: Db, limit: int | None, product_ids: list[str] | None) -> list[str]:
    if product_ids:
        return product_ids
    sql = ("select p.product_id from products p where not exists "
           "(select 1 from catalog_products c where c.product_id = p.product_id) order by p.product_id")
    rows = [r[0] for r in db.execute(sql)]
    return rows[:limit] if limit else rows


def run_categorise(ctx: Context, profile, product_ids: list[str], run: Run | None = None) -> Run:
    instructions = read_skill(CATEGORISING_SKILL, ctx.skills_dir)
    notes = None if profile.accepts_images else "profile does not accept images: text only"
    run = run or start_run(ctx.db, "categorise", profile, instructions, None, notes)
    provider = make_provider(run.profile)
    ctx.log(f"categorise run {run.run_id}: {len(product_ids)} products, model {run.profile.name}")
    process(ctx.db, run, product_ids, lambda pid: categorise_one(ctx, run, provider, instructions, pid),
            ctx.settings.concurrency, ctx.log)
    with ctx.db.lock:      # recency needs a launch date; dummy dates are never written over real ones
        seed_launch_dates(ctx.db.conn, commit=False)
        ctx.db.commit()
    return run


# ------------------------------------------------------------------ step 2

# Drill runs (PRD 9.6): one input deliberately removed, to prove the monitor catches the shift.
DRILLS = {
    "no-design-details": "the Design Details text",
    "no-images": "all product images",
    "title-only": "everything except the title",
}


def drill_text(product: Product, drill: str | None) -> str:
    if drill == "title-only":
        return f"TITLE:\n{product.name}"
    parts = product.parts(extras=True)
    if drill == "no-design-details":
        parts = [(label, text) for label, text in parts if label != "Design Details"]
    return "\n\n".join(f"{label.upper()}:\n{text}" for label, text in parts)

def build_tag_request(product: Product, category_path: str, specs, instructions: str, images,
                      drill: str | None = None) -> ModelRequest:
    """The step 2 request. Shared by the pipeline and `tagger golden prefill`."""
    text = build_request_text(drill_text(product, drill), category_path, specs)
    return ModelRequest("tag", instructions, text, images, reply_schema(specs, value_enums=False))


def tag_request(ctx: Context, run: Run, product: Product, category_path: str, specs, instructions: str):
    images = []
    if run.profile.accepts_images and ctx.images and run.drill not in ("no-images", "title-only"):
        images = [ctx.images.get(p) for p in product.image_paths[:ctx.settings.max_images]]
    return build_tag_request(product, category_path, specs, instructions, images, run.drill)


INSERT_TAG = """insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id, value_number,
                    value_text, status, confidence, evidence, evidence_quote, reason, review_reason)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""


def tag_one(ctx: Context, run: Run, provider, instructions: str, product_id: str):
    t0 = time.monotonic()
    db = ctx.db
    with db.lock:
        row = db.conn.execute("""select cp.category_id, cp.category_status, cp.category_run_id, c.path
                                 from catalog_products cp join categories c on c.node_id = cp.category_id
                                 where cp.product_id = %s""", (product_id,)).fetchone()
        if not row:
            raise ValueError("not categorised yet: run `tagger categorise` first")
        category_id, status, category_run_id, path = row
        if status not in ("auto", "flagged"):
            raise ValueError(f"category status is {status}: a person must confirm the category first")
        product = load_product(db.conn, product_id)
        specs = facets_for_tagging(db.conn, category_id, path, ctx.settings.function_categories)
    rows, note = [], None
    if specs:
        request = tag_request(ctx, run, product, path, specs, instructions)
        result = call_model(db, run, provider, request, product_id, ctx.gate)
        rows = rows_from_reply(specs, result.parsed, product.searchable())
    else:
        note = f"no model-facing facets for {category_id}"
    with db.lock, db.conn.transaction():
        db.conn.execute("delete from product_facet_tags where product_id = %s and tagging_run_id = %s",
                        (product_id, run.run_id))
        with db.conn.cursor() as cur:
            cur.executemany(INSERT_TAG, [r.as_db(product_id, run.run_id) for r in rows])
        record_product(db, run.run_id, product_id, "done", int((time.monotonic() - t0) * 1000), note=note,
                       category_id=category_id, category_status=status, category_run_id=category_run_id)
        if not run.drill:      # Build PRD 3: ai_run_id is always the latest tagger run; an approved
            db.conn.execute(   # product keeps showing its review run until it is sent back
                """update catalog_products set ai_run_id = %s,
                          current_run_id = case when is_published then current_run_id else %s end
                    where product_id = %s""", (run.run_id, run.run_id, product_id))
    db.commit()
    counts = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    ctx.log(f"    {product_id}: {len(specs)} facets -> {counts}")


def golden_product_ids(db: Db, include_unlabelled: bool = False) -> list[str]:
    """Golden products with a reviewed label (a saved category or at least one golden tag); with
    include_unlabelled, every golden product, holdout ones included."""
    if include_unlabelled:
        return [r[0] for r in db.execute("select product_id from golden_products order by 1")]
    return [r[0] for r in db.execute(
        """select g.product_id from golden_products g where g.category_id is not null
           or exists (select 1 from golden_tags t where t.product_id = g.product_id) order by 1""")]


def products_to_tag(db: Db, limit: int | None, product_ids: list[str] | None, golden_only: bool) -> list[str]:
    # store samples (Richie's) are tagged by their own Live tag mode, never by a tagger batch
    sql = "select product_id from catalog_products where category_status in ('auto', 'flagged') and source <> 'sample'"
    params: list = []
    if product_ids:
        sql += " and product_id = any(%s)"
        params.append(product_ids)
    if golden_only:
        sql += " and product_id = any(%s)"
        params.append(golden_product_ids(db))
    rows = [r[0] for r in db.execute(sql + " order by product_id", params)]
    return rows[:limit] if limit else rows


def run_tag(ctx: Context, profile, product_ids: list[str], run: Run | None = None, drill: str | None = None) -> Run:
    instructions = read_skill(TAGGING_SKILL, ctx.skills_dir)
    notes = None if profile.accepts_images else "profile does not accept images: text only"
    if drill:
        if drill not in DRILLS:
            raise ValueError(f"unknown drill {drill!r}; valid: {', '.join(DRILLS)}")
        notes = "; ".join(filter(None, [notes, f"DRILL {drill}: removed {DRILLS[drill]}"]))
    with ctx.db.lock:
        rubric = rubric_version(ctx.db.conn)
    run = run or start_run(ctx.db, "tag", profile, instructions, rubric, notes, drill=drill)
    provider = make_provider(run.profile)
    ctx.log(f"tag run {run.run_id}: {len(product_ids)} products, model {run.profile.name}"
            + (f", DRILL {run.drill}" if run.drill else ""))
    process(ctx.db, run, product_ids, lambda pid: tag_one(ctx, run, provider, instructions, pid),
            ctx.settings.concurrency, ctx.log)
    return run


def finish(ctx: Context, run: Run) -> dict:
    return finish_run(ctx.db, run)


def publish_run(db: Db, run_id: int) -> int:
    """Point current_run_id at a tag run and publish its products. Drill runs are refused."""
    with db.lock:
        row = db.conn.execute("select stage, is_drill, drill_name from tagging_runs where tagging_run_id = %s",
                              (run_id,)).fetchone()
        if not row or row[0] != "tag":
            raise ValueError(f"run {run_id} is not a tag run")
        if row[1]:
            raise ValueError(f"run {run_id} is a drill run ({row[2]}) and can never be published")
        # Build PRD 5: approval in the app is the only way a product reaches the website, so this no
        # longer sets is_published. It points unapproved products at the run, as the pipeline does.
        n = db.conn.execute(
            """update catalog_products set current_run_id = %s, ai_run_id = %s
               where not is_published and product_id in
                 (select product_id from tagging_run_products where tagging_run_id = %s and status = 'done')""",
            (run_id, run_id, run_id)).rowcount
        db.commit()
    return n
