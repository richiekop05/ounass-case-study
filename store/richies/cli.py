"""`richies`: Richie's store commands."""
from __future__ import annotations

import os

import psycopg
import typer
from dotenv import load_dotenv

from scraper.store import normalize_db_url

from .rules import ROOT, RuleError, seed

app = typer.Typer(add_completion=False, help="Richie's store: rules, sample data and the store server.")


@app.callback()
def main():
    """Richie's store."""


def _conn() -> psycopg.Connection:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        typer.secho("SUPABASE_DB_URL is not set in .env", fg="red")
        raise typer.Exit(1)
    return psycopg.connect(normalize_db_url(url))


@app.command("seed-rules")
def seed_rules():
    """Load the rule tables (occasion profiles, outfit roles, look templates, colour pairings, weights,
    personas) from store/sample_data and the category spine. Fails on any value not in the vocabulary."""
    with _conn() as conn:
        try:
            r = seed(conn)
        except RuleError as e:
            typer.secho(str(e), fg="red")
            raise typer.Exit(1)
    typer.echo(f"occasion profiles: {r['occasions']} (drafts until reviewed)")
    typer.echo("outfit roles: " + ", ".join(f"{k} {v}" for k, v in sorted(r["roles"].items())))
    typer.echo(f"colour pairings: {r['pairs']}; personas: {r['personas']}")
    typer.echo(f"spine nodes to review ({len(r['flagged'])}):")
    for line in r["flagged"]:
        typer.echo(f"  {line}")


@app.command("draft-personas")
def draft_personas():
    """Propose starting histories for the personas from the approved catalogue, written into
    store/sample_data/personas.json for the owner to edit. Does not load them."""
    from .loader import write_draft
    with _conn() as conn:
        r = write_draft(conn)
    typer.echo("drafted: " + ", ".join(f"{k} {v}" for k, v in r.items()))
    typer.echo("edit store/sample_data/personas.json, then run `uv run richies load-personas`")


@app.command("load-personas")
def load_personas_cmd():
    """Load the personas' starting histories (seed events) from store/sample_data/personas.json."""
    from .loader import LoadError, load_personas
    with _conn() as conn:
        try:
            r = load_personas(conn)
        except LoadError as e:
            typer.secho(str(e), fg="red")
            raise typer.Exit(1)
    typer.echo("seed events: " + ", ".join(f"{k} {v}" for k, v in r.items()))


@app.command()
def coverage():
    """Approved products per outfit role for each active occasion and department: where samples are needed."""
    from .loader import coverage as cov
    with _conn() as conn:
        rows = cov(conn)
    roles = ["one_piece", "top", "bottom", "outerwear", "shoes", "bag", "jewellery", "accessory"]
    head = f"{'occasion':<18}{'dept':<7}" + "".join(f"{r[:9]:>10}" for r in roles)
    typer.echo(head)
    by = {}
    for occ, aud, role, kind, n in rows:
        by.setdefault((occ, aud), {})[role] = (n, kind)
    for (occ, aud), cells in by.items():
        typer.echo(f"{occ:<18}{aud:<7}" + "".join(
            f"{(str(cells[r][0]) + ('*' if cells[r][1] == 'hero' else '')):>10}" for r in roles))
    typer.echo("* = a hero role for the occasion (needs at least 4 for its chip to show); other cells: slot candidates")


@app.command()
def setup():
    """One-time (and safe to rerun) store setup: apply migrations, seed the rule tables, load persona histories."""
    from scraper.store import apply_migrations
    from .loader import LoadError, load_personas
    with _conn() as conn:
        applied = apply_migrations(conn)
        typer.echo(f"migrations applied: {applied or 'none needed'}")
        try:
            r = seed(conn)
            typer.echo(f"rules: {r['occasions']} occasions, {sum(r['roles'].values())} spine nodes, {r['pairs']} colour pairs")
            typer.echo("personas: " + ", ".join(f"{k} {v}" for k, v in load_personas(conn).items()))
        except (RuleError, LoadError) as e:
            typer.secho(str(e), fg="red")
            raise typer.Exit(1)
    typer.echo("next: cd store/web && npm install && npm run build, then uv run richies serve --with-tagger")


