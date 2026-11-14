"""Write docs/database.md from db/migrations/*.sql, so the table reference cannot drift from the schema.

    uv run python scripts/db_reference.py            # rewrite docs/database.md
    uv run python scripts/db_reference.py --check    # exit 1 if docs/database.md is out of date

Columns and keys are read from the migrations themselves: `create table`, then every `alter table` (add, drop
and rename column, including those inside `do $$ ... $$` blocks), in file order. Only the one-line purpose and
"written by" of each table are kept by hand, below; the script stops if a table or view has none, so a new
migration cannot slip in undocumented.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
OUT = ROOT / "docs" / "database.md"

# table -> (what it holds, which part of the system writes it)
TABLES = {
    "categories": ("The category spine: one row per node, L1 to L5.", "`scraper plan --spine`"),
    "products": ("One scraped product per colourway, with the page's own JSON in `raw`.", "`scraper scrape`, Live tag"),
    "product_images": ("Up to 4 photos per product: source URL and path in the private `product-images` bucket.",
                       "`scraper scrape`, Live tag"),
    "product_rejects": ("Products the scraper skipped, and why.", "`scraper scrape`"),
    "scrape_runs": ("One row per scrape run: requests, counts against quota, fill rates, errors.", "`scraper scrape`"),
    "facets": ("Each facet's rules: policy, definition (rubric), limits, confidence level.",
               "`catalog seed-taxonomy`, tagger app (Facets, Confidence)"),
    "facet_values": ("The allowed values of each facet, with definitions.", "`catalog seed-taxonomy`, tagger app (Facets)"),
    "category_facets": ("Which facets apply to which category.", "`catalog seed-taxonomy`"),
    "brands": ("Brand list used to check the brand facet.", "`catalog seed-taxonomy`, tagging (new brands)"),
    "facet_changes": ("History of facet edits made in the app, for Restore.", "tagger app (Facets)"),
    "tagging_runs": ("One row per categorise or tag run: model, counts, cost, time.", "`tagger categorise` / `tag` / `run`, Live tag"),
    "tagging_run_products": ("Each product in a run and how it went.", "`tagger categorise` / `tag` / `run`, Live tag"),
    "tagging_calls": ("Every model request and reply, with tokens and cost.", "`tagger categorise` / `tag` / `run`, Live tag"),
    "catalog_products": ("The tagger's category for each product, review state, approval and publishing.",
                         "`tagger categorise` / `run`, Live tag, tagger app (review, approve, Confidence), `tagger review import`"),
    "product_facet_tags": ("Every facet answer for a product in a run: value, confidence, quote, status.",
                           "`tagger tag` / `derive` / `restatus`, Live tag, tagger app (review)"),
    "golden_products": ("The hand-labelled golden set: product, answer category, holdout flag.",
                        "`tagger golden sample` / `import` / `import-holdout`"),
    "golden_tags": ("The golden set's facet answers.", "`tagger golden import` / `import-holdout` / `import-disagreements`"),
    "evaluation_results": ("Scores of a run against the golden set.", "`tagger evaluate` / `compare`"),
    "run_facet_stats": ("Per run and facet: unknown rate and confidence, for monitoring.", "`tagger tag` / `run` / `restatus` / `monitor`"),
    "monitor_baseline": ("The run that monitoring compares against.", "`tagger monitor`"),
    "monitor_runs": ("Each monitoring check.", "`tagger tag` / `run` / `restatus` / `monitor`"),
    "monitor_alerts": ("Unknown-rate and confidence-shift alerts.", "`tagger tag` / `run` / `restatus` / `monitor`"),
    "app_settings": ("One row: the models chosen for categorise and tag, and the category confidence level.",
                     "tagger app (Models, Confidence), `tagger models use`"),
    "store_occasion_profiles": ("The store's occasions and what each one asks of an outfit.", "`richies seed-rules`"),
    "store_outfit_roles": ("Which spine node plays which outfit role (top, shoes, bag ...).", "`richies seed-rules`"),
    "store_look_templates": ("The outfit shapes a look is built from.", "`richies seed-rules`"),
    "store_colour_pairings": ("Colour pairs that go together.", "`richies seed-rules`"),
    "store_score_weights": ("Weights for ranking and for how events move a shopper's taste.", "`richies seed-rules`"),
    "store_personas": ("The four demo shoppers and their switcher lines.", "`richies seed-rules`"),
    "store_events": ("The store's event log: persona seed history and every session event.",
                     "`richies load-personas`, the store (`richies serve`)"),
}
VIEWS = {
    "products_with_type": "Products with `product_type` from the leaf node's slug.",
    "v_catalog_products": "Approved, published products: what the store reads.",
    "v_product_facets": "The current facet answers of each published product.",
    "v_invalid_tags": "Answers whose value is not allowed for the facet or category.",
    "v_run_costs": "Cost and tokens per run.",
    "v_confidence_bands": "Answers grouped by confidence band.",
    "v_facet_trends": "Unknown rate and confidence per facet across runs.",
    "v_monitor_latest": "The latest monitoring figures per facet.",
    "v_open_alerts": "Monitoring alerts not yet cleared.",
    "v_product_review_state": "Ready or needs review, per product, for the review app.",
    "v_product_review_state_all": "The same, including store samples.",
    "v_review_queue": "The review app's Products list.",
    "v_review_outcomes": "What reviewers did with each answer, for the Metrics tab.",
    "v_review_outcome_rows": "The rows behind `v_review_outcomes`.",
    "v_tag_flags": "Flags raised on answers (low confidence, conflicts).",
}

NAME = r'"?([a-z_][a-z0-9_]*)"?'
CONSTRAINT = re.compile(r"^(constraint|primary\s+key|unique|foreign\s+key|check|exclude)\b", re.I)


def _strip(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _split(text: str, sep: str) -> list[str]:
    """Split on `sep` outside brackets, quotes and $tag$ blocks."""
    out, buf, depth, i, quote, dollar = [], [], 0, 0, False, None
    while i < len(text):
        if dollar:
            if text.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
        elif quote:
            if text[i] == "'":
                quote = False
        elif text[i] == "'":
            quote = True
        elif m := re.match(r"\$[a-z_]*\$", text[i:], re.I):
            dollar = m.group(0)
            buf.append(dollar)
            i += len(dollar)
            continue
        elif text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        elif text[i] == sep and depth == 0:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(text[i])
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _column(defn: str) -> dict:
    m = re.match(NAME + r"\s+(.*)$", defn.strip(), re.S | re.I)
    name, rest = m.group(1), " ".join(m.group(2).split())
    typ = re.split(r"\s+(not\s+null|null|default|primary|references|check|unique|generated|constraint)\b",
                   rest, maxsplit=1, flags=re.I)[0]
    ref = re.search(r"references\s+" + NAME, rest, re.I)
    return {"name": name, "type": typ, "pk": bool(re.search(r"primary\s+key", rest, re.I)),
            "ref": ref.group(1) if ref else None, "not_null": bool(re.search(r"not\s+null|primary\s+key", rest, re.I))}


def build() -> tuple[dict, dict, dict]:
    tables: dict[str, dict] = {}
    views: dict[str, str] = {}
    origin: dict[str, str] = {}

    def statement(s: str, fname: str) -> None:
        s = s.strip()
        if m := re.match(r"do\s+(\$[a-z_]*\$)(.*)\1", s, re.S | re.I):     # walk the alters inside a do block
            for inner in re.findall(r"alter\s+table[^;]*;", m.group(2), re.I):
                statement(inner.rstrip(";"), fname)
            return
        if m := re.match(r"create\s+table\s+(?:if\s+not\s+exists\s+)?" + NAME + r"\s*\((.*)\)\s*$", s, re.S | re.I):
            name = m.group(1)
            if name in tables:
                return
            cols, pk = {}, []
            for part in _split(m.group(2), ","):
                if CONSTRAINT.match(part):
                    if k := re.search(r"primary\s+key\s*\(([^)]*)\)", part, re.I):
                        pk = [c.strip().strip('"') for c in k.group(1).split(",")]
                    if f := re.search(r"foreign\s+key\s*\(([^)]*)\)\s*references\s+" + NAME, part, re.I):
                        for c in f.group(1).split(","):
                            cols.get(c.strip(), {})["ref"] = f.group(2)
                    continue
                c = _column(part)
                cols[c["name"]] = c
            for c in pk:
                cols[c]["pk"] = True
            tables[name], origin[name] = cols, fname
            return
        if m := re.match(r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?" + NAME + r"\s+(.*)$", s, re.S | re.I):
            cols = tables.get(m.group(1))
            if cols is None:
                return
            for clause in _split(m.group(2), ","):
                if a := re.match(r"add\s+column\s+(?:if\s+not\s+exists\s+)?(.*)$", clause, re.S | re.I):
                    c = _column(a.group(1))
                    cols.setdefault(c["name"], c)
                elif d := re.match(r"drop\s+column\s+(?:if\s+exists\s+)?" + NAME, clause, re.I):
                    cols.pop(d.group(1), None)
                elif r := re.match(r"rename\s+column\s+" + NAME + r"\s+to\s+" + NAME, clause, re.I):
                    if r.group(1) in cols:
                        cols[r.group(2)] = {**cols.pop(r.group(1)), "name": r.group(2)}
                elif n := re.match(r"alter\s+column\s+" + NAME + r"\s+(set|drop)\s+not\s+null", clause, re.I):
                    if n.group(1) in cols:
                        cols[n.group(1)]["not_null"] = n.group(2).lower() == "set"
                elif re.match(r"drop\s+constraint\s+(?:if\s+exists\s+)?" + re.escape(m.group(1)) + r"_pkey\b",
                              clause, re.I):
                    for c in cols.values():
                        c["pk"] = False
                elif re.match(r"add\s+(constraint\s+\S+\s+)?primary\s+key", clause, re.I):
                    for c in re.search(r"\(([^)]*)\)", clause).group(1).split(","):
                        cols[c.strip()]["pk"] = True
            return
        if m := re.match(r"drop\s+table\s+(?:if\s+exists\s+)?" + NAME, s, re.I):
            tables.pop(m.group(1), None)
            return
        if m := re.match(r"create\s+(?:or\s+replace\s+)?view\s+" + NAME, s, re.I):
            views.setdefault(m.group(1), fname)
            return

    for f in sorted(MIGRATIONS.glob("*.sql")):
        for s in _split(_strip(f.read_text()), ";"):
            statement(s, f.name)
    return tables, views, origin


def render() -> str:
    tables, views, origin = build()
    missing = [t for t in tables if t not in TABLES] + [v for v in views if v not in VIEWS]
    if missing:
        sys.exit("scripts/db_reference.py: add a description for " + ", ".join(missing))
    lines = ["# Database reference", "",
             "Generated by `uv run python scripts/db_reference.py` from `db/migrations/*.sql`. Do not edit by hand.", "",
             "All tables are in the `public` schema of a Supabase (Postgres) project. Row level security is on with no "
             "policies, so only the service key (used by the command line and the two local apps) can read or write. "
             "Migrations are applied in file order and recorded in `schema_migrations`; `scraper migrate`, "
             "`catalog migrate` and `richies setup` all apply the ones not yet applied. Migration "
             "`001_scraper.sql` also creates the private storage bucket `product-images` for product photos.", "",
             "## Tables", "", "| Table | What it holds | Written by | Key columns | First created in |",
             "| --- | --- | --- | --- | --- |"]
    for t, cols in tables.items():
        what, who = TABLES[t]
        keys = [f"`{c['name']}` (PK)" for c in cols.values() if c["pk"]]
        keys += [f"`{c['name']}` → `{c['ref']}`" for c in cols.values() if c["ref"] and not c["pk"]]
        lines.append(f"| `{t}` | {what} | {who} | {', '.join(keys) or '—'} | `{origin[t]}` |")
    lines += ["", "## Views", "", "Read-only; the apps read through these.", "", "| View | What it shows | First created in |",
              "| --- | --- | --- |"]
    lines += [f"| `{v}` | {VIEWS[v]} | `{f}` |" for v, f in views.items()]
    lines += ["", "## Columns", ""]
    for t, cols in tables.items():
        lines += [f"### `{t}`", "", "| Column | Type | Null | Key |", "| --- | --- | --- | --- |"]
        for c in cols.values():
            key = "PK" if c["pk"] else (f"→ `{c['ref']}`" if c["ref"] else "")
            typ = c["type"].replace("|", "\\|")
            lines.append(f"| `{c['name']}` | {typ} | {'no' if c['not_null'] else 'yes'} | {key} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            sys.exit("docs/database.md is out of date: run uv run python scripts/db_reference.py")
        print("docs/database.md is up to date")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(ROOT)}")
