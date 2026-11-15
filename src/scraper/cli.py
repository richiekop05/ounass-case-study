"""Command line: discover, migrate, plan, scrape, validate, export."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

from .client import SITE, Client, StopScrape

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

ROOT = Path.cwd()
PLAN_PATH = ROOT / "config" / "sample_plan.csv"
CACHE_DIR = ROOT / ".cache"


def _env(name: str, required: bool = True) -> str:
    load_dotenv(ROOT / ".env")
    value = os.environ.get(name, "").strip()
    if required and not value:
        typer.secho(f"{name} is not set. Copy .env.example to .env and fill it in.", fg="red")
        raise typer.Exit(1)
    return value


def _client(refresh: bool = False) -> Client:
    try:
        return Client(_env("SCRAPER_USER_AGENT"), cache_dir=CACHE_DIR, refresh=refresh)
    except ValueError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)


def _store():
    from .store import Settings, Store
    return Store(Settings(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"), _env("SUPABASE_DB_URL")))


def _db_configured() -> bool:
    load_dotenv(ROOT / ".env")
    return all(os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_URL"))


@app.command()
def discover(listing: str = typer.Option("/women/clothing/dresses", help="Listing path to inspect."),
             out: Path = typer.Option(ROOT / "docs" / "discovery.md")):
    """Fetch robots.txt, one listing and one product page; write docs/discovery.md."""
    from .discover import run_discover
    client = _client()
    try:
        path = run_discover(client, listing, out)
    except StopScrape as e:
        typer.secho(f"STOPPED: {e}", fg="red")
        raise typer.Exit(2)
    typer.echo(f"wrote {path} ({client.stats.requests} requests, {client.stats.cache_hits} from cache)")


@app.command()
def migrate():
    """Create the scraper's tables, the products_with_type view and the image bucket."""
    store = _store()
    applied = store.migrate()
    store.close()
    typer.echo(f"applied: {', '.join(applied)}")


@app.command()
def plan(spine: Path = typer.Option(..., exists=True, help="Spine v5 nodes CSV."),
         target: int = typer.Option(250, help="Total products to plan for."),
         out: Path = typer.Option(PLAN_PATH),
         force: bool = typer.Option(False, help="Overwrite an existing (possibly reviewed) plan."),
         skip_db: bool = typer.Option(False, help="Only draft the CSV; do not load categories.")):
    """Load the spine into `categories` and draft config/sample_plan.csv for review."""
    from .sampling import draft_plan, load_spine, write_plan
    nodes = load_spine(spine)
    typer.echo(f"spine: {len(nodes)} nodes from {spine}")

    if skip_db or not _db_configured():
        typer.echo("database not configured or --skip-db: categories not loaded")
    else:
        store = _store()
        count = store.load_categories(nodes)
        store.close()
        typer.echo(f"categories: {count} rows (file has {len(nodes)})")
        if count != len(nodes):
            typer.secho("row count differs from the spine file", fg="red")
            raise typer.Exit(1)

    if out.exists() and not force:
        typer.secho(f"{out} exists (it may have been reviewed). Use --force to redraft.", fg="yellow")
        return
    client = _client()
    sitemap = client.get(SITE + "/sitemaps/categories.xml", expect_page=False).text
    import re
    paths = [u.replace(SITE, "") for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap)]
    paths = [p for p in paths if client.allowed(SITE + p)]
    rows, notes = draft_plan(nodes, paths, target)
    write_plan(rows, out)
    typer.echo(f"wrote {out}: {len(rows)} rows, {sum(r.quota for r in rows)} products")
    for n in notes:
        typer.echo(f"  - {n}")
    typer.echo("Review the plan by hand before running `scraper scrape`.")


def _cats_from_spine(spine: Path) -> dict[str, dict]:
    from .sampling import load_spine
    return {n.node_id: {"node_id": n.node_id, "parent_id": n.parent_id, "level": n.level, "name": n.name,
                        "slug": n.slug, "path": n.path, "is_leaf": n.is_leaf, "review_flag": n.review_flag,
                        "department": n.department} for n in load_spine(spine)}


@app.command()
def scrape(limit: Optional[int] = typer.Option(None, help="Stop after N products (smoke test: 20)."),
           category: Optional[str] = typer.Option(None, help="Only the plan row(s) for this spine node ID."),
           no_images: bool = typer.Option(False, "--no-images", help="Skip image download and upload."),
           refresh: bool = typer.Option(False, "--refresh", help="Ignore the page cache."),
           dry_run: bool = typer.Option(False, "--dry-run",
                                        help="No database: write data/dry_run.jsonl. Needs --spine."),
           spine: Optional[Path] = typer.Option(None, help="Spine CSV, for --dry-run only."),
           plan_path: Path = typer.Option(PLAN_PATH, "--plan")):
    """Run the plan and write products to the database."""
    from .report import checks, render
    from .sampling import check_plan, load_spine, read_plan
    from .scrape import DryRunSink, build_report, run_scrape

    plan_rows = read_plan(plan_path)
    started = datetime.now(timezone.utc).isoformat()
    args = {"limit": limit, "category": category, "no_images": no_images, "refresh": refresh,
            "dry_run": dry_run}

    if dry_run:
        if not spine:
            typer.secho("--dry-run needs --spine to resolve nodes", fg="red")
            raise typer.Exit(1)
        cats = _cats_from_spine(spine)
        sink, store, run_id = DryRunSink(ROOT / "data" / "dry_run.jsonl"), None, None
    else:
        store = _store()
        cats = store.categories()
        if not cats:
            typer.secho("categories is empty: run `scraper plan --spine ...` first", fg="red")
            raise typer.Exit(1)
        sink = store
        run_id = store.start_run(started, args)

    problems = check_plan(plan_rows, load_spine(spine) if dry_run else [
        _node_like(c) for c in cats.values()])
    if problems:
        typer.secho("plan problems:\n  " + "\n  ".join(problems), fg="red")
        raise typer.Exit(1)

    client = _client(refresh)
    status, notes, exit_code = "completed", None, 0
    from .scrape import RunState
    state = RunState()
    try:
        state = run_scrape(client, sink, plan_rows, cats, run_id=run_id, limit=limit, category=category,
                           no_images=no_images, log=typer.echo, state=state)
    except StopScrape as e:
        status, notes, exit_code = "stopped", str(e), 2
    except KeyboardInterrupt:
        status, notes, exit_code = "interrupted", "interrupted by user; rerun to resume from cache", 130
    report = build_report(state, client, plan_rows, started, status, notes)
    report["run_id"] = run_id
    if store is not None:
        store.finish_run(run_id, report)
        store.close()
    scoped = [r for r in plan_rows if not category or r.spine_node_id == category]
    typer.echo(render(report, None if (limit or category) else checks(state.accepted, cats, scoped)))
    if dry_run:
        typer.echo(f"dry run: wrote {sink.path} ({len(sink.rejects)} rejects)")
    if exit_code:
        typer.secho(f"Run {status}: {notes}", fg="red")
        raise typer.Exit(exit_code)


