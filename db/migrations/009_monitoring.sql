-- Monitoring: unknown rate and confidence shift (PRD "Tagging pipeline", section 9).
-- Per-run facet stats, a pinned baseline per stage, alerts, drill runs, and views for the front end.
-- Safe to run more than once.

alter table tagging_runs
    add column if not exists is_drill   boolean not null default false,
    add column if not exists drill_name text;

-- One row per run, facet and segment. Categorise runs use facet_id 'category'.
create table if not exists run_facet_stats (
    stat_id                bigserial primary key,
    tagging_run_id         bigint  not null references tagging_runs (tagging_run_id) on delete cascade,
    facet_id               text    not null,
    segment_type           text    not null check (segment_type in ('all', 'department', 'category', 'brand')),
    segment_value          text    not null default '',
    products_applicable    integer not null,
    auto_count             integer not null,
    review_count           integer not null,
    unknown_count          integer not null,
    not_applicable_count   integer not null,
    unknown_rate           numeric,
    review_rate            numeric,
    coverage               numeric,
    values_with_confidence integer not null,
    band_lt_50             integer not null,
    band_50_70             integer not null,
    band_70_80             integer not null,
    band_80_90             integer not null,
    band_90_100            integer not null,
    mean_confidence        numeric,
    median_confidence      numeric,
    created_at             timestamptz not null default now(),
    check (auto_count + review_count + unknown_count + not_applicable_count = products_applicable)
);
create unique index if not exists run_facet_stats_key
    on run_facet_stats (tagging_run_id, facet_id, segment_type, segment_value);

-- The pinned baseline, one row per stage.
create table if not exists monitor_baseline (
    stage          text primary key check (stage in ('categorise', 'tag')),
    tagging_run_id bigint not null references tagging_runs (tagging_run_id) on delete cascade,
    pinned_at      timestamptz not null default now()
);

-- Which baseline each run was last compared with (the views read this).
create table if not exists monitor_runs (
    tagging_run_id  bigint primary key references tagging_runs (tagging_run_id) on delete cascade,
    baseline_run_id bigint references tagging_runs (tagging_run_id) on delete set null,
    facets_checked  integer not null default 0,
    warnings        integer not null default 0,
    alerts          integer not null default 0,
    checked_at      timestamptz not null default now()
);

create table if not exists monitor_alerts (
    alert_id        bigserial primary key,
    tagging_run_id  bigint not null references tagging_runs (tagging_run_id) on delete cascade,
    baseline_run_id bigint not null references tagging_runs (tagging_run_id) on delete cascade,
    facet_id        text   not null,
    segment_type    text   not null,
    segment_value   text   not null default '',
    check_name      text   not null check (check_name in
                        ('unknown_rate', 'review_rate', 'coverage', 'mean_confidence', 'stability_index')),
    baseline_value  numeric,
    current_value   numeric,
    change          numeric,
    severity        text   not null check (severity in ('warning', 'alert')),
    status          text   not null default 'open' check (status in ('open', 'acknowledged')),
    note            text,
    acknowledged_at timestamptz,
    created_at      timestamptz not null default now()
);
create index if not exists monitor_alerts_pair_idx on monitor_alerts (tagging_run_id, baseline_run_id);

-- Run costs leave out drill runs (ask for a drill run by ID in tagging_runs or `tagger compare`).
drop view if exists v_run_costs;
create view v_run_costs with (security_invoker = true) as
select r.tagging_run_id,
       r.stage,
       r.provider,
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
from tagging_runs r
where not r.is_drill;

-- Band counts with half a count added to every band, as the stability index uses them.
create or replace function monitor_band_shares(s run_facet_stats) returns numeric[]
language sql immutable set search_path = public as $$
    select array[s.band_lt_50 + 0.5, s.band_50_70 + 0.5, s.band_70_80 + 0.5, s.band_80_90 + 0.5,
                 s.band_90_100 + 0.5]::numeric[]
$$;

