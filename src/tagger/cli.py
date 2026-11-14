"""Tagging pipeline: categorise, tag, derive, publish, golden set, evaluate, compare."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

import psycopg
import typer
from dotenv import load_dotenv

from scraper.store import normalize_db_url

from .config import ConfigError, Settings, load_settings
from .runs import Db, RateGate, open_run, render_summary

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
golden_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Golden set: sample, export, import.")
app.add_typer(golden_app, name="golden")

ROOT = Path.cwd()
CONFIG = ROOT / "config"
SHEET = ROOT / "golden" / "labelling_sheet.xlsx"


def _settings() -> Settings:
    """The pipeline settings: config/pipeline.toml, with the category gate and the default models taken
    from the database (app_settings), which the review app edits."""
    from .dbsettings import apply_from_url
    try:
        settings = load_settings(CONFIG)
        load_dotenv(ROOT / ".env")
        url = os.environ.get("SUPABASE_DB_URL", "").strip()
        if url:
            settings = apply_from_url(settings, normalize_db_url(url))
        return settings
    except ConfigError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)


def _profile(settings: Settings, name: str):
    try:
        return settings.profile(name)
    except ConfigError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)


def _db() -> Db:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        typer.secho("SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it in.", fg="red")
        raise typer.Exit(1)
    conn = psycopg.connect(normalize_db_url(url), autocommit=False)
    ready = conn.execute("select to_regclass('public.tagging_calls')").fetchone()[0]
    if not ready:
        typer.secho("run `catalog migrate` first (migration 006_tagger.sql)", fg="red")
        raise typer.Exit(1)
    return Db(conn)


def _context(db: Db, settings: Settings, need_images: bool = True):
    from .products import ImageStore
    from .steps import Context
    images = None
    if need_images:
        try:
            images = ImageStore()
        except RuntimeError as e:
            typer.secho(f"{e}; sending text only", fg="yellow")
    return Context(db, settings, images, RateGate(), log=typer.echo)


def _fail(e: Exception):
    typer.secho(str(e), fg="red")
    raise typer.Exit(1)


def _resume(db, run_id, stage, settings):
    try:
        return open_run(db, run_id, stage, settings.profiles)
    except (LookupError, ValueError) as e:
        _fail(e)


# ------------------------------------------------------------------ steps

@app.command()
def categorise(limit: Optional[int] = typer.Option(None, help="At most N products."),
               product: Optional[List[str]] = typer.Option(None, help="Only this product ID (repeatable)."),
               model: Optional[str] = typer.Option(None, help="Model profile for this run only."),
               run: Optional[int] = typer.Option(None, help="Continue this categorise run."),
               concurrency: Optional[int] = typer.Option(None, help="Products at once (default from config).")):
    """Step 1 for products not yet categorised. Starts a run with stage `categorise`."""
    from .steps import products_to_categorise, run_categorise
    settings = _settings()
    if concurrency:
        settings.concurrency = concurrency
    profile = _profile(settings, model or settings.categorise_model)
    db = _db()
    ctx = _context(db, settings)
    resumed = _resume(db, run, "categorise", settings) if run else None
    pids = products_to_categorise(db, limit, product)
    r = run_categorise(ctx, profile, pids, resumed)
    from .runs import finish_run
    typer.echo(render_summary(finish_run(db, r)))


@app.command()
def tag(limit: Optional[int] = typer.Option(None, help="At most N products."),
        product: Optional[List[str]] = typer.Option(None, help="Only this product ID (repeatable)."),
        model: Optional[str] = typer.Option(None, help="Model profile for this run only."),
        golden_only: bool = typer.Option(False, "--golden-only", help="Only golden set products."),
        run: Optional[int] = typer.Option(None, help="Continue this tag run."),
        concurrency: Optional[int] = typer.Option(None, help="Products at once (default from config)."),
        derive: bool = typer.Option(True, help="Run step 3 for this run afterwards."),
        drill: Optional[str] = typer.Option(None, help="Degrade one input on purpose: no-design-details, "
                                                       "no-images or title-only. The run can never be published."),
        monitor: bool = typer.Option(True, help="Run the monitor on this run afterwards.")):
    """Step 2 for products whose category status is `auto`. Starts a run with stage `tag`.
    Always tags from the categories already saved, so two runs with different models are comparable."""
    from .derive import run_derive
    from .runs import finish_run
    from .steps import products_to_tag, run_tag
    settings = _settings()
    if concurrency:
        settings.concurrency = concurrency
    profile = _profile(settings, model or settings.tag_model)
    db = _db()
    ctx = _context(db, settings)
    resumed = _resume(db, run, "tag", settings) if run else None
    pids = products_to_tag(db, limit, product, golden_only)
    if not pids:
        _fail(ValueError("no products to tag: categorise first, or check --product / --golden-only"))
    try:
        r = run_tag(ctx, profile, pids, resumed, drill=drill)
    except ValueError as e:
        _fail(e)
    if derive:
        typer.echo(f"derive: {run_derive(db, r.run_id, settings, ROOT, log=typer.echo)}")
    typer.echo(render_summary(finish_run(db, r)))
    if monitor:
        _monitor(db, settings, r.run_id)


@app.command()
def derive(run: int = typer.Option(..., help="Tag run ID.")):
    """Step 3 for the products in that tag run. Replaces the run's rule rows; touches nothing else."""
    from .derive import run_derive
    settings = _settings()
    db = _db()
    try:
        typer.echo(run_derive(db, run, settings, ROOT, log=typer.echo))
    except ValueError as e:
        _fail(e)


