"""Catalog layer commands: migrate, seed-taxonomy, seed-launch-dates."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import typer
from dotenv import load_dotenv

from scraper.store import apply_migrations, normalize_db_url

from .launch_dates import seed_launch_dates
from .seed import SeedError, check_nodes, seed_taxonomy
from .taxonomy import TaxonomyError, apply_definitions, apply_policy, load_taxonomy

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

ROOT = Path.cwd()
CONFIG = ROOT / "config"
DEFAULT_DICTIONARY = CONFIG / "ounass_facet_dictionary_v3_1_corrected.xlsx"
SKIPPED_ROWS_FILE = ROOT / "data" / "seed_skipped_rows.csv"


def connect() -> psycopg.Connection:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        typer.secho("SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it in.", fg="red")
        raise typer.Exit(1)
    return psycopg.connect(normalize_db_url(url), autocommit=False)


@app.command()
def migrate():
    """Apply the migrations not applied yet (001 scraper, 002-005 catalog, 006 tagger)."""
    with connect() as conn:
        applied = apply_migrations(conn)
    typer.secho(f"applied: {', '.join(applied) or 'nothing new'}", fg="green")


@app.command("seed-taxonomy")
def seed_taxonomy_cmd(
    dictionary: Path = typer.Option(DEFAULT_DICTIONARY, help="Facet dictionary workbook (.xlsx)."),
    policy: Path = typer.Option(CONFIG / "facet_policy.csv", help="Supplied facet policy file."),
    definitions: Path = typer.Option(CONFIG / "facet_definitions.csv", help="Rubrics; loaded when present."),
    brands: Path = typer.Option(CONFIG / "brands.csv", help="Brand tiers; exported, then filled in by a person."),
    skip_missing_nodes: bool = typer.Option(False, help="Skip Matrix_long rows whose node is not in categories."),
    skip_facets_without_policy: bool = typer.Option(
        False, help="Leave out Register facets that have no row in the policy file (else stop)."),
    force: bool = typer.Option(False, "--force", help="Also overwrite facets edited in the review app."),
):
    """Load facets, values, category_facets and brands. Safe to rerun."""
    if not dictionary.exists():
        typer.secho(f"{dictionary} not found. Put the facet dictionary in the repository first.", fg="red")
        raise typer.Exit(1)
    try:
        tax = load_taxonomy(dictionary)
        apply_policy(tax, policy, skip_unlisted=skip_facets_without_policy)
        apply_definitions(tax, definitions)
    except TaxonomyError as e:
        typer.secho(f"Cannot load the taxonomy: {e}", fg="red")
        raise typer.Exit(1)
    with connect() as conn:
        missing = check_nodes(conn, tax)
        typer.echo(f"node check: {len({n for n, _, _ in tax.applicability})} Matrix_long nodes, "
                   f"{len(missing)} not in categories")
        try:
            report = seed_taxonomy(conn, tax, skip_missing_nodes=skip_missing_nodes, brands_file=brands,
                                   skipped_rows_file=SKIPPED_ROWS_FILE, force=force)
        except SeedError as e:
            conn.rollback()
            typer.secho(str(e), fg="red")
            raise typer.Exit(1)
    typer.echo(report.render(tax.source))


@app.command("seed-launch-dates")
def seed_launch_dates_cmd():
    """Give catalog products without a launch date a dummy one (25 in the last 30 days). Never overwrites."""
    with connect() as conn:
        result = seed_launch_dates(conn)
    typer.echo(f"launch dates set: {result['set']} ({result['recent_set']} recent); "
               f"catalog products: {result['catalog_products']}, launched in the last 30 days: "
               f"{result['in_last_30_days']}")


if __name__ == "__main__":
    app()
