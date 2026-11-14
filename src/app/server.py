"""FastAPI server: the JSON API under /api and the built front end from ./static.

Errors return a status code and a `message` the page can show as written; raw database errors are
never returned (Build PRD section 6).
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import ConfigError, read

log = logging.getLogger("tagger.app")
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Tagger", docs_url=None, redoc_url=None, openapi_url=None)


class AppError(Exception):
    """An error whose message is safe to show on the page."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


@app.exception_handler(AppError)
async def _app_error(_: Request, e: AppError):
    return JSONResponse({"message": e.message}, status_code=e.status)


@app.exception_handler(HTTPException)
async def _http_error(_: Request, e: HTTPException):
    return JSONResponse({"message": str(e.detail)}, status_code=e.status_code)


@app.exception_handler(ConfigError)
async def _config_error(_: Request, e: ConfigError):
    return JSONResponse({"message": str(e)}, status_code=500)


@app.exception_handler(Exception)
async def _unexpected(_: Request, e: Exception):
    log.exception("unhandled error")              # details go to the server log only
    return JSONResponse({"message": "Something went wrong on the server. Try again."}, status_code=500)


@app.get("/api/health")
def health():
    n = read("select count(*) from products p where not exists (select 1 from catalog_products c "
                   "where c.product_id = p.product_id and c.source = 'sample')")[0][0]
    return {"ok": True, "products": n}


# ------------------------------------------------------------------ products and filters (step 1)

def _filters(tab: str = "products", state: str = "", q: str = "", category: str = "", facet: str = "",
             value: str = "", flagged: bool = False, ids: str = "", sort: str = "review", page: int = 1):
    from .listing import Filters
    if tab not in ("products", "approved"):
        raise AppError("tab must be products or approved")
    if sort not in ("review", "brand"):
        raise AppError("sort must be review or brand")
    return Filters(tab=tab, state=state, q=q, category=category, facet=facet, value=value,
                   flagged=flagged, ids=ids, sort=sort, page=max(1, page))


@app.get("/api/summary")
def api_summary(tab: str = "products", state: str = "", q: str = "", category: str = "", facet: str = "",
                value: str = "", flagged: bool = False, ids: str = ""):
    from .listing import summary
    return summary(_filters(tab, state, q, category, facet, value, flagged, ids))


@app.get("/api/products")
def api_products(tab: str = "products", state: str = "", q: str = "", category: str = "", facet: str = "",
                 value: str = "", flagged: bool = False, ids: str = "", sort: str = "review", page: int = 1):
    from .listing import rows
    return rows(_filters(tab, state, q, category, facet, value, flagged, ids, sort, page))


@app.get("/api/facets")
def api_facets():
    from .listing import facet_options
    return {"facets": facet_options()}


@app.get("/api/categories/tree")
def api_category_tree():
    from .listing import category_tree
    return {"nodes": category_tree()}


# ------------------------------------------------------------------ detail page (step 2)

@app.get("/api/products/{product_id}")
def api_product(product_id: str):
    from .detail import NotFound, product_detail
    try:
        return product_detail(product_id)
    except NotFound as e:
        raise AppError(str(e), 404)
    except LookupError:
        raise AppError(f"Product {product_id} was not found.", 404)


@app.get("/api/categories")
def api_categories(q: str = ""):
    from .detail import category_search
    return {"categories": category_search(q)}


# ------------------------------------------------------------------ review and approval (step 3)

from pydantic import BaseModel  # noqa: E402


class Decisions(BaseModel):
    facets: dict[str, dict] = {}


class CategoryChoice(BaseModel):
    categoryId: str | None = None    # None = approve the category as shown


class ReadyFilters(BaseModel):
    state: str = ""
    q: str = ""
    category: str = ""
    facet: str = ""
    value: str = ""
    flagged: bool = False
    ids: str = ""