@app.command("run")
def run_all(limit: Optional[int] = typer.Option(None, help="At most N products (`--limit 10` is the smoke test)."),
            product: Optional[List[str]] = typer.Option(None, help="Only this product ID (repeatable)."),
            categorise_model: Optional[str] = typer.Option(None, help="Profile for step 1."),
            tag_model: Optional[str] = typer.Option(None, help="Profile for step 2."),
            concurrency: Optional[int] = typer.Option(None, help="Products at once (default from config)."),
            with_total: bool = typer.Option(False, "--with-total", help="Also print the combined cost."),
            golden_only: bool = typer.Option(False, "--golden-only",
                                             help="Only the labelled golden products, for all three steps "
                                                  "(categorised again even if already in the catalog)."),
            all_golden: bool = typer.Option(False, "--all-golden",
                                            help="Every golden product, holdout and unlabelled ones included.")):
    """All three steps in order, for products not yet categorised (or --product, or --golden-only)."""
    from .derive import run_derive
    from .runs import finish_run
    from .steps import products_to_categorise, products_to_tag, run_categorise, run_tag
    settings = _settings()
    if concurrency:
        settings.concurrency = concurrency
    cat_profile = _profile(settings, categorise_model or settings.categorise_model)
    tag_profile = _profile(settings, tag_model or settings.tag_model)
    db = _db()
    ctx = _context(db, settings)
    if golden_only or all_golden:
        from .steps import golden_product_ids
        if product:
            _fail(ValueError("pass --product or --golden-only / --all-golden, not both"))
        pids = golden_product_ids(db, include_unlabelled=all_golden)
        pids = pids[:limit] if limit else pids
        if not pids:
            _fail(ValueError("no labelled golden products: import a reviewed golden sheet first"))
    else:
        pids = products_to_categorise(db, limit, product)
    if not pids:
        _fail(ValueError("nothing to categorise: every product is already in catalog_products (use --product)"))
    cat_run = run_categorise(ctx, cat_profile, pids)
    cat_summary = finish_run(db, cat_run)
    auto = products_to_tag(db, None, pids, False)
    review = len(pids) - len(auto)
    n_flagged = db.execute("select count(*) from catalog_products where product_id = any(%s) "
                           "and category_status = 'flagged'", (pids,)).fetchone()[0]
    typer.echo(f"{len(auto) - n_flagged} products accepted, {n_flagged} tagged with the category flagged, "
               f"{review} not taggable (review or failed)")
    tag_summary = None
    if auto:
        tag_run = run_tag(ctx, tag_profile, auto)
        typer.echo(f"derive: {run_derive(db, tag_run.run_id, settings, ROOT, log=typer.echo)}")
        tag_summary = finish_run(db, tag_run)
    _monitor(db, settings, cat_run.run_id)
    if tag_summary:
        _monitor(db, settings, tag_run.run_id)
    typer.echo(render_summary(cat_summary))
    if tag_summary:
        typer.echo(render_summary(tag_summary))
        if with_total:
            total = cat_summary["cost_usd"] + tag_summary["cost_usd"]
            typer.echo(f"combined cost of both steps: ${total:.4f}")


@app.command()
def restatus(run: int = typer.Option(..., help="Tag run ID.")):
    """Recalculate the saved status of every tag in a run from its stored confidence and the current
    thresholds (no model call), then re-run the monitor stats for that run."""
    from .restatus import restatus as do_restatus
    settings = _settings()
    db = _db()
    try:
        typer.echo(do_restatus(db, run).render())
    except (LookupError, ValueError) as e:
        _fail(e)
    _monitor(db, settings, run)


@app.command()
def publish(run: int = typer.Option(..., help="Tag run ID.")):
    """Point unapproved products at that run. Publishing happens only by approval in `tagger app`.
    Drill runs are refused."""
    from .steps import publish_run
    try:
        n = publish_run(_db(), run)
    except ValueError as e:
        _fail(e)
    typer.echo(f"{n} unapproved products now point at run {run}; approve them in `tagger app` to publish")


# ------------------------------------------------------------------ monitoring

monitor_app = typer.Typer(add_completion=False, help="Unknown rate and confidence shift against a baseline run.")
app.add_typer(monitor_app, name="monitor")


