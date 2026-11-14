"""The app's bridge to the tagger pipeline: the same functions a batch run uses, never a second path.

Each job gets its own database connection so a long model call never blocks page requests. Settings are
read fresh for every job (config file + app_settings), so a level or model saved in the app is used at once.
"""
from __future__ import annotations

from tagger.config import load_settings
from tagger.dbsettings import apply
from tagger.runs import Db, RateGate, finish_run

from .db import end, ROOT, _connect
from .jobs import Job


def settings_for(db: Db):
    s = load_settings(ROOT / "config")
    with db.lock:
        try:
            apply(s, db.conn)
        finally:
            end(db)          # ends the read; leaves a test's own transaction alone
    return s


def context(db: Db, settings, job: Job | None = None):
    from tagger.products import ImageStore
    from tagger.steps import Context
    try:
        images = ImageStore()
    except RuntimeError:
        images = None
    log = (lambda *a: None)
    return Context(db, settings, images, RateGate(), log=log)


def _mark(db, run_id: int, purpose: str) -> None:
    """Runs made for Richie's store samples are marked so the tagger's run figures and monitoring skip them."""
    if purpose != "tagger":
        db.execute("update tagging_runs set purpose = %s where tagging_run_id = %s", (purpose, run_id))
        db.commit()


def categorise(product_id: str, job: Job, purpose: str = "tagger") -> dict:
    """Step 1 for one product. Returns its category decision."""
    from tagger.steps import run_categorise
    db = _connect()
    try:
        settings = settings_for(db)
        profile = settings.profiles[settings.categorise_model]
        job.models["categorise"] = profile.name
        run = run_categorise(context(db, settings, job), profile, [product_id])
        _mark(db, run.run_id, purpose)
        summary = finish_run(db, run)
        job.cost_usd += float(summary.get("cost_usd") or 0)
        row = db.execute("""select category_id, category_status, category_confidence from catalog_products
                            where product_id = %s""", (product_id,)).fetchone()
        db.conn.rollback()
        if not row:
            done = db.execute("""select status, error from tagging_run_products
                                 where tagging_run_id = %s and product_id = %s""", (run.run_id, product_id)).fetchone()
            raise RuntimeError(f"Categorising failed: {done[1] if done and done[1] else 'no category returned'}")
        return {"categoryId": row[0], "status": row[1], "confidence": float(row[2]) if row[2] is not None else None,
                "runId": run.run_id}
    finally:
        db.conn.close()


def tag(product_id: str, job: Job, purpose: str = "tagger") -> dict:
    """Steps 2 and 3 (tag, derive) for one product, with the saved tagging model."""
    from tagger.derive import run_derive
    from tagger.steps import run_tag
    db = _connect()
    try:
        settings = settings_for(db)
        profile = settings.profiles[settings.tag_model]
        job.models["tag"] = profile.name
        ctx = context(db, settings, job)
        run = run_tag(ctx, profile, [product_id])
        _mark(db, run.run_id, purpose)
        run_derive(db, run.run_id, settings, ROOT, log=lambda *a: None)
        summary = finish_run(db, run)
        job.cost_usd += float(summary.get("cost_usd") or 0)
        done = db.execute("""select status, error from tagging_run_products
                             where tagging_run_id = %s and product_id = %s""", (run.run_id, product_id)).fetchone()
        db.conn.rollback()
        if not done or done[0] != "done":
            raise RuntimeError(f"Tagging failed: {(done[1] if done else None) or 'the model call did not succeed'}")
        return {"runId": run.run_id}
    finally:
        db.conn.close()
