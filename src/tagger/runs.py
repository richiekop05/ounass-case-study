"""Run records, the model call loop (retries + call log), and running products concurrently."""
from __future__ import annotations

import hashlib
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Callable

import psycopg
from psycopg.types.json import Jsonb

from . import __version__
from .config import Profile, price_tokens
from .providers import (ModelRequest, ModelResult, ProviderError, RateLimited, RetryableError,
                        SchemaError, get_provider)
from .tagging import validate_reply

TRANSIENT_RETRIES = 3
SCHEMA_RETRIES = 1
BACKOFF_BASE = 2.0


class Db:
    """One connection shared by worker threads: each unit of work holds the lock.
    `commit` is a no-op in tests, which run inside one rolled-back transaction."""

    def __init__(self, conn: psycopg.Connection, commit: bool = True):
        self.conn = conn
        self.lock = threading.RLock()
        self._commit = commit

    def commit(self):
        if self._commit:
            self.conn.commit()

    def execute(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params)


@dataclass
class Run:
    run_id: int
    stage: str
    profile: Profile                  # prices are the ones recorded on the run
    started: float = field(default_factory=time.monotonic)
    drill: str | None = None          # a deliberately degraded run (PRD 9.6); never published


def skill_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def start_run(db: Db, stage: str, profile: Profile, instructions: str, rubric: str | None,
              notes: str | None = None, drill: str | None = None) -> Run:
    with db.lock:
        rid = db.conn.execute(
            """insert into tagging_runs (tagger_version, model, instructions_hash, started_at, stage,
                   rubric_version, model_profile, model_id, input_price, output_price, cached_input_price,
                   cache_write_price, batch_input_price, batch_output_price, notes, provider, is_drill, drill_name,
                   products_attempted, products_succeeded, input_tokens, output_tokens, cached_input_tokens,
                   cache_write_tokens, cost_usd, taxonomy_version)
               values (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       0, 0, 0, 0, 0, 0, 0,
                       (select taxonomy_version from app_settings where to_regclass('public.app_settings') is not null))
               returning tagging_run_id""",
            (__version__, profile.model_id, skill_hash(instructions), stage, rubric, profile.name,
             profile.model_id, profile.input_price, profile.output_price, profile.cached_input_price,
             profile.cache_write_price, profile.batch_input_price, profile.batch_output_price, notes,
             profile.provider, drill is not None, drill)).fetchone()[0]
        db.commit()
    return Run(rid, stage, profile, drill=drill)


def open_run(db: Db, run_id: int, stage: str, profiles: dict[str, Profile]) -> Run:
    """Resume a run: the model and prices recorded on the run win over the current config."""
    row = db.execute("""select stage, model_profile, model_id, input_price, output_price, cached_input_price,
                               cache_write_price, batch_input_price, batch_output_price, drill_name
                        from tagging_runs where tagging_run_id = %s""", (run_id,)).fetchone()
    if not row:
        raise LookupError(f"run {run_id} not found")
    if row[0] != stage:
        raise ValueError(f"run {run_id} is a {row[0]} run, not {stage}")
    base = profiles.get(row[1])
    if base is None:
        raise ValueError(f"run {run_id} used profile {row[1]!r}, which is no longer in config/models.toml")
    prof = replace(base, model_id=row[2], input_price=float(row[3]), output_price=float(row[4]),
                   cached_input_price=_f(row[5]), cache_write_price=_f(row[6]),
                   batch_input_price=_f(row[7]), batch_output_price=_f(row[8]))
    return Run(run_id, stage, prof, drill=row[9])


def _f(x):
    return float(x) if x is not None else None