def _monitor(db, settings, run_id: int, baseline: int | None = None):
    from .monitor import MonitorSettings, render, run_monitor
    try:
        typer.echo(render(run_monitor(db, run_id, MonitorSettings.from_dict(settings.monitor), baseline)))
    except (LookupError, ValueError) as e:
        _fail(e)


@monitor_app.callback(invoke_without_command=True)
def monitor_cmd(ctx: typer.Context, run: Optional[int] = typer.Option(None, help="Run to check."),
                baseline: Optional[int] = typer.Option(None, help="Compare with this run instead of the default.")):
    """Compute a run's facet stats, compare with the baseline, write alerts, print the largest changes."""
    if ctx.invoked_subcommand:
        return
    if run is None:
        _fail(ValueError("give --run ID (or a subcommand: baseline, history, ack)"))
    _monitor(_db(), _settings(), run, baseline)


@monitor_app.command("baseline")
def monitor_baseline(run: int = typer.Option(..., help="Run to pin as the baseline for its stage.")):
    """Pin a run as the baseline for its stage."""
    from .monitor import pin_baseline
    try:
        typer.echo(pin_baseline(_db(), run))
    except (LookupError, ValueError) as e:
        _fail(e)


@monitor_app.command("history")
def monitor_history(facet: str = typer.Option(..., help="Facet ID, or 'category' for categorise runs."),
                    segment: Optional[str] = typer.Option(None, help="e.g. brand=Gucci or department='Women / Bags'."),
                    as_json: bool = typer.Option(False, "--json")):
    """A facet's unknown rate, review rate, coverage and mean confidence for every run, in date order."""
    from .monitor import history
    try:
        rows = history(_db(), facet, segment)
    except ValueError as e:
        _fail(e)
    if as_json:
        typer.echo(json.dumps(rows, indent=1, default=str))
        return
    if not rows:
        typer.echo("no runs have stats for that facet and segment")
    pct = lambda v: "-" if v is None else f"{100 * float(v):.0f}%"
    for r in rows:
        mc = "-" if r["mean_confidence"] is None else f"{float(r['mean_confidence']):.3f}"
        typer.echo(f"run {r['run_id']:<6} {r['stage']:<10} {r['started_at']:%Y-%m-%d %H:%M}  {r['model_profile'] or '-':<8} "
                   f"n={r['products_applicable']:<4} unknown {pct(r['unknown_rate']):>4}  review {pct(r['review_rate']):>4}  "
                   f"coverage {pct(r['coverage']):>4}  mean conf {mc}")


@monitor_app.command("ack")
def monitor_ack(alert: int = typer.Option(..., help="Alert ID."),
                note: Optional[str] = typer.Option(None, help="Why it is acknowledged.")):
    """Mark an alert acknowledged."""
    from .monitor import acknowledge
    try:
        typer.echo(acknowledge(_db(), alert, note))
    except LookupError as e:
        _fail(e)


# ------------------------------------------------------------------ golden

@golden_app.command("sample")
def golden_sample(n: int = typer.Option(50, help="Products to pick (first sample only)."),
                  add: Optional[str] = typer.Option(None, help="Add to the existing set, e.g. "
                                                               "clothing=10,beauty=7,accessories=10,bags=8.")):
    """Pick the golden set with a fixed seed, spread across departments; or add to it with --add."""
    from .golden import sample, sample_add
    db = _db()
    try:
        if add:
            r = sample_add(db, add)
        else:
            alloc = sample(db, n)
            typer.echo(f"golden set: {sum(alloc.values())} products: {alloc}")
            return
    except ValueError as e:
        _fail(e)
    typer.echo(f"before: {sum(r['before'].values())} products {r['before']}")
    if r["already"]:
        typer.echo(f"nothing added: the products for '{r['tag']}' were already selected")
    else:
        typer.echo(f"added: {sum(r['added'].values())} {r['added']}")
        for dept, missing in r["shortfall"].items():
            typer.secho(f"shortfall: {dept} has {missing} fewer unselected products than asked for "
                        f"(not filled from another department)", fg="yellow")
    typer.echo(f"after:  {sum(r['after'].values())} products {r['after']}")


@golden_app.command("export")
def golden_export(out: Path = typer.Option(SHEET, help="Where to write the sheet.")):
    """Write the labelling sheet (no model output in it). Fill in Categories first, import, then export again."""
    from .golden import export
    db = _db()
    try:
        rep = export(db, out)
    except ValueError as e:
        _fail(e)
    typer.echo(f"wrote {out}: {rep}")


