-- 017: the tagger review app (Build PRD v2, sections 3 and 11). Go-ahead given 14 Nov 2026.
-- A reviewer's decisions are stored as a run with stage 'review'; the tagger's own runs and rows are
-- never edited. Confidence levels and the default models move from config files into the database.

-- ---------------------------------------------------------------- runs
alter table tagging_runs drop constraint if exists tagging_runs_stage_check;
alter table tagging_runs add constraint tagging_runs_stage_check check (stage in ('categorise', 'tag', 'review'));
alter table tagging_runs
    add column if not exists product_id       text references products (product_id),
    add column if not exists parent_run_id    bigint references tagging_runs (tagging_run_id),
    add column if not exists taxonomy_version integer;
create index if not exists tagging_runs_review_product on tagging_runs (product_id) where stage = 'review';

-- ---------------------------------------------------------------- catalog products
alter table catalog_products
    add column if not exists approved_at             timestamptz,
    add column if not exists approved_by             text,
    add column if not exists approved_via            text check (approved_via in ('detail', 'list', 'bulk')),
    add column if not exists ai_run_id               bigint references tagging_runs (tagging_run_id),
    add column if not exists category_review_outcome text
        check (category_review_outcome in ('approved_unchanged', 'changed', 'set_unknown', 'not_reviewed')),
    add column if not exists category_was_flagged    boolean,
    add column if not exists category_reviewed_at    timestamptz,
    add column if not exists review_opened_at        timestamptz;

-- ---------------------------------------------------------------- tags
alter table product_facet_tags drop constraint if exists product_facet_tags_evidence_check;
alter table product_facet_tags add constraint product_facet_tags_evidence_check
    check (evidence in ('text', 'image', 'both', 'rule', 'human'));
alter table product_facet_tags
    add column if not exists review_reason      text
        check (review_reason in ('low_confidence', 'quote_not_found', 'missing_required', 'text_policy', 'other')),
    add column if not exists review_outcome     text
        check (review_outcome in ('approved_unchanged', 'changed', 'set_unknown', 'not_reviewed')),
    add column if not exists review_was_flagged boolean;

-- ---------------------------------------------------------------- facets
alter table facets
    add column if not exists review_threshold numeric check (review_threshold between 0 and 1),
    add column if not exists updated_at       timestamptz,
    add column if not exists edited_in_app    boolean not null default false;
alter table facet_values add column if not exists is_active boolean not null default true;

create table if not exists facet_changes (
    change_id  bigserial primary key,
    facet_id   text not null references facets (facet_id),
    changed_at timestamptz not null default now(),
    changed_by text,
    summary    text not null,
    before     jsonb not null,
    after      jsonb not null
);
create index if not exists facet_changes_facet on facet_changes (facet_id, changed_at desc);

-- ---------------------------------------------------------------- settings (one row)
create table if not exists app_settings (
    id                        boolean primary key default true check (id),
    default_review_threshold  numeric not null check (default_review_threshold between 0 and 1),
    category_review_threshold numeric not null check (category_review_threshold between 0 and 1),
    taxonomy_version          integer not null default 1,
    categorise_model          text not null,
    tag_model                 text not null,
    projection_catalog_size   bigint not null default 3000000,
    updated_at                timestamptz not null default now()
);
-- Seeded from config/pipeline.toml and config/facet_policy.csv as they stand on 14 Nov 2026; from now
-- on the database is the only source and those files are not read for levels or default models.
insert into app_settings (default_review_threshold, category_review_threshold, categorise_model, tag_model)
values (0.80, 0.75, 'haiku', 'sonnet') on conflict (id) do nothing;
update facets set review_threshold = auto_threshold
 where auto_threshold is not null and auto_threshold <> 0.80 and review_threshold is null;

-- ---------------------------------------------------------------- triggers allow review runs

CREATE OR REPLACE FUNCTION public.product_facet_tags_check_applicable()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
declare
    f   facets%rowtype;
    cp  record;