def _review_call(fn, *args, **kw):
    from .review import ReviewError
    try:
        return fn(*args, **kw)
    except ReviewError as e:
        return JSONResponse({"message": e.message, "problems": e.problems}, status_code=e.status)


@app.put("/api/products/{product_id}/review")
def api_save(product_id: str, body: Decisions):
    from .review import save
    return _review_call(save, product_id, body.facets)


@app.post("/api/products/approve-ready")
def api_approve_ready(body: ReadyFilters):
    from .listing import Filters
    from .review import approve_ready
    return _review_call(approve_ready, Filters(**body.model_dump()))


@app.post("/api/products/{product_id}/approve")
def api_approve(product_id: str, body: Decisions | None = None):
    from .review import approve
    if body is None or not body.facets:     # from the list: allowed only for a Ready product
        return _review_call(approve, product_id, None, via="list", require_ready=True)
    return _review_call(approve, product_id, body.facets, via="detail")


@app.post("/api/products/{product_id}/approve-detail")
def api_approve_detail(product_id: str, body: Decisions | None = None):
    """Approve and publish from the detail page, with or without new decisions (saved ones are used)."""
    from .review import approve
    return _review_call(approve, product_id, body.facets if body else None, via="detail")


@app.post("/api/products/{product_id}/unapprove")
def api_unapprove(product_id: str):
    from .review import unapprove
    return _review_call(unapprove, product_id)


@app.put("/api/products/{product_id}/category")
def api_category(product_id: str, body: CategoryChoice):
    from . import jobs, pipeline
    from .review import ReviewError, decide_category
    try:
        r = decide_category(product_id, body.categoryId)
    except ReviewError as e:
        return JSONResponse({"message": e.message, "problems": e.problems}, status_code=e.status)
    if not r["retag"]:
        return {"jobId": None}

    def work(job):
        job.begin("Tagging")
        out = pipeline.tag(product_id, job)
        job.end("Tagging")
        job.result = {"productId": product_id, **out}
    try:
        job = jobs.start("retag", work, product_id, steps=["Tagging"])
    except jobs.Busy as e:
        raise AppError(str(e), 409)
    return {"jobId": job.id}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int):
    from . import jobs
    from .live import item_states
    job = jobs.get(job_id)
    if job is None:
        raise AppError("That job is not known (the app may have restarted).", 404)
    out = job.to_json()
    if job.kind in ("listing", "sample"):
        out["items"] = item_states(job)
    return out


# ------------------------------------------------------------------ live tag (step 4)

class LiveCheck(BaseModel):
    url: str
    limit: int | None = None


class LiveStart(BaseModel):
    url: str
    mode: str                     # product | listing (what the reviewer chose)
    limit: int | None = None      # listing only; ignored for a single product
    again: bool = False           # "Tag again" for a product already in the database


def _live_call(fn, *args):
    from .live import LiveError
    try:
        return fn(*args)
    except LiveError as e:
        raise AppError(e.message, e.status)


@app.post("/api/live-tag/check")
def api_live_check(body: LiveCheck):
    from .live import check
    return check(body.url, body.limit)


@app.post("/api/live-tag")
def api_live_start(body: LiveStart):
    from .live import start_listing, start_product
    if body.mode not in ("product", "listing", "sample"):
        raise AppError("mode must be product, listing or sample")
    if body.mode == "product":
        return _live_call(start_product, body.url, body.again)
    if body.limit is None:
        raise AppError("Choose a number from 1 to 25.", 422)
    return _live_call(start_listing, body.url, body.limit, body.mode == "sample")


@app.post("/api/jobs/{job_id}/retry")
def api_job_retry(job_id: int):
    from .live import retry_product
    return _live_call(retry_product, job_id)


@app.post("/api/jobs/{job_id}/stop")
def api_job_stop(job_id: int):
    from . import jobs
    job = jobs.get(job_id)
    if job is None:
        raise AppError("That job is not known.", 404)
    job.stop = True        # items in progress finish; the rest are cancelled
    return {"stopping": True}