@golden_app.command("import")
def golden_import(file: Path = typer.Argument(..., exists=True, help="A filled-in labelling sheet, a reviewed "
                                                                     "pre-filled sheet, or the master answer key."),
                  verdicts_only: bool = typer.Option(
                      False, "--verdicts-only", help="Reviewed sheet: import only facet rows that have a verdict; "
                      "a blank category verdict counts as ok when the product has a facet verdict."),
                  force: bool = typer.Option(
                      False, "--force", help="Import a labelling or review sheet even though the golden tables "
                      "were changed after the sheet was saved (this can undo those changes).")):
    """Validate every answer and load the valid ones. Safe to re-run: it replaces those labels.
    The master answer key (`golden export-master`) is always safe to re-import."""
    from datetime import datetime, timezone
    from .golden import import_sheet
    from .golden_master import import_master, is_master_sheet
    from .golden_review import import_reviewed, is_review_sheet
    db = _db()
    try:
        if is_master_sheet(file):
            if verdicts_only:
                _fail(ValueError("--verdicts-only applies to a reviewed pre-filled sheet, not the master"))
            rep = import_master(db, file)
        else:
            saved = datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc)
            newer = db.execute("select count(*), max(created_at) from golden_tags where created_at > %s",
                               (saved,)).fetchone()
            if newer[0] and not force:
                _fail(ValueError(
                    f"{newer[0]} golden labels were written after {file.name} was saved (latest {newer[1]:%d %b %Y %H:%M}); "
                    f"importing it could undo those corrections. Import the master answer key "
                    f"(golden/golden_master.xlsx) instead, or pass --force."))
            if verdicts_only and not is_review_sheet(file):
                _fail(ValueError("--verdicts-only applies to a reviewed pre-filled sheet"))
            rep = import_reviewed(db, file, verdicts_only) if is_review_sheet(file) else import_sheet(db, file)
    except ValueError as e:
        _fail(e)
    typer.echo(rep.render())


@golden_app.command("export-holdout")
def golden_export_holdout(model: Optional[str] = typer.Option(None, help="Profile (default: golden_prefill_model)."),
                          out: Optional[Path] = typer.Option(None, help="Sheet to write.")):
    """Mark the golden products nobody has reviewed as holdout, and pre-fill them into a review sheet.
    The model drafts the category and the facets afresh; no tagger output is read or written."""
    from .golden_review import prefill
    from .products import ImageStore
    settings = _settings()
    name = model or settings.golden_prefill_model
    profile = _profile(settings, name)
    db = _db()
    with db.conn.transaction():
        db.conn.execute("""update golden_products g set is_holdout = true where g.category_id is null
                           and not exists (select 1 from golden_tags t where t.product_id = g.product_id)""")
    db.conn.commit()
    pids = [r[0] for r in db.execute("select product_id from golden_products where is_holdout order by 1")]
    if not pids:
        _fail(ValueError("no unreviewed golden products to hold out"))
    target = out or ROOT / "golden" / f"holdout_{len(pids)}.xlsx"
    db.conn.rollback()                         # end the read above before switching to read only
    db.conn.read_only = True
    try:
        images = ImageStore()
    except RuntimeError as e:
        typer.secho(f"{e}; drafting from text only", fg="yellow")
        images = None
    try:
        r = prefill(db.conn, profile, images, target, settings, ROOT / "skills",
                    only={p: (None, "") for p in pids}, log=typer.echo)
    except ValueError as e:
        _fail(e)
    finally:
        db.conn.rollback()
    typer.echo(f"{len(pids)} holdout products; model {r['model']} ({r['model_id']}): {r['drafted']} drafted, "
               f"{len(r['failed'])} failed; cost ${r['cost_usd']:.4f}\nwrote {r['path']}")


@golden_app.command("import-holdout")
def golden_import_holdout(file: Path = typer.Argument(..., exists=True, help="The reviewed holdout sheet."),
                          verdicts_only: bool = typer.Option(
                              False, "--verdicts-only", help="Import only facet rows that have a verdict; a blank "
                              "category verdict counts as ok when the product has a facet verdict.")):
    """Load the reviewed holdout sheet. Only holdout products are accepted."""
    import openpyxl
    from .golden_review import import_reviewed, is_review_sheet
    db = _db()
    if not is_review_sheet(file):
        _fail(ValueError(f"{file.name} is not a pre-filled review sheet"))
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    pids = {str(r[0]).strip() for r in wb["Products"].iter_rows(min_row=2, values_only=True) if r and r[0]}
    holdout = {r[0] for r in db.execute("select product_id from golden_products where is_holdout")}
    if pids - holdout:
        _fail(ValueError(f"not holdout products: {', '.join(sorted(pids - holdout))}"))
    try:
        rep = import_reviewed(db, file, verdicts_only)
    except ValueError as e:
        _fail(e)
    typer.echo(rep.render())


@golden_app.command("export-master")
def golden_export_master(out: Path = typer.Option(ROOT / "golden" / "golden_master.xlsx", help="Sheet to write.")):
    """Write the golden tables as they are now (categories and every answer). Re-import with `golden import`."""
    from .golden_master import export_master
    db = _db()
    r = export_master(db.conn, out)
    typer.echo(f"{r['products']} golden products ({r['labelled_products']} with a category), {r['answers']} answers"
               f"\nwrote {r['path']}")


