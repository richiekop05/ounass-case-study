"""`tagger compare`: runs side by side on quality, cost and time (PRD 2.1).

Each step is reported on its own; a combined total appears only with --with-total.
Projected costs are estimates: per-product cost of this run multiplied by N.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from .config import price_tokens
from .runs import Db


def load_run(db: Db, run_id: int) -> dict:
    row = db.execute(
        """select tagging_run_id, stage, model_profile, model_id, products_attempted, products_succeeded,
                  input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, cost_usd,
                  extract(epoch from (finished_at - started_at)), input_price, output_price, cached_input_price,
                  cache_write_price, batch_input_price, batch_output_price
           from tagging_runs where tagging_run_id = %s""", (run_id,)).fetchone()
    if not row:
        raise LookupError(f"run {run_id} not found")
    keys = ("run_id", "stage", "profile", "model_id", "attempted", "succeeded", "input", "cached", "written",
            "output", "cost", "seconds", "ip", "op", "cp", "wp", "bip", "bop")
    r = {k: (float(v) if hasattr(v, "is_finite") else v) for k, v in zip(keys, row)}
    for k in ("input", "cached", "written", "output", "attempted", "succeeded"):
        r[k] = int(r[k] or 0)
    r["cost"] = float(r["cost"] or 0)
    r["seconds"] = float(r["seconds"] or 0)
    n = r["succeeded"]
    r["per_product"] = r["cost"] / n if n else None
    if n and r["bip"] is not None and r["bop"] is not None:
        scale = r["bip"] / r["ip"] if r["ip"] else 1     # cache prices scaled by the batch discount
        batch = price_tokens(r["input"], r["output"], r["cached"], r["written"], r["bip"], r["bop"],
                             (r["cp"] if r["cp"] is not None else r["ip"]) * scale,
                             (r["wp"] if r["wp"] is not None else r["ip"]) * scale)
        r["batch_per_product"] = batch / n
    else:
        r["batch_per_product"] = None
    q = {}
    for scope, metric, value in db.execute(
            """select scope, metric, value from evaluation_results
               where tagging_run_id = %s and kind = 'evaluate' and metric in ('accuracy', 'coverage', 'review_rate',
                   'accuracy_exact')""", (run_id,)):
        q[(scope, metric)] = float(value) if value is not None else None
    r["quality"] = q
    return r


def _money(x, digits=4):
    return "-" if x is None else f"${x:,.{digits}f}"


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def compare(db: Db, run_ids: list[int], stage: str | None = None, project: int | None = None,
            with_total: bool = False, save: bool = True) -> str:
    runs = [load_run(db, r) for r in run_ids]
    if stage:
        runs = [r for r in runs if r["stage"] == stage]
        if not runs:
            return f"none of runs {run_ids} is a {stage} run"
    rows: list[tuple[str, list[str]]] = []

    def add(name, fn):
        rows.append((name, [fn(r) for r in runs]))

    add("step", lambda r: r["stage"])
    add("model profile", lambda r: r["profile"] or "-")
    add("model id", lambda r: r["model_id"] or "-")
    add("products attempted / succeeded", lambda r: f"{r['attempted']} / {r['succeeded']}")
    for k, name in (("input", "input tokens"), ("cached", "cached input tokens"), ("written", "cache-write tokens"),
                    ("output", "output tokens")):
        add(f"{name} (total / per product)",
            lambda r, k=k: f"{r[k]:,} / {r[k] / r['succeeded']:,.0f}" if r["succeeded"] else f"{r[k]:,} / -")
    add("cost total", lambda r: _money(r["cost"]))
    add("cost per product", lambda r: _money(r["per_product"], 5))
    add("cost per 1,000 products", lambda r: _money(r["per_product"] * 1000 if r["per_product"] else None, 2))
    add("time total", lambda r: f"{r['seconds']:.0f}s")
    add("time per product", lambda r: f"{r['seconds'] / r['succeeded']:.1f}s" if r["succeeded"] else "-")
    add("accuracy (overall)", lambda r: _pct(r["quality"].get(("overall", "accuracy"),
                                                              r["quality"].get(("category", "accuracy_exact")))))
    add("coverage (overall)", lambda r: _pct(r["quality"].get(("overall", "coverage"))))
    add("review rate (overall)", lambda r: _pct(r["quality"].get(("overall", "review_rate"),
                                                                 r["quality"].get(("category", "review_rate")))))
    # the five facets where the runs differ most
    facets = {s for r in runs for (s, m) in r["quality"] if m == "accuracy" and s not in ("overall", "category")}
    spread = []
    for f in facets:
        vals = [r["quality"].get((f, "accuracy")) for r in runs]
        known = [v for v in vals if v is not None]
        if len(known) > 1:
            spread.append((max(known) - min(known), f))
    for _, f in sorted(spread, reverse=True)[:5]:
        add(f"accuracy {f}", lambda r, f=f: _pct(r["quality"].get((f, "accuracy"))))
    if project:
        add(f"projected cost for {project:,} products, standard (estimate)",
            lambda r: _money(r["per_product"] * project if r["per_product"] else None, 0))
        add(f"projected cost for {project:,} products, batch (estimate)",
            lambda r: _money(r["batch_per_product"] * project if r["batch_per_product"] else None, 0))

    head = ["run"] + [str(r["run_id"]) for r in runs]
    width = max(len(n) for n, _ in rows) + 2
    colw = max(14, *(len(v) for _, vals in rows for v in vals)) + 2
    out = [f"{head[0]:<{width}}" + "".join(f"{h:>{colw}}" for h in head[1:])]
    out += [f"{n:<{width}}" + "".join(f"{v:>{colw}}" for v in vals) for n, vals in rows]
    if with_total:
        total = sum(r["cost"] for r in runs)
        out.append(f"{'combined cost of these runs':<{width}}{_money(total):>{colw}}")
        if project:
            per = sum(r["per_product"] or 0 for r in runs)
            out.append(f"{'combined projection, standard (estimate)':<{width}}{_money(per * project, 0):>{colw}}")

    if save:
        with db.lock, db.conn.transaction():
            for r in runs:
                db.conn.execute("delete from evaluation_results where tagging_run_id = %s and kind = 'compare'",
                                (r["run_id"],))
                figures = {"cost_usd": r["cost"], "cost_per_product": r["per_product"],
                           "cost_per_1000_products": r["per_product"] * 1000 if r["per_product"] else None,
                           "seconds": r["seconds"],
                           "seconds_per_product": r["seconds"] / r["succeeded"] if r["succeeded"] else None,
                           "input_tokens": r["input"], "cached_input_tokens": r["cached"],
                           "output_tokens": r["output"]}
                if project:
                    figures["projected_cost_standard_estimate"] = (r["per_product"] or 0) * project
                    if r["batch_per_product"] is not None:
                        figures["projected_cost_batch_estimate"] = r["batch_per_product"] * project
                for metric, value in figures.items():
                    db.conn.execute(
                        """insert into evaluation_results (tagging_run_id, kind, scope, metric, value, product_count,
                               detail) values (%s, 'compare', 'run', %s, %s, %s, %s)""",
                        (r["run_id"], metric, value, r["succeeded"],
                         Jsonb({"stage": r["stage"], "profile": r["profile"], "compared_with": run_ids,
                                "projected_products": project})))
        db.commit()
    return "\n".join(out)