begin
    select * into f from facets where facet_id = new.facet_id;
    if f.status <> 'active' or f.source_policy = 'not_tagged' then
        raise exception 'facet % is not taggable (status %, source policy %)',
            new.facet_id, f.status, f.source_policy using errcode = 'check_violation';
    end if;
    if f.source_policy = 'derived' and new.evidence is distinct from 'rule' then
        raise exception 'facet % is derived: rows are written by code with evidence rule', new.facet_id
            using errcode = 'check_violation';
    end if;
    if f.source_policy = 'text_only' and new.evidence in ('image', 'both') then
        raise exception 'facet % is text_only: evidence % is not allowed', new.facet_id, new.evidence
            using errcode = 'check_violation';
    end if;
    if (new.value_number is not null and f.data_type <> 'number')
       or (new.value_text is not null and f.data_type <> 'text')
       or (new.facet_value_id is not null and f.data_type not in ('enum', 'ordinal', 'boolean')) then
        raise exception 'facet % is %, the value is in the wrong column', new.facet_id, f.data_type
            using errcode = 'check_violation';
    end if;
    if not exists (select 1 from tagging_runs where tagging_run_id = new.tagging_run_id and stage in ('tag', 'review')) then
        raise exception 'run % is not a tag or review run', new.tagging_run_id using errcode = 'check_violation';
    end if;
    select category_id, category_status into cp from catalog_products where product_id = new.product_id;
    if cp.category_status not in ('auto', 'flagged') then
        raise exception 'product % has category status %; only auto or flagged categories are tagged',
            new.product_id, cp.category_status using errcode = 'check_violation';
    end if;
    if not exists (select 1 from category_facets
                   where category_id = cp.category_id and facet_id = new.facet_id) then
        raise exception 'facet % does not apply to category % (product %)',
            new.facet_id, cp.category_id, new.product_id using errcode = 'check_violation';
    end if;
    return new;
end $function$;

CREATE OR REPLACE FUNCTION public.catalog_products_before_write()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
begin
    if not exists (select 1 from categories c where c.node_id = new.category_id
                   and c.name not ilike 'other %'
                   and (c.is_leaf or exists (select 1 from category_facets cf
                                             where cf.category_id = c.node_id and cf.inherited_from is null))) then
        raise exception 'category % is not a leaf node or a parent node with its own facets', new.category_id
            using errcode = 'check_violation';
    end if;
    if new.category_run_id is not null and not exists
       (select 1 from tagging_runs where tagging_run_id = new.category_run_id and stage = 'categorise') then
        raise exception 'category_run_id % is not a categorise run', new.category_run_id
            using errcode = 'check_violation';
    end if;
    if new.current_run_id is not null and not exists
       (select 1 from tagging_runs where tagging_run_id = new.current_run_id and stage in ('tag', 'review')) then
        raise exception 'current_run_id % is not a tag or review run', new.current_run_id using errcode = 'check_violation';
    end if;
    new.updated_at := now();
    return new;
end $function$;

-- ---------------------------------------------------------------- backfill (triggers off: history rows)
alter table product_facet_tags disable trigger product_facet_tags_check_applicable;
update product_facet_tags set review_reason = case
        when coalesce(reason, '') ilike '%quote not found%' then 'quote_not_found'
        when num_nonnulls(facet_value_id, value_number, value_text) = 0 then 'missing_required'
        else 'low_confidence' end
 where status = 'review' and review_reason is null;
alter table product_facet_tags enable trigger product_facet_tags_check_applicable;

-- ai_run_id: the latest finished tag run that tagged the product under its current category
update catalog_products cp set ai_run_id = x.run
  from (select distinct on (rp.product_id) rp.product_id, rp.category_id, rp.tagging_run_id as run
          from tagging_run_products rp join tagging_runs r using (tagging_run_id)
         where r.stage = 'tag' and not r.is_drill and rp.status = 'done'
         order by rp.product_id, rp.tagging_run_id desc) x
 where x.product_id = cp.product_id and x.category_id = cp.category_id and cp.ai_run_id is null;
update catalog_products set current_run_id = ai_run_id
 where not is_published and ai_run_id is not null and current_run_id is distinct from ai_run_id;

-- ---------------------------------------------------------------- views
create or replace view v_run_costs with (security_invoker = true) as
SELECT tagging_run_id,
    stage,
    provider,
    model_profile,
    model_id,
    started_at,
    finished_at,
    products_attempted,
    products_succeeded,
    input_tokens,
    cached_input_tokens,
    cache_write_tokens,
    output_tokens,
    cost_usd,
    round(cost_usd / NULLIF(products_succeeded, 0)::numeric, 6) AS cost_per_product,
    round(1000::numeric * cost_usd / NULLIF(products_succeeded, 0)::numeric, 4) AS cost_per_1000_products,
    EXTRACT(epoch FROM finished_at - started_at) AS seconds
   FROM tagging_runs r
  WHERE NOT is_drill AND stage <> 'review'::text;