@golden_app.command("export-disagreements")
def golden_export_disagreements(run: int = typer.Option(..., help="Tag run to compare with the answer key."),
                                out: Optional[Path] = typer.Option(None, help="Sheet to write."),
                                holdout: bool = typer.Option(False, "--holdout", help="Holdout products only."),
                                reviewed: bool = typer.Option(False, "--reviewed",
                                                              help="Reviewed (non-holdout) products only.")):
    """Every golden answer the run disagrees with, with the tagger's quote and reason, for review."""
    from .golden_master import export_disagreements
    db = _db()
    if holdout and reviewed:
        _fail(ValueError("pass --holdout or --reviewed, not both"))
    which = True if holdout else False if reviewed else None
    suffix = "_holdout" if holdout else "_reviewed" if reviewed else ""
    target = out or ROOT / "golden" / f"disagreements{suffix}_run{run}.xlsx"
    if target.exists():
        _fail(ValueError(f"{target} exists (it may hold your verdicts); pass another --out"))
    r = export_disagreements(db.conn, run, target, which)
    typer.echo(f"run {run}: {r['rows']} facet disagreements on {r['products_scored']} scored products, "
               f"{r['category_rows']} category disagreements\nwrote {r['path']}")


@golden_app.command("import-disagreements")
def golden_import_disagreements(file: Path = typer.Argument(..., exists=True, help="A reviewed disagreements sheet.")):
    """Apply the verdicts: "tagger right" takes the tagger's value, "neither" takes corrected_value,
    "key right" or blank changes nothing."""
    from .golden_master import import_disagreements
    db = _db()
    try:
        rep = import_disagreements(db, file)
    except ValueError as e:
        _fail(e)
    typer.echo(rep.render())


@golden_app.command("prefill")
def golden_prefill(model: Optional[str] = typer.Option(None, help="Profile (default: golden_prefill_model, opus)."),
                   only_recategorised: Optional[Path] = typer.Option(
                       None, exists=True, help="A reviewed sheet: draft facets only for products whose category "
                                               "was changed, under the corrected category."),
                   products: Optional[str] = typer.Option(
                       None, help="Only these golden products (comma-separated IDs). Each keeps the category "
                                  "pre-filled for it in --categories-from; needs --out."),
                   categories_from: Path = typer.Option(
                       ROOT / "golden" / "prefilled_labels.xlsx",
                       help="Sheet whose pre-filled categories --products keeps (opened read only)."),
                   out: Optional[Path] = typer.Option(None, help="Sheet to write."),
                   force: bool = typer.Option(False, "--force", help="Overwrite a sheet that has review entries.")):
    """Draft a category and facet answers for every golden product into a review sheet.
    Uses the pipeline's requests and checks, with no gate and no thresholds. Writes nothing to the database."""
    from .golden_review import chosen_categories, prefill, recategorised
    from .products import ImageStore
    if products and only_recategorised:
        _fail(ValueError("pass --products or --only-recategorised, not both"))
    if products and out is None:
        _fail(ValueError("--products needs --out: a partial pre-fill never goes to the main sheet"))
    settings = _settings()
    name = model or settings.golden_prefill_model
    if not name:
        _fail(ValueError("set golden_prefill_model in config/pipeline.toml or pass --model"))
    profile = _profile(settings, name)
    if name in (settings.categorise_model, settings.tag_model):
        typer.secho(f"note: {name} is also a model under test; the PRD asks for a different model", fg="yellow")
    db = _db()
    db.conn.rollback()
    db.conn.read_only = True                   # any write would fail: the pre-fill only reads
    try:
        images = ImageStore()
    except RuntimeError as e:
        typer.secho(f"{e}; drafting from text only", fg="yellow")
        images = None
    target = out or ROOT / "golden" / ("prefilled_labels_pass2.xlsx" if only_recategorised
                                       else "prefilled_labels.xlsx")
    try:
        if products:
            only = chosen_categories(db.conn, [p.strip() for p in products.split(",") if p.strip()],
                                     categories_from)
        else:
            only = recategorised(db.conn, only_recategorised) if only_recategorised else None
        if only is not None and not only and not products:
            _fail(ValueError("no product in that sheet has category_verdict = change with a valid corrected category"))
        r = prefill(db.conn, profile, images, target, settings, ROOT / "skills", force, only, log=typer.echo)
    except ValueError as e:
        _fail(e)
    finally:
        db.conn.rollback()
    typer.echo(f"model {r['model']} ({r['model_id']}): {r['drafted']} products drafted, {len(r['failed'])} failed"
               f"{' ' + str(r['failed']) if r['failed'] else ''}; {r['facet_rows']} facet rows; "
               f"cost ${r['cost_usd']:.4f}; {r['seconds']}s\nwrote {r['path']}")