def finish_run(db: Db, run: Run) -> dict:
    with db.lock:
        db.conn.execute(
            """update tagging_runs r set finished_at = now(),
                   products_attempted = p.attempted, products_succeeded = p.succeeded,
                   input_tokens = c.input_tokens, cached_input_tokens = c.cached, cache_write_tokens = c.written,
                   output_tokens = c.output_tokens, cost_usd = c.cost
               from (select count(*) attempted, count(*) filter (where status = 'done') succeeded
                     from tagging_run_products where tagging_run_id = %(r)s) p,
                    (select coalesce(sum(input_tokens), 0) input_tokens, coalesce(sum(cached_input_tokens), 0) cached,
                            coalesce(sum(cache_write_tokens), 0) written, coalesce(sum(output_tokens), 0) output_tokens,
                            coalesce(sum(cost_usd), 0) cost
                     from tagging_calls where tagging_run_id = %(r)s) c
               where r.tagging_run_id = %(r)s""", {"r": run.run_id})
        db.commit()
    return run_summary(db, run.run_id)


def run_summary(db: Db, run_id: int) -> dict:
    row = db.execute("""select stage, model_profile, model_id, products_attempted, products_succeeded,
                               input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, cost_usd,
                               extract(epoch from (finished_at - started_at))
                        from tagging_runs where tagging_run_id = %s""", (run_id,)).fetchone()
    keys = ("stage", "model_profile", "model_id", "attempted", "succeeded", "input_tokens", "cached_input_tokens",
            "cache_write_tokens", "output_tokens", "cost_usd", "seconds")
    s = dict(zip(keys, row))
    n = s["succeeded"] or 0
    s["cost_usd"] = float(s["cost_usd"] or 0)
    s["seconds"] = float(s["seconds"] or 0)
    s["cost_per_product"] = s["cost_usd"] / n if n else None
    s["seconds_per_product"] = s["seconds"] / n if n else None
    s["run_id"] = run_id
    return s


def render_summary(s: dict) -> str:
    per = f"${s['cost_per_product']:.5f}" if s["cost_per_product"] is not None else "n/a"
    sec = f"{s['seconds_per_product']:.1f}s" if s["seconds_per_product"] is not None else "n/a"
    return (f"run {s['run_id']} ({s['stage']}, {s['model_profile']} = {s['model_id']}): "
            f"{s['succeeded']}/{s['attempted']} products; tokens in {s['input_tokens']:,} "
            f"(+{s['cached_input_tokens']:,} cached, +{s['cache_write_tokens']:,} cache writes), "
            f"out {s['output_tokens']:,}; cost ${s['cost_usd']:.4f}, {per} per product; "
            f"time {s['seconds']:.0f}s, {sec} per product")


# ------------------------------------------------------------------ calls

class RateGate:
    """Shared pause: when the provider signals a rate limit, every worker waits."""

    def __init__(self):
        self._until = 0.0
        self._lock = threading.Lock()

    def wait(self):
        while (delay := self._until - time.monotonic()) > 0:
            time.sleep(min(delay, 5))

    def slow_down(self, seconds: float):
        with self._lock:
            self._until = max(self._until, time.monotonic() + seconds)


class CallFailed(RuntimeError):
    pass


def call_with_retries(provider, request: ModelRequest, gate: RateGate,
                      on_attempt: Callable[[int, ModelResult | None, str | None], None] | None = None,
                      sleep: Callable[[float], None] = time.sleep) -> ModelResult:
    """One model call with the PRD 7 retry rules: up to 3 retries on rate limits and server errors
    with exponential backoff, one retry on a reply that does not match the schema.
    `on_attempt(attempt, result_or_None, error_or_None)` is called after every attempt."""
    transient = schema = attempt = 0
    while True:
        attempt += 1
        gate.wait()
        try:
            result = provider.complete(request)
            validate_reply(request.schema, result.parsed)
        except ProviderError as e:
            if on_attempt:
                on_attempt(attempt, e.result, f"{type(e).__name__}: {e}")
            if isinstance(e, RetryableError) and transient < TRANSIENT_RETRIES:
                delay = e.retry_after or BACKOFF_BASE * 2 ** transient + random.uniform(0, 1)
                transient += 1
                if isinstance(e, RateLimited):
                    gate.slow_down(delay)
                else:
                    sleep(delay)
                continue
            if isinstance(e, SchemaError) and schema < SCHEMA_RETRIES:
                schema += 1
                continue
            raise CallFailed(str(e)) from e
        if on_attempt:
            on_attempt(attempt, result, None)
        return result


