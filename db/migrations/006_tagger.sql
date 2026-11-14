-- Tagging pipeline (PRD "Tagging pipeline for the Ounass case", 13 Nov 2026).
-- Adds: run model/price columns, per-run product ledger, model call log, evaluation results,
-- v_run_costs. golden_products.category_id becomes nullable (sampled products have no label yet).
-- Safe to run more than once.

-- What each run used, recorded when it starts, so later config edits never change a past run's cost.
alter table tagging_runs
    add column if not exists model_profile       text,
    add column if not exists model_id            text,
    add column if not exists input_price         numeric,
    add column if not exists output_price        numeric,
    add column if not exists cached_input_price  numeric,
    add column if not exists cache_write_price   numeric,
    add column if not exists batch_input_price   numeric,
    add column if not exists batch_output_price  numeric,
    add column if not exists cached_input_tokens bigint,
    add column if not exists cache_write_tokens  bigint;

-- One row per product a run finished or gave up on. Resume skips 'done' rows; the category fields
-- record what a categorise run decided, and which category (and categorise run) a tag run tagged from.
create table if not exists tagging_run_products (
    tagging_run_id        bigint not null references tagging_runs (tagging_run_id) on delete cascade,
    product_id            text   not null references products (product_id),
    status                text   not null check (status in ('done', 'failed')),
    category_id           text references categories (node_id),
    category_runner_up_id text references categories (node_id),
    category_confidence   numeric,
    category_status       text check (category_status in ('auto', 'review')),
    category_run_id       bigint references tagging_runs (tagging_run_id),
    error                 text,
    note                  text,
    duration_ms           integer,
    finished_at           timestamptz not null default now(),
    primary key (tagging_run_id, product_id)
);

-- Every model call, retries included. Requests are stored with image storage paths only:
-- no API keys and no base64 image data.
create table if not exists tagging_calls (
    call_id             bigserial primary key,
    tagging_run_id      bigint not null references tagging_runs (tagging_run_id) on delete cascade,
    product_id          text   not null,
    stage               text   not null check (stage in ('categorise', 'tag')),
    attempt             integer not null,
    model_id            text,
    served_model        text,
    request             jsonb  not null,
    reply               text,
    parsed              jsonb,
    stop_reason         text,
    input_tokens        integer not null default 0,
    cached_input_tokens integer not null default 0,
    cache_write_tokens  integer not null default 0,
    output_tokens       integer not null default 0,
    duration_ms         integer,
    cost_usd            numeric not null default 0,
    error               text,
    created_at          timestamptz not null default now()
);
create index if not exists tagging_calls_run_idx on tagging_calls (tagging_run_id);
create index if not exists tagging_calls_product_idx on tagging_calls (product_id);

-- Scores from `tagger evaluate` and figures from `tagger compare`.
-- scope: 'category', 'overall', 'run', a facet_id, or 'evidence:<text|image|both|rule>'.
create table if not exists evaluation_results (
    evaluation_id  bigserial primary key,
    tagging_run_id bigint not null references tagging_runs (tagging_run_id) on delete cascade,
    kind           text   not null check (kind in ('evaluate', 'compare')),
    scope          text   not null,
    metric         text   not null,
    value          numeric,
    product_count  integer,
    detail         jsonb,
    created_at     timestamptz not null default now()
);
create index if not exists evaluation_results_run_idx on evaluation_results (tagging_run_id, kind);

-- A sampled golden product has no category until a person labels it.
alter table golden_products alter column category_id drop not null;

-- One row per run: stage, model, products, tokens and cost. Cost per product divides by the
-- products that succeeded. Read tagging cost by filtering on stage = 'tag'.
drop view if exists v_run_costs;
create view v_run_costs with (security_invoker = true) as
select r.tagging_run_id,
       r.stage,
       r.model_profile,
       r.model_id,
       r.started_at,
       r.finished_at,
       r.products_attempted,
       r.products_succeeded,
       r.input_tokens,
       r.cached_input_tokens,
       r.cache_write_tokens,
       r.output_tokens,
       r.cost_usd,
       round(r.cost_usd / nullif(r.products_succeeded, 0), 6)        as cost_per_product,
       round(1000 * r.cost_usd / nullif(r.products_succeeded, 0), 4) as cost_per_1000_products,
       extract(epoch from (r.finished_at - r.started_at))::numeric   as seconds
from tagging_runs r;

alter table tagging_run_products enable row level security;
alter table tagging_calls enable row level security;
alter table evaluation_results enable row level security;
revoke all on tagging_run_products, tagging_calls, evaluation_results, v_run_costs from anon, authenticated;
