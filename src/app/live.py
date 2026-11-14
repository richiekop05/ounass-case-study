"""Live tag (Build PRD 7): one Ounass product link, or the first X products of a listing page, run through
the scraper and the pipeline in the background while the page polls the job.

Same steps, models, skill files and levels as a batch run, so a live-tagged product is indistinguishable
in the database. Nothing is ever published here: only approval does that.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from scraper.live import classify, listing_links, scrape_one

from . import jobs, pipeline
from .db import ROOT, read

MAX_LISTING = 25
PARALLEL = 3
FALLBACK_SECONDS, FALLBACK_COST = 60.0, 0.05
STEPS = ["Fetching product", "Categorising", "Tagging", "Done"]


class LiveError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _client():
    from dotenv import load_dotenv

    from scraper.client import Client
    load_dotenv(ROOT / ".env")
    # refresh: Live tag reads the site as it is now, never a page cached by an earlier scrape
    return Client(os.environ.get("SCRAPER_USER_AGENT", ""), cache_dir=ROOT / ".cache", refresh=True)


def _store():
    from scraper.store import Settings, Store
    names = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_URL")
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        raise LiveError(f"{', '.join(missing)} not set. Copy .env.example to .env and fill it in.", 500)
    return Store(Settings(*(os.environ[n] for n in names)))


def estimate(n: int) -> dict:
    """X times the average time and cost per product of past runs with the current models (else a fallback)."""
    row = read("""select avg(r.cost_usd / nullif(r.products_succeeded, 0)),
                         avg(extract(epoch from r.finished_at - r.started_at) / nullif(r.products_succeeded, 0))
                    from tagging_runs r, app_settings s
                   where not r.is_drill and r.products_succeeded > 0 and r.finished_at is not null
                     and ((r.stage = 'categorise' and r.model_profile = s.categorise_model)
                       or (r.stage = 'tag' and r.model_profile = s.tag_model))
                   group by r.stage""")
    if len(row) == 2 and all(r[0] is not None for r in row):
        cost = sum(float(r[0]) for r in row)
        secs = sum(float(r[1] or 0) for r in row) + 8.0          # + fetching the page and photos
    else:
        cost, secs = FALLBACK_COST, FALLBACK_SECONDS
    return {"seconds": round(secs * n), "costUsd": round(cost * n, 2), "perProductSeconds": round(secs, 1)}


def check(url: str, limit: int | None = None) -> dict:
    c = classify(url)
    out = {"kind": c["kind"]}
    if c["kind"] == "product":
        out["productId"] = c["productId"]
        out["existing"] = _known(c["productId"])
    elif c["kind"] == "listing":
        out["estimate"] = estimate(max(1, min(MAX_LISTING, limit or 5)))
    return out


def _known(pid: str) -> bool:
    return bool(read("select 1 from products where product_id = %s", (pid,)))


def _categorised(pid: str) -> bool:
    return bool(read("select 1 from catalog_products where product_id = %s", (pid,)))


# ------------------------------------------------------------------ single product

def start_product(url: str, again: bool = False) -> dict:
    c = classify(url)
    if c["kind"] != "product":
        raise LiveError("This is not an Ounass product or listing link" if c["kind"] == "neither" else
                        "This is a listing page link, but “Single product” is selected.", 422)
    pid = c["productId"]
    if _known(pid) and _categorised(pid) and not again:
        return {"productId": pid, "existing": True, "jobId": None}

    def work(job: jobs.Job):
        _run_product(job, url, pid, skip_fetch=_known(pid))
    try:
        job = jobs.start("live", work, pid, steps=STEPS)
    except jobs.Busy as e:
        raise LiveError(str(e), 409)
    return {"productId": pid, "existing": False, "jobId": job.id}


def _run_product(job: jobs.Job, url: str, pid: str, skip_fetch: bool, from_step: str | None = None):
    order = STEPS[:3]
    start_at = order.index(from_step) if from_step in order else 0
    if start_at <= 0:
        job.begin("Fetching product")
        if not skip_fetch:
            client, store = _client(), _store()
            try:
                scrape_one(client, store, url)
            finally:
                client.close()
                store.close()
        job.end("Fetching product", None if not skip_fetch else "Already in the database")
    if start_at <= 1:
        job.begin("Categorising")
        cat = pipeline.categorise(pid, job)
        job.end("Categorising")
        if cat["status"] == "review":         # below the category level: the reviewer decides first
            job.step("Tagging").state = "paused"
            job.status = "waiting_for_category"
            job.result = {"productId": pid, "category": cat}
            return
    job.begin("Tagging")
    pipeline.tag(pid, job)
    job.end("Tagging")
    s = job.step("Done")
    s.state, s.seconds = "done", round(time.time() - job.started, 1)
    job.result = {"productId": pid}


def retry_product(job_id: int) -> dict:
    """Try again: resume a failed single-product job from the step that failed."""
    old = jobs.get(job_id)
    if old is None or old.kind != "live" or old.status != "failed":
        raise LiveError("Only a failed live tag can be tried again.", 409)
    pid, step = old.product_id, old.error_step or "Fetching product"
    url = read("select url from products where product_id = %s", (pid,))
    url = url[0][0] if url else None

    def work(job: jobs.Job):
        for s in old.steps:                          # carry over the finished steps
            if s.state == "done":
                job.step(s.name).state, job.step(s.name).seconds = "done", s.seconds
        _run_product(job, url or "", pid, skip_fetch=_known(pid), from_step=step)
    try:
        job = jobs.start("live", work, pid, steps=STEPS)
    except jobs.Busy as e:
        raise LiveError(str(e), 409)
    return {"productId": pid, "jobId": job.id}


# ------------------------------------------------------------------ listing pages

def start_listing(url: str, limit: int, sample: bool = False) -> dict:
    """The first `limit` products of a listing page. `sample`: Richie's store samples (owner, 14 Nov 2026):
    each new product is tagged as usual and published for the store only, without review; products already in
    the catalog are left as they are."""
    c = classify(url)
    if c["kind"] != "listing":
        raise LiveError("This is not an Ounass product or listing link" if c["kind"] == "neither" else
                        "This is a product page link; store samples come from a listing page.", 422)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LISTING:
        raise LiveError(f"Choose a number from 1 to {MAX_LISTING}.", 422)

    def work(job: jobs.Job):
        client, store = _client(), _store()
        lock = threading.Lock()                       # one request at a time, with the scraper's delay
        try:
            job.begin("Reading the listing page")
            with lock:
                items, found = listing_links(client, url, limit)
            job.end("Reading the listing page", f"This page showed {found} products" if found < limit else None)
            job.result = {"found": found, "requested": limit}
            job.items = [{**it, "step": "waiting", "state": None, "error": None, "title": None, "brand": None,
                          "already": False, "seconds": None, "costUsd": 0.0} for it in items]
            for it in job.items:
                if _known(it["productId"]) and (not sample or _categorised(it["productId"])):
                    it.update(step="done", already=True)
            with ThreadPoolExecutor(PARALLEL) as pool:
                list(pool.map(lambda it: _run_item(job, it, client, store, lock),
                              [it for it in job.items if it["step"] == "waiting"]))
        finally:
            client.close()
            store.close()
        if job.stop:
            job.status = "stopped"
    try:
        job = jobs.start("sample" if sample else "listing", work, None, steps=["Reading the listing page"])
    except jobs.Busy as e:
        raise LiveError(str(e), 409)
    return {"jobId": job.id}


def _run_item(job: jobs.Job, it: dict, client, store, lock, from_step: str = "fetch"):
    if job.stop:
        it["step"] = "cancelled"
        return
    t0, cost0 = time.time(), job.cost_usd
    pid = it["productId"]
    try:
        if from_step == "fetch" and not _known(pid):
            it["step"] = "fetching"
            with lock:
                p = scrape_one(client, store, it["url"])
            it.update(title=p.get("name"), brand=p.get("brand"))
        sample = job.kind == "sample"
        purpose = "store_sample" if sample else "tagger"
        if from_step in ("fetch", "categorise"):
            it["step"] = "categorising"
            cat = pipeline.categorise(pid, job, purpose)
            if sample:
                _mark_sample(pid, held=cat["status"] == "review")
            elif cat["status"] == "review":       # a listing job never pauses: the item waits for review
                it.update(step="done", state="needs_review", seconds=round(time.time() - t0, 1))
                return
        it["step"] = "tagging"
        pipeline.tag(pid, job, purpose)
        if sample:
            from .review import publish_sample
            it["step"] = "publishing"
            publish_sample(pid)
            it["state"] = "in_store"
        it.update(step="done", seconds=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001 - one item failing never stops the others
        it.update(failedAt=it["step"], step="failed", error=str(e) or type(e).__name__)
    finally:
        it["costUsd"] = round(job.cost_usd - cost0, 4)


def _mark_sample(pid: str, held: bool) -> None:
    """A store sample is hidden from the tagger app from the moment it is categorised. A category below the gate
    is accepted (owner's decision) so the product can be tagged; it is recorded as flagged."""
    from .db import commit, end, get_db
    db = get_db()
    with db.lock:
        try:
            db.conn.execute("""update catalog_products set source = 'sample',
                                      category_status = case when %s then 'flagged' else category_status end
                                where product_id = %s""", (held, pid))
            commit(db)
        except Exception:
            end(db)
            raise


def retry_item(job_id: int, position: int) -> dict:
    job = jobs.get(job_id)
    if job is None or job.kind not in ("listing", "sample"):
        raise LiveError("That listing job is not known.", 404)
    it = next((x for x in job.items if x["position"] == position), None)
    if it is None or it["step"] != "failed":
        raise LiveError("Only a failed item can be tried again.", 409)
    step = {"fetching": "fetch", "categorising": "categorise", "tagging": "tag",
            "publishing": "tag"}.get(it.get("failedAt"), "fetch")
    it.update(step="waiting", error=None)
    job.status, job.finished = "running", None

    def run():
        client, store = _client(), _store()
        try:
            _run_item(job, it, client, store, threading.Lock(), from_step=step)
        finally:
            client.close()
            store.close()
            job.status, job.finished = "done", time.time()
    threading.Thread(target=run, daemon=True).start()
    return {"jobId": job.id}


def item_states(job: jobs.Job) -> list[dict]:
    """Items with each product's name and current state (Ready / Needs review / Approved), for the table."""
    ids = [it["productId"] for it in job.items if it["step"] == "done"]
    states = {r[0]: (r[1], r[2], r[3], r[4]) for r in read(
        """select product_id, state, n_flagged, name, brand
             from v_product_review_state where product_id = any(%s)""", (ids,))} if ids else {}
    from .photos import sign
    imgs = {r[0]: r[1] for r in read("""select product_id, storage_path from product_images
                                        where product_id = any(%s) and position = 1 and storage_path is not null""",
                                     ([it["productId"] for it in job.items],))} if job.items else {}
    signed = sign(list(imgs.values()))
    names = {r[0]: (r[1], r[2]) for r in read("select product_id, name, brand from products where product_id = any(%s)",
                                              ([it["productId"] for it in job.items],))} if job.items else {}
    out = []
    for it in job.items:
        st = states.get(it["productId"])
        nm = names.get(it["productId"], (it.get("title"), it.get("brand")))
        if job.kind == "sample":         # samples are not in the tagger's state view
            st = None
        out.append({**it, "title": nm[0], "brand": nm[1], "thumb": signed.get(imgs.get(it["productId"])),
                    "state": st[0] if st else it.get("state"), "nFlagged": st[1] if st else None})
    return out