@app.post("/api/jobs/{job_id}/items/{position}/retry")
def api_item_retry(job_id: int, position: int):
    from .live import retry_item
    return _live_call(retry_item, job_id, position)


# ------------------------------------------------------------------ metrics (step M)

@app.on_event("startup")
def _warm_metrics():
    """Compute the default Metrics page in the background, so the first visit is quick. Best effort:
    nothing here may stop the app from starting."""
    import threading

    try:
        from .metrics import warm
        threading.Thread(target=warm, name="metrics-warm", daemon=True).start()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("metrics warm-up skipped")


@app.get("/api/runs")
def api_runs():
    from .metrics import runs
    return {"runs": runs()}


@app.get("/api/metrics")
def api_metrics(run: int | None = None, category: str = "", facet: str = ""):
    from .metrics import MetricsError, metrics
    try:
        return metrics(run, category, facet)
    except MetricsError as e:
        raise AppError(e.message, e.status)


# ------------------------------------------------------------------ confidence and models (step C)

def _settings_call(fn, *args):
    from .settings_tabs import SettingsError
    try:
        return fn(*args)
    except SettingsError as e:
        return JSONResponse({"message": e.message, "problems": e.problems}, status_code=e.status)


@app.get("/api/confidence")
def api_confidence(category: str = ""):
    from .settings_tabs import confidence
    return confidence(category)


@app.post("/api/confidence/preview")
def api_confidence_preview(body: dict, category: str = ""):
    from .settings_tabs import preview
    return _settings_call(preview, body, category)


@app.put("/api/confidence")
def api_confidence_apply(body: dict):
    from .settings_tabs import apply
    return _settings_call(apply, body)


@app.get("/api/models")
def api_models():
    from .settings_tabs import models
    return models()


@app.put("/api/models")
def api_models_save(body: dict):
    from .settings_tabs import save_models
    return _settings_call(save_models, body)


# ------------------------------------------------------------------ facets tab (step F)

def _facet_call(fn, *args):
    from .facets_tab import FacetError
    try:
        return fn(*args)
    except FacetError as e:
        return JSONResponse({"message": e.message, "problems": e.problems, "fields": e.fields},
                            status_code=e.status)


@app.get("/api/facet-list")
def api_facet_list(category: str = "", q: str = ""):
    from .facets_tab import facet_list
    return {"facets": facet_list(category, q)}


@app.get("/api/facets/{facet_id}")
def api_facet(facet_id: str):
    from .facets_tab import facet_detail
    return _facet_call(facet_detail, facet_id)


@app.put("/api/facets/{facet_id}")
def api_facet_save(facet_id: str, body: dict):
    from .facets_tab import save
    return _facet_call(save, facet_id, body)


@app.get("/api/facets/{facet_id}/changes/{change_id}")
def api_facet_change(facet_id: str, change_id: int):
    row = read("select before, after from facet_changes where facet_id = %s and change_id = %s", (facet_id, change_id))
    if not row:
        raise AppError("That change is not in this facet's history.", 404)
    return {"before": row[0][0], "after": row[0][1]}


@app.post("/api/facets/{facet_id}/restore")
def api_facet_restore(facet_id: str, body: dict):
    from .facets_tab import restore
    return _facet_call(restore, facet_id, int(body.get("changeId") or 0))


@app.post("/api/facets/{facet_id}/test")
def api_facet_test(facet_id: str, body: dict):
    from .facets_tab import test_on_product
    return _facet_call(test_on_product, facet_id, body.get("product") or "")


@app.get("/api/product-search")
def api_product_search(q: str = ""):
    from .facets_tab import product_search
    return {"products": product_search(q)}


# ------------------------------------------------------------------ front end

if (STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Every non-API path returns the single-page app, so addresses with filters survive a reload."""
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    index = STATIC / "index.html"
    if not index.exists():
        return JSONResponse({"message": "The front end is not built yet: run `npm run build` in web/."},
                            status_code=503)
    return FileResponse(index)
