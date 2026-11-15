"""Copy the data in the public tables to files in db/data/, and load those files into a new database.

The tables themselves come from the migrations (`catalog migrate`); these files hold only rows. One gzipped CSV
per table, plus manifest.json with the load order, the columns and the row counts.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

# Not copied: the migration ledger is written by `catalog migrate`, and session events are one person's clicks.
SKIP_TABLES = {"schema_migrations"}
# Rows kept for some tables. Persona starting histories are store events marked is_seed.
ROW_FILTERS = {"store_events": "is_seed"}
# Rows a migration inserts. They are replaced by the copied rows.
SEEDED_BY_MIGRATIONS = {"app_settings"}
# Checks on new rows that copied rows may fail although they passed when written (a product's category
# changed after older runs tagged it), and a trigger that would reset updated_at to the time of the load.
TRIGGERS_OFF = {"catalog_products": "catalog_products_before_write",
                "product_facet_tags": "product_facet_tags_check_applicable"}


class DataError(Exception):
    pass


def _tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute("""select c.relname from pg_class c
                           where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
                           order by c.relname""").fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def _load_order(conn: psycopg.Connection, tables: list[str]) -> list[str]:
    """Tables ordered so each comes after the tables it refers to (self references are left out)."""
    deps = {t: set() for t in tables}
    for child, parent in conn.execute("""select conrelid::regclass::text, confrelid::regclass::text
                                         from pg_constraint
                                         where contype = 'f' and connamespace = 'public'::regnamespace""").fetchall():
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    order, done = [], set()
    while len(order) < len(tables):
        ready = sorted(t for t in tables if t not in done and deps[t] <= done)
        if not ready:
            raise DataError(f"circular references between {sorted(set(tables) - done)}")
        order += ready
        done |= set(ready)
    return order


def _columns(conn: psycopg.Connection, table: str) -> list[str]:
    rows = conn.execute("""select column_name from information_schema.columns
                           where table_schema = 'public' and table_name = %s and is_generated = 'NEVER'
                           order by ordinal_position""", (table,)).fetchall()
    return [r[0] for r in rows]


def _key(conn: psycopg.Connection, table: str) -> list[str]:
    rows = conn.execute("""select a.attname from pg_index i
                           join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
                           where i.indrelid = %s::regclass and i.indisprimary
                           order by array_position(i.indkey, a.attnum)""", (f"public.{table}",)).fetchall()
    return [r[0] for r in rows]


def _migrations(conn: psycopg.Connection) -> list[str]:
    return [r[0] for r in conn.execute("select filename from schema_migrations order by filename").fetchall()]


def export_data(conn: psycopg.Connection, out_dir: Path) -> dict:
    """Write every public table to out_dir. Reads only."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = _load_order(conn, _tables(conn))
    manifest = {"exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "migrations": _migrations(conn), "tables": []}
    for t in tables:
        cols = _columns(conn, t)
        key = _key(conn, t) or cols
        where = sql.SQL(" where {}").format(sql.SQL(ROW_FILTERS[t])) if t in ROW_FILTERS else sql.SQL("")
        query = sql.SQL("copy (select {cols} from {t}{where} order by {key}) to stdout with (format csv, header)").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)), t=sql.Identifier(t), where=where,
            key=sql.SQL(", ").join(map(sql.Identifier, key)))
        path = out_dir / f"{t}.csv.gz"
        # mtime=0 keeps the file the same when the rows are the same.
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
            with conn.cursor().copy(query) as copy:
                for chunk in copy:
                    f.write(chunk)
        rows = conn.execute(sql.SQL("select count(*) from {t}{where}").format(t=sql.Identifier(t), where=where)).fetchone()[0]
        manifest["tables"].append({"table": t, "file": path.name, "columns": cols, "rows": rows})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def load_data(conn: psycopg.Connection, in_dir: Path) -> dict:
    """Load the files in in_dir into an empty database, in one transaction: all of it or nothing."""
    manifest_path = in_dir / "manifest.json"
    if not manifest_path.exists():
        raise DataError(f"{manifest_path} not found")
    manifest = json.loads(manifest_path.read_text())
    have = set(_migrations(conn))
    missing = [m for m in manifest["migrations"] if m not in have]
    if missing:
        raise DataError(f"run `uv run catalog migrate` first ({len(missing)} migrations not applied, "
                        f"first {missing[0]})")
    tables = set(_tables(conn))
    for entry in manifest["tables"]:
        t = entry["table"]
        if t not in tables:
            raise DataError(f"table {t} is not in this database; run `uv run catalog migrate` first")
        if t in SEEDED_BY_MIGRATIONS:
            continue
        if conn.execute(sql.SQL("select exists (select 1 from {})").format(sql.Identifier(t))).fetchone()[0]:
            raise DataError(f"table {t} already has rows. load-data only fills a new, empty database, "
                            "so it never overwrites data.")
    counts = {}
    try:
        for t, trig in TRIGGERS_OFF.items():
            conn.execute(sql.SQL("alter table {} disable trigger {}").format(sql.Identifier(t), sql.Identifier(trig)))
        conn.execute("set constraints all deferred")
        for t in SEEDED_BY_MIGRATIONS:
            conn.execute(sql.SQL("delete from {}").format(sql.Identifier(t)))
        for entry in manifest["tables"]:
            t, cols = entry["table"], entry["columns"]
            query = sql.SQL("copy {t} ({cols}) from stdin with (format csv, header)").format(
                t=sql.Identifier(t), cols=sql.SQL(", ").join(map(sql.Identifier, cols)))
            with gzip.open(in_dir / entry["file"], "rb") as f, conn.cursor().copy(query) as copy:
                while chunk := f.read(1 << 20):
                    copy.write(chunk)
            counts[t] = conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(t))).fetchone()[0]
            if counts[t] != entry["rows"]:
                raise DataError(f"{t}: loaded {counts[t]} rows, the file has {entry['rows']}")
        # Move each id counter past the loaded ids, so new rows do not collide with them.
        for t, col, seq in conn.execute("""select c.table_name, c.column_name, pg_get_serial_sequence('public.' || c.table_name, c.column_name)
                                           from information_schema.columns c
                                           where c.table_schema = 'public'
                                             and pg_get_serial_sequence('public.' || c.table_name, c.column_name) is not null""").fetchall():
            conn.execute(sql.SQL("select setval(%s, coalesce((select max({col}) from {t}), 0) + 1, false)").format(
                col=sql.Identifier(col), t=sql.Identifier(t)), (seq,))
        for t, trig in TRIGGERS_OFF.items():
            conn.execute(sql.SQL("alter table {} enable trigger {}").format(sql.Identifier(t), sql.Identifier(trig)))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return counts