@app.command()
def serve(port: int = typer.Option(8780, help="Port on 127.0.0.1."),
          open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the store in the browser."),
          with_tagger: bool = typer.Option(False, "--with-tagger", help="Also start the tagger app on 8765.")):
    """Start Richie's on http://127.0.0.1:<port> (local only). Build the pages first:
    cd store/web && npm install && npm run build."""
    import threading
    import webbrowser

    import uvicorn
    load_dotenv(ROOT / ".env")
    if not os.environ.get("SUPABASE_DB_URL", "").strip():      # fail here with the name, not on the first page
        typer.secho("SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it in.", fg="red")
        raise typer.Exit(1)
    url = f"http://127.0.0.1:{port}/"
    typer.echo(f"Richie's on {url}")
    if with_tagger:
        def tagger_app():
            uvicorn.run("app.server:app", host="127.0.0.1", port=8765, log_level="warning")
        threading.Thread(target=tagger_app, daemon=True).start()
        typer.echo("tagger app on http://127.0.0.1:8765/")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("richies.server:app", host="127.0.0.1", port=port, log_level="warning")


@app.command()
def funnel():
    """The success-measure funnel per persona from the event log (engineering PRD 8): sessions, and how many saw a
    shortlist, tapped a hero piece, viewed a look, swapped, and added a look to bag."""
    with _conn() as conn:
        rows = conn.execute("""
            select persona_id, count(distinct session_id),
                   count(distinct session_id) filter (where type = 'shortlist_view'),
                   count(distinct session_id) filter (where type = 'view' and context->>'surface' = 'shortlist'),
                   count(distinct session_id) filter (where type = 'look_view'),
                   count(distinct session_id) filter (where type in ('swap_in', 'swap_out')),
                   count(distinct session_id) filter (where type = 'add_look_to_bag'),
                   count(*) filter (where type = 'look_card_view' and context->>'placement' = 'top_band'),
                   count(*) filter (where type = 'look_card_tap' and context->>'placement' = 'top_band'),
                   count(*) filter (where type = 'look_card_view' and context->>'placement' = 'grid'),
                   count(*) filter (where type = 'look_card_tap' and context->>'placement' = 'grid'),
                   count(*) filter (where type = 'look_card_view' and context->>'placement' = 'product_page'),
                   count(*) filter (where type = 'look_card_tap' and context->>'placement' = 'product_page'),
                   count(*) filter (where type = 'occasion_select' and coalesce(context->>'surface', 'plp') = 'plp'),
                   count(*) filter (where type = 'occasion_select' and context->>'surface' = 'product_page')
              from store_events where not is_seed group by persona_id order by persona_id""").fetchall()
    # look cards by placement (PRD V5), and occasion choices by surface (PRD V7), so entry points can be compared
    heads = ["persona", "sessions", "shortlist", "tapped hero", "viewed look", "swapped", "look to bag",
             "top seen", "top tapped", "grid seen", "grid tapped", "pdp seen", "pdp tapped",
             "occ on plp", "occ on pdp"]
    typer.echo("".join(f"{h:>13}" for h in heads))
    for r in rows:
        typer.echo("".join(f"{str(v):>13}" for v in r))
    if not rows:
        typer.echo("no sessions yet")


@app.command("reset-events")
def reset_events(yes: bool = typer.Option(False, "--yes", help="Do it without asking.")):
    """Delete every session event (clicks, swaps, filters), keeping the personas' starting histories. Use before a
    demo so the funnel and the behind-the-scenes panel start clean. Restart `richies serve` afterwards."""
    with _conn() as conn:
        n = conn.execute("select count(*) from store_events where not is_seed").fetchone()[0]
        if not n:
            typer.echo("no session events")
            return
        if not yes and not typer.confirm(f"Delete {n} session events (persona histories are kept)?"):
            raise typer.Exit(1)
        conn.execute("delete from store_events where not is_seed")
    typer.echo(f"deleted {n} session events")