def call_model(db: Db, run: Run, provider, request: ModelRequest, product_id: str, gate: RateGate,
               sleep: Callable[[float], None] = time.sleep) -> ModelResult:
    """call_with_retries, logging every attempt to tagging_calls."""
    return call_with_retries(provider, request, gate,
                             lambda attempt, result, error: _log_call(db, run, product_id, request, attempt,
                                                                      result, error=error), sleep)


def _log_call(db: Db, run: Run, product_id: str, request: ModelRequest, attempt: int,
              result: ModelResult | None, error: str | None = None):
    r = result or ModelResult(parsed=None, raw="")
    p = run.profile
    cost = price_tokens(r.input_tokens, r.output_tokens, r.cached_input_tokens, r.cache_write_tokens,
                        p.input_price, p.output_price, p.cached_input_price, p.cache_write_price)
    with db.lock:
        db.conn.execute(
            """insert into tagging_calls (tagging_run_id, product_id, stage, attempt, model_id, served_model,
                   request, reply, parsed, stop_reason, input_tokens, cached_input_tokens, cache_write_tokens,
                   output_tokens, duration_ms, cost_usd, error)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run.run_id, product_id, run.stage, attempt, p.model_id, r.served_model, Jsonb(request.loggable()),
             r.raw or None, Jsonb(r.parsed) if r.parsed is not None else None, r.stop_reason, r.input_tokens,
             r.cached_input_tokens, r.cache_write_tokens, r.output_tokens, r.duration_ms, cost, error))
        db.commit()


def make_provider(profile: Profile):
    return get_provider(profile.provider, profile)


# ------------------------------------------------------------------ products

def done_products(db: Db, run_id: int) -> set[str]:
    return {r[0] for r in db.execute(
        "select product_id from tagging_run_products where tagging_run_id = %s and status = 'done'", (run_id,))}


def record_product(db: Db, run_id: int, product_id: str, status: str, duration_ms: int, error: str | None = None,
                   note: str | None = None, **category):
    cols = ("category_id", "category_runner_up_id", "category_confidence", "category_status", "category_run_id")
    vals = [category.get(c) for c in cols]
    db.conn.execute(
        f"""insert into tagging_run_products (tagging_run_id, product_id, status, error, note, duration_ms,
                {', '.join(cols)}, finished_at)
            values (%s, %s, %s, %s, %s, %s, {', '.join(['%s'] * len(cols))}, now())
            on conflict (tagging_run_id, product_id) do update set status = excluded.status,
                error = excluded.error, note = excluded.note, duration_ms = excluded.duration_ms,
                {', '.join(f'{c} = excluded.{c}' for c in cols)}, finished_at = now()""",
        (run_id, product_id, status, error, note, duration_ms, *vals))


def process(db: Db, run: Run, product_ids: list[str], work: Callable[[str], None], concurrency: int,
            log: Callable[[str], None] = print) -> dict:
    """Run `work` for each product not already done in this run. One failure never stops the run:
    it is recorded as 'failed' and counted as attempted, not succeeded."""
    skip = done_products(db, run.run_id)
    todo = [p for p in dict.fromkeys(product_ids) if p not in skip]
    if skip:
        log(f"run {run.run_id}: {len(skip)} products already done, {len(todo)} to go")
    failed = 0

    def one(pid):
        t0 = time.monotonic()
        try:
            work(pid)
            return pid, None
        except Exception as e:      # noqa: BLE001 - a product's failure is recorded, never raised
            ms = int((time.monotonic() - t0) * 1000)
            with db.lock:
                if db._commit and db.conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
                    db.conn.rollback()
                record_product(db, run.run_id, pid, "failed", ms, error=f"{type(e).__name__}: {e}"[:2000])
                db.commit()
            return pid, e

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(one, pid) for pid in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            pid, err = fut.result()
            if err:
                failed += 1
                log(f"  [{i}/{len(todo)}] {pid} FAILED: {err}")
            else:
                log(f"  [{i}/{len(todo)}] {pid} ok")
    return {"todo": len(todo), "failed": failed, "skipped": len(skip)}