drop view if exists v_monitor_latest;
create view v_monitor_latest with (security_invoker = true) as
with latest as (
    select m.tagging_run_id, m.baseline_run_id
    from monitor_runs m join tagging_runs r on r.tagging_run_id = m.tagging_run_id
    where r.stage = 'tag' and not r.is_drill
    order by r.started_at desc, r.tagging_run_id desc
    limit 1
), pairs as (
    select l.tagging_run_id, l.baseline_run_id, c.facet_id, c as cur, b as base
    from latest l
    join run_facet_stats c on c.tagging_run_id = l.tagging_run_id and c.segment_type = 'all'
    left join run_facet_stats b on b.tagging_run_id = l.baseline_run_id and b.segment_type = 'all'
                               and b.facet_id = c.facet_id
)
select p.tagging_run_id, p.baseline_run_id, p.facet_id,
       (p.cur).products_applicable as products_applicable,
       (p.base).unknown_rate as baseline_unknown_rate, (p.cur).unknown_rate as unknown_rate,
       (p.base).review_rate as baseline_review_rate, (p.cur).review_rate as review_rate,
       (p.base).coverage as baseline_coverage, (p.cur).coverage as coverage,
       (p.base).mean_confidence as baseline_mean_confidence, (p.cur).mean_confidence as mean_confidence,
       case when (p.base).facet_id is null then null else (
           select round(sum((c.v / ct.t - b.v / bt.t) * ln((c.v / ct.t) / (b.v / bt.t))), 4)
           from unnest(monitor_band_shares(p.cur)) with ordinality c(v, i)
           join unnest(monitor_band_shares(p.base)) with ordinality b(v, i) using (i)
           cross join (select sum(x) t from unnest(monitor_band_shares(p.cur)) x) ct
           cross join (select sum(x) t from unnest(monitor_band_shares(p.base)) x) bt) end as stability_index,
       (select case when bool_or(a.severity = 'alert') then 'alert'
                    when bool_or(a.severity = 'warning') then 'warning' end
        from monitor_alerts a where a.tagging_run_id = p.tagging_run_id
          and a.baseline_run_id = p.baseline_run_id and a.facet_id = p.facet_id) as worst_severity
from pairs p;

drop view if exists v_facet_trends;
create view v_facet_trends with (security_invoker = true) as
select s.facet_id, r.stage, r.tagging_run_id, r.started_at, r.model_profile, s.products_applicable,
       s.unknown_rate, s.review_rate, s.coverage, s.mean_confidence, s.median_confidence,
       s.values_with_confidence
from run_facet_stats s join tagging_runs r using (tagging_run_id)
where s.segment_type = 'all' and not r.is_drill
order by s.facet_id, r.started_at, r.tagging_run_id;

drop view if exists v_confidence_bands;
create view v_confidence_bands with (security_invoker = true) as
select m.tagging_run_id, m.baseline_run_id, s.facet_id,
       case when s.tagging_run_id = m.tagging_run_id then 'current' else 'baseline' end as role,
       s.values_with_confidence,
       round(s.band_lt_50::numeric / nullif(s.values_with_confidence, 0), 4)  as share_lt_50,
       round(s.band_50_70::numeric / nullif(s.values_with_confidence, 0), 4)  as share_50_70,
       round(s.band_70_80::numeric / nullif(s.values_with_confidence, 0), 4)  as share_70_80,
       round(s.band_80_90::numeric / nullif(s.values_with_confidence, 0), 4)  as share_80_90,
       round(s.band_90_100::numeric / nullif(s.values_with_confidence, 0), 4) as share_90_100
from monitor_runs m
join run_facet_stats s on s.segment_type = 'all'
                      and s.tagging_run_id in (m.tagging_run_id, m.baseline_run_id);

drop view if exists v_open_alerts;
create view v_open_alerts with (security_invoker = true) as
select a.alert_id, a.tagging_run_id, a.baseline_run_id, r.stage, r.is_drill, r.drill_name, a.facet_id,
       a.segment_type, a.segment_value, a.check_name, a.baseline_value, a.current_value, a.change,
       a.severity, a.created_at
from monitor_alerts a join tagging_runs r using (tagging_run_id)
where a.status = 'open'
order by (a.severity = 'alert') desc, abs(a.change) desc nulls last, a.alert_id;

alter table run_facet_stats enable row level security;
alter table monitor_baseline enable row level security;
alter table monitor_runs enable row level security;
alter table monitor_alerts enable row level security;
revoke all on run_facet_stats, monitor_baseline, monitor_runs, monitor_alerts, v_run_costs, v_monitor_latest,
    v_facet_trends, v_confidence_bands, v_open_alerts from anon, authenticated;