-- One row per categorised product with its review state (Build PRD 4). Levels are read on every query,
-- so a change on the Confidence tab takes effect at once; stored statuses are a record of the run.
create or replace view v_product_review_state with (security_invoker = true) as
with s as (select default_review_threshold as dlvl, category_review_threshold as clvl from app_settings),
tags as (
    select t.product_id, t.facet_id, t.status, t.confidence, t.review_reason,
           num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0 as has_value,
           coalesce(f.review_threshold, s.dlvl) as lvl,
           (f.default_value is not null and fv.facet_value_id is not null
            and f.default_value in (fv.value, fv.label)) as is_default
      from catalog_products cp
      join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.ai_run_id
      join facets f on f.facet_id = t.facet_id
      left join facet_values fv on fv.facet_value_id = t.facet_value_id
      cross join s
     where f.status = 'active' and f.source_policy in ('text_only', 'text_first', 'judge')
       and t.evidence is distinct from 'rule'
),
per_facet as (
    select product_id, facet_id,
           bool_or((status = 'review' and coalesce(review_reason, 'low_confidence') <> 'low_confidence')
                   or (has_value and not is_default and confidence is not null and confidence < lvl)) as flagged,
           min(confidence) filter (where has_value) as min_conf
      from tags group by product_id, facet_id
),
agg as (
    select product_id, count(*) filter (where flagged) as n_flagged,
           coalesce(array_agg(facet_id order by facet_id) filter (where flagged), '{}') as flagged_facets,
           min(min_conf) as lowest_confidence
      from per_facet group by product_id
),
nf as (
    select cp.product_id, count(*) as n_facets
      from catalog_products cp
      join category_facets cf on cf.category_id = cp.category_id
      join facets f on f.facet_id = cf.facet_id
     where f.status = 'active' and f.source_policy in ('text_only', 'text_first', 'judge')
     group by cp.product_id
)
select cp.product_id, cp.name, cp.brand, cp.price, cp.currency, cp.url, cp.primary_image_path,
       cp.category_id, c.path as category_path, c.audience, c.department,
       cp.category_confidence, cp.category_status, cp.category_runner_up_id,
       (cp.category_reviewed_at is null
        and (cp.category_status = 'review' or cp.category_confidence < s.clvl)) as category_flagged,
       cp.ai_run_id, cp.current_run_id, cp.is_published, cp.approved_at, cp.approved_via,
       coalesce(nf.n_facets, 0) as n_facets, coalesce(agg.n_flagged, 0) as n_flagged,
       coalesce(agg.flagged_facets, '{}') as flagged_facets, agg.lowest_confidence,
       case when cp.is_published then 'approved'
            when cp.ai_run_id is null
              or (cp.category_reviewed_at is null
                  and (cp.category_status = 'review' or cp.category_confidence < s.clvl))
              or coalesce(agg.n_flagged, 0) > 0 then 'needs_review'
            else 'ready' end as state
  from catalog_products cp
  join categories c on c.node_id = cp.category_id
  cross join s
  left join agg on agg.product_id = cp.product_id
  left join nf on nf.product_id = cp.product_id;

-- Review outcomes over approved products (Build PRD 3): per facet and model, and overall.
-- reviewed = flagged answers a person approved, changed or set to unknown; a change to an answer that
-- was not flagged is counted apart as "changed though confident".
create or replace view v_review_outcomes with (security_invoker = true) as
with r as (
    select distinct t.product_id, t.facet_id, t.review_outcome as outcome,
           coalesce(t.review_was_flagged, false) as was_flagged, pr.model_profile
      from catalog_products cp
      join tagging_runs rr on rr.tagging_run_id = cp.current_run_id and rr.stage = 'review'
      join product_facet_tags t on t.tagging_run_id = rr.tagging_run_id and t.product_id = cp.product_id
      left join tagging_runs pr on pr.tagging_run_id = rr.parent_run_id
     where cp.is_published and t.review_outcome is not null
    union all
    select cp.product_id, 'category', cp.category_review_outcome, coalesce(cp.category_was_flagged, false),
           pr.model_profile
      from catalog_products cp
      join tagging_runs rr on rr.tagging_run_id = cp.current_run_id and rr.stage = 'review'
      left join tagging_runs pr on pr.tagging_run_id = rr.parent_run_id
     where cp.is_published and cp.category_review_outcome is not null
)
select facet_id, model_profile,
       count(*) filter (where was_flagged and outcome <> 'not_reviewed') as reviewed,
       count(*) filter (where was_flagged and outcome = 'approved_unchanged') as approved_unchanged,
       count(*) filter (where was_flagged and outcome in ('changed', 'set_unknown')) as changed,
       count(*) filter (where was_flagged and outcome = 'set_unknown') as set_unknown,
       count(*) filter (where not was_flagged and outcome in ('changed', 'set_unknown')) as changed_though_confident,
       round(count(*) filter (where was_flagged and outcome in ('changed', 'set_unknown'))::numeric
             / nullif(count(*) filter (where was_flagged and outcome <> 'not_reviewed'), 0), 4) as change_rate
  from r
 group by grouping sets ((facet_id, model_profile), (facet_id), (model_profile), ());