def _node_like(c: dict):
    from .sampling import Node
    return Node(node_id=c["node_id"], parent_id=c["parent_id"], level=c["level"], audience="",
                department=c.get("department") or "", name=c["name"], slug=c["slug"], path=c["path"],
                is_leaf=c["is_leaf"], review_flag=c.get("review_flag") or "")


@app.command()
def validate(check_files: int = typer.Option(10, help="Download this many stored images to confirm they open.")):
    """Re-check the products table against the schema and the acceptance rules."""
    from .report import checks, fill_rates
    from .sampling import read_plan
    store = _store()
    products = store.all_products()
    cats = store.categories()
    plan_rows = read_plan(PLAN_PATH) if PLAN_PATH.exists() else None
    rows = checks(products, cats, plan_rows, typed=store.products_with_type())

    bad_files = []
    sample = [im["storage_path"] for p in products[:: max(1, len(products) // max(check_files, 1))]
              for im in p["images"][:1] if im.get("storage_path")][:check_files]
    for path in sample:
        try:
            data = store.download_image(path)
            if not data.startswith(b"\xff\xd8"):
                bad_files.append(f"{path}: not a JPEG")
        except Exception as e:  # noqa: BLE001 - report every failure
            bad_files.append(f"{path}: {e}")
    rows.append((f"stored image files open ({len(sample)} sampled)", bool(sample) and not bad_files,
                 "; ".join(bad_files) or "ok"))
    store.close()

    typer.echo(f"products: {len(products)}")
    typer.echo(f"fill rates: {json.dumps(fill_rates(products))}")
    for name, ok, detail in rows:
        typer.echo(f"[{'x' if ok else ' '}] {name}: {detail}")
    if not all(ok for _, ok, _ in rows):
        raise typer.Exit(1)


@app.command()
def export(out: Path = typer.Option(ROOT / "data" / "products.jsonl"),
           with_raw: bool = typer.Option(False, help="Include the raw source JSON.")):
    """Write the products table to data/products.jsonl."""
    store = _store()
    products = store.all_products(with_raw=with_raw)
    store.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for p in products:
            f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
    typer.echo(f"wrote {len(products)} rows to {out}")


@app.command("fetch-photos")
def fetch_photos(limit: Optional[int] = typer.Option(None, help="Stop after N products.")):
    """Download the photos of the products in the database from Ounass into the photo bucket.

    For a database filled with `catalog load-data`: the data has each photo's Ounass address but not the photo.
    Photos already in the bucket are skipped, so it is safe to stop and rerun. About one request a second."""
    client = _client()
    store = _store()
    with store.conn.cursor() as cur:
        cur.execute("""select product_id, position, source_url, storage_path from product_images
                       where storage_path is not null order by product_id, position""")
        rows = cur.fetchall()
    by_product: dict[str, list] = {}
    for r in rows:
        by_product.setdefault(r[0], []).append(r)
    products = list(by_product.items())[:limit] if limit else list(by_product.items())
    stored = skipped = failed = 0
    try:
        for i, (pid, images) in enumerate(products, 1):
            have = store.listed_images(pid)
            for _, _, url, path in images:
                if path in have:
                    skipped += 1
                    continue
                try:
                    resp = client.get(url, expect_page=False)
                except Exception as e:  # a photo Ounass no longer serves: report and carry on
                    if isinstance(e, StopScrape):
                        raise
                    failed += 1
                    typer.secho(f"  {path}: {e}", fg="yellow")
                    continue
                if not resp.content_type.startswith("image/"):
                    failed += 1
                    typer.secho(f"  {path}: not an image ({resp.content_type})", fg="yellow")
                    continue
                store.upload_image(path, resp.content, resp.content_type.split(";")[0])
                stored += 1
            if i % 20 == 0 or i == len(products):
                typer.echo(f"{i}/{len(products)} products: {stored} photos stored, {skipped} already there, "
                           f"{failed} failed")
    except StopScrape as e:
        typer.secho(f"STOPPED: {e}. Wait a while, then run the command again to continue.", fg="red")
        raise typer.Exit(2)
    finally:
        store.close()
        client.close()


if __name__ == "__main__":
    sys.exit(app())