# ------------------------------------------------------------------ evaluation

@app.command()
def evaluate(run: int = typer.Option(..., help="Run to score."),
             compare_with: Optional[int] = typer.Option(None, "--compare", help="Second run, side by side.")):
    """Score a tag run, and its categorise run, against the golden set. For a tag run, also writes the facet
    scorecard to reports/facet_scorecard_run<ID>.xlsx."""
    from .evaluate import evaluate as ev, facet_scorecard, render, write_scorecard
    db = _db()
    try:
        a = ev(db, run)
        b = ev(db, compare_with) if compare_with else None
    except (LookupError, ValueError) as e:
        _fail(e)
    typer.echo(render(a, b))
    for e in filter(None, (a, b)):
        if e["stage"] == "tag":
            path = ROOT / "reports" / f"facet_scorecard_run{e['run_id']}.xlsx"
            write_scorecard(facet_scorecard(e), path)
            typer.echo(f"facet scorecard: {path}")


@app.command("compare")
def compare_cmd(runs: List[int] = typer.Option(..., "--runs", help="Run IDs (repeat --runs or pass several)."),
                project: Optional[int] = typer.Option(None, help="Project cost to this many products."),
                stage: Optional[str] = typer.Option(None, help="Only `tag` or `categorise` runs."),
                with_total: bool = typer.Option(False, "--with-total", help="Add a combined total.")):
    """Runs side by side on quality, cost and time."""
    from .compare import compare
    db = _db()
    try:
        typer.echo(compare(db, runs, stage, project, with_total))
    except LookupError as e:
        _fail(e)


models_app = typer.Typer(add_completion=False, help="Model profiles: list, add, choose per step, store a key, test.")
app.add_typer(models_app, name="models")


def _admin(fn, *args, **kwargs):
    from .model_admin import AdminError
    try:
        return fn(*args, **kwargs)
    except (AdminError, ConfigError) as e:
        _fail(e)


def _emit(data, as_json: bool, text: str):
    typer.echo(json.dumps(data, indent=1, default=str) if as_json else text)


@models_app.callback(invoke_without_command=True)
def models_list(ctx: typer.Context, as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")):
    """List the model profiles, which step each is the default for, and whether its key is set."""
    if ctx.invoked_subcommand:
        return
    from .model_admin import list_models
    load_dotenv(ROOT / ".env")
    data = _admin(list_models, CONFIG)
    lines = [f"defaults: categorise = {data['defaults']['categorise']}, tag = {data['defaults']['tag']}"]
    for m in data["models"]:
        key = "no key needed" if not m["api_key_env"] else (
            f"key {m['api_key_env']} {'set' if m['key_present'] else 'MISSING'}")
        mark = f"   <- default for {', '.join(m['default_for'])}" if m["default_for"] else ""
        cached = f"${m['cached_input_price']}/M" if m["cached_input_price"] is not None else "-"
        lines.append(f"{m['name']:<12} {m['provider']}:{m['model_id']:<24} in ${m['input_price']}/M "
                     f"out ${m['output_price']}/M cached {cached} "
                     f"images={'yes' if m['accepts_images'] else 'no'}  {key}{mark}")
    _emit(data, as_json, "\n".join(lines))


@models_app.command("providers")
def models_providers(as_json: bool = typer.Option(False, "--json")):
    """Adapters available, and what each needs in a profile."""
    from .model_admin import providers
    data = providers()
    _emit(data, as_json, "\n".join(
        f"{p['name']:<18} {p.get('description', p.get('error', ''))}"
        + (" (needs --base-url)" if p.get("needs_base_url") else "") for p in data))


@models_app.command("add")
def models_add(
        name: str = typer.Argument(..., help="Short name used on the command line, e.g. gpt-mini."),
        provider: str = typer.Option(..., help="Adapter: see `tagger models providers`."),
        model_id: str = typer.Option(..., help="Exact model identifier the provider expects."),
        input_price: float = typer.Option(..., help="USD per million input tokens."),
        output_price: float = typer.Option(..., help="USD per million output tokens."),
        cached_input_price: Optional[float] = typer.Option(None, help="USD per million cached input tokens."),
        cache_write_price: Optional[float] = typer.Option(None, help="USD per million cache-write tokens."),
        batch_input_price: Optional[float] = typer.Option(None, help="Batch rate, for projections."),
        batch_output_price: Optional[float] = typer.Option(None, help="Batch rate, for projections."),
        api_key_env: Optional[str] = typer.Option(None, help="The .env variable holding the key ('' for none)."),
        base_url: Optional[str] = typer.Option(None, help="API base URL (needed for openai_compatible)."),
        images: bool = typer.Option(True, "--images/--no-images", help="Whether the model accepts images."),
        json_mode: Optional[str] = typer.Option(None, help="openai_compatible: json_schema, json_object or none."),
        effort: Optional[str] = typer.Option(None, help="Reasoning effort, if the provider has one."),
        max_tokens: Optional[int] = typer.Option(None, help="Reply length ceiling."),
        max_tokens_field: Optional[str] = typer.Option(None, help="e.g. max_completion_tokens for OpenAI reasoning models."),
        extra: Optional[str] = typer.Option(None, help='Extra request fields as JSON, e.g. \'{"temperature": 0}\'.'),
        prices_checked: Optional[str] = typer.Option(None, help="Date the prices were checked (default today)."),
        replace: bool = typer.Option(False, "--replace", help="Overwrite a profile with this name."),
        as_json: bool = typer.Option(False, "--json")):
    """Add a model profile to config/models.toml. The key goes in .env (see `models set-key`)."""
    from .model_admin import add_model
    try:
        extra_dict = json.loads(extra) if extra else None
    except json.JSONDecodeError as e:
        _fail(ValueError(f"--extra is not valid JSON: {e}"))
    fields = dict(provider=provider, model_id=model_id, accepts_images=images, input_price=input_price,
                  output_price=output_price, cached_input_price=cached_input_price,
                  cache_write_price=cache_write_price, batch_input_price=batch_input_price,
                  batch_output_price=batch_output_price, api_key_env=api_key_env, base_url=base_url,
                  json_mode=json_mode, effort=effort, max_tokens=max_tokens, max_tokens_field=max_tokens_field,
                  extra=extra_dict, prices_checked=prices_checked)
    load_dotenv(ROOT / ".env")
    m = _admin(add_model, CONFIG, name, fields, replace)
    hint = "" if m["key_present"] else f"\nnext: uv run tagger models set-key {m['api_key_env']}"
    _emit(m, as_json, f"added {name} ({m['provider']}:{m['model_id']}){hint}\n"
                      f"then: uv run tagger models test {name}")


@models_app.command("remove")
def models_remove(name: str = typer.Argument(...)):
    """Remove a profile. Past runs keep their recorded model and prices."""
    from .model_admin import remove_model
    typer.echo(_admin(remove_model, CONFIG, name))


@models_app.command("use")
def models_use(name: str = typer.Argument(..., help="Profile name."),
               step: str = typer.Option(..., help="categorise, tag or golden_prefill.")):
    """Make a profile the default for a step. A single run can still override it with --model."""
    from .model_admin import set_default
    d = _admin(set_default, CONFIG, step, name)
    if step in ("categorise", "tag"):
        # the database holds the default the pipeline uses (the app's Models tab); the config file is the seed
        load_dotenv(ROOT / ".env")
        url = os.environ.get("SUPABASE_DB_URL", "").strip()
        if url:
            import psycopg
            col = "categorise_model" if step == "categorise" else "tag_model"
            with psycopg.connect(normalize_db_url(url)) as conn:
                conn.execute(f"update app_settings set {col} = %s", (name,))
            d[step] = name
    typer.echo(f"defaults: categorise = {d['categorise']}, tag = {d['tag']}, "
               f"golden_prefill = {d.get('golden_prefill')}")


@models_app.command("set-key")
def models_set_key(env_name: str = typer.Argument(..., help="Variable name, e.g. OPENAI_API_KEY.")):
    """Store an API key in .env. You are prompted for it; it is never echoed or printed."""
    from .model_admin import set_key
    value = typer.prompt(f"{env_name}", hide_input=True)
    r = _admin(set_key, ROOT / ".env", env_name, value)
    typer.echo(f"{'replaced' if r['replaced'] else 'added'} {env_name} in .env")


@models_app.command("test")
def models_test(name: str = typer.Argument(..., help="Profile name."),
                as_json: bool = typer.Option(False, "--json")):
    """Send one tiny request to check the key, model ID, JSON replies and image input."""
    from .model_admin import check_model
    load_dotenv(ROOT / ".env")
    r = _admin(check_model, CONFIG, name)
    if r["ok"]:
        img = "" if not r["images_checked"] else f"; image read {'correctly' if r['image_ok'] else 'WRONGLY'}"
        text = (f"{name}: ok ({r['served_model']}){img}; {r['input_tokens']} in / {r['output_tokens']} out tokens, "
                f"${r['cost_usd']:.6f}, {r['seconds']}s")
    else:
        text = f"{name}: FAILED: {r['error']}"
    _emit(r, as_json, text)
    if not r["ok"]:
        raise typer.Exit(1)


@app.command()
def show(product: str = typer.Option(..., help="Product ID."),
         run: Optional[int] = typer.Option(None, help="Only this run."),
         full: bool = typer.Option(False, help="Also print the instructions (skill file) and schema.")):
    """Print the stored request, reply and saved rows for one product."""
    db = _db()
    sql = """select call_id, tagging_run_id, stage, attempt, model_id, served_model, request, reply, error,
                    input_tokens, cached_input_tokens, output_tokens, cost_usd, duration_ms
             from tagging_calls where product_id = %s"""
    params: list = [product]
    if run:
        sql += " and tagging_run_id = %s"
        params.append(run)
    calls = db.execute(sql + " order by call_id", params).fetchall()
    if not calls:
        typer.echo(f"no model calls stored for {product}")
    for (cid, rid, stage, attempt, model_id, served, req, reply, err, it, ct, ot, cost, ms) in calls:
        typer.secho(f"=== call {cid}: run {rid} ({stage}) attempt {attempt}, {model_id}"
                    f"{f' served by {served}' if served and served != model_id else ''}; tokens in {it} "
                    f"(+{ct} cached) out {ot}; ${float(cost):.5f}; {ms} ms", bold=True)
        typer.echo(f"--- request: images {req.get('images')}")
        if full:
            typer.echo(req.get("instructions", ""))
            typer.echo(json.dumps(req.get("schema"), indent=1)[:20000])
        typer.echo(req.get("text", ""))
        typer.echo("--- reply:")
        typer.echo(_pretty(reply) if reply else "(none)")
        if err:
            typer.secho(f"--- error: {err}", fg="red")
    rows_sql = """select t.tagging_run_id, t.facet_id, coalesce(fv.label, t.value_number::text, t.value_text),
                         t.status, t.confidence, t.evidence, t.evidence_quote, t.reason
                  from product_facet_tags t left join facet_values fv on fv.facet_value_id = t.facet_value_id
                  where t.product_id = %s"""
    rp: list = [product]
    if run:
        rows_sql += " and t.tagging_run_id = %s"
        rp.append(run)
    rows = db.execute(rows_sql + " order by t.tagging_run_id, t.facet_id, t.tag_id", rp).fetchall()
    cat = db.execute("""select category_id, category_status, category_confidence, category_reason
                        from catalog_products where product_id = %s""", (product,)).fetchone()
    typer.secho("=== saved", bold=True)
    if cat:
        typer.echo(f"category {cat[0]} ({cat[1]}, {float(cat[2]):.2f}): {cat[3]}")
    for rid, fid, val, st, conf, ev, quote, reason in rows:
        c = f"{float(conf):.2f}" if conf is not None else "-"
        typer.echo(f"  run {rid} {fid:<22} {str(val or ''):<20} {st:<14} conf {c:<5} {ev or '-':<6} "
                   f"{('“' + quote + '” ') if quote else ''}{reason or ''}")


def _pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=1, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return text



# ------------------------------------------------------------------ review sheets

review_app = typer.Typer(add_completion=False, help="Review a tag run in a spreadsheet instead of the app.")
app.add_typer(review_app, name="review")


@review_app.command("export")
def review_export(run: int = typer.Option(..., help="Tag run to review.")):
    """Write review/review_run<ID>.xlsx: flagged or held categories and the answers in review, golden products
    left out. Accepted answers are not in the sheet."""
    from .review_sheet import export
    db = _db()
    try:
        r = export(db.conn, run, ROOT / "review")
    except (ValueError, FileExistsError) as e:
        _fail(e)
    typer.echo(f"wrote {r['path']}: {r['categories']} category rows, {r['facets']} facet rows, "
               f"{r['products']} products")


@review_app.command("auto-approve")
def review_auto_approve(run: int = typer.Option(..., help="Tag run whose waiting products to approve.")):
    """Approve and publish every product of the run that is waiting for review, with the tagger's answers as
    they stand. Recorded as approved_via 'auto' with the answers 'not reviewed', so review metrics count only
    what a person checked. Products the app would refuse are left for a person and listed."""
    from app.db import get_db
    from .review_sheet import auto_approve_run
    r = auto_approve_run(get_db(), run)
    typer.echo(f"approved and published: {len(r['approved'])}")
    typer.echo(f"left for a person: {len(r['left'])}")
    for x in r["left"]:
        typer.echo(f"  {x}")


@review_app.command("import")
def review_import(path: Path = typer.Argument(..., help="A filled-in review sheet."),
                  retag: bool = typer.Option(True, "--retag/--no-retag",
                                             help="Re-tag products whose category was decided (paid).")):
    """Write the verdicts as the review app does. A product with every row filled in is approved and published;
    one with a blank verdict keeps the verdicts given as saved decisions and stays for review."""
    from .review_sheet import import_sheet
    if not path.exists():
        _fail(FileNotFoundError(f"{path} not found"))
    try:
        r = import_sheet(path, retag=retag)
    except ValueError as e:
        _fail(e)
    typer.echo(r.render())


@app.command("app")
def app_cmd(port: int = typer.Option(8765, help="Port on localhost."),
            open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser.")):
    """Start the review app on localhost (no login, one reviewer) and open it in the browser."""
    import threading
    import webbrowser

    import uvicorn
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    typer.echo(f"Tagger app on {url} (Ctrl+C to stop)")
    uvicorn.run("app.server:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
