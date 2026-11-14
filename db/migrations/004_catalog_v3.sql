-- Catalog v3 (PRD version 3, facet review of 12 Nov 2026): five source policies, on_missing,
-- rubric definitions, brands, launch dates, and derived facets written by code (evidence 'rule').
-- Applied on top of 003_catalog_v2.sql. Safe to run more than once. Never changes the scraper's tables.

drop view if exists v_catalog_products;
drop view if exists v_product_facets;
drop view if exists v_review_queue;
drop view if exists v_invalid_tags;

-- ===================================================================== facets

alter table facets add column if not exists on_missing         text;
alter table facets add column if not exists default_value      text;   -- slug written when on_missing = 'default'
alter table facets add column if not exists definition_version text;   -- version label of facet_definitions.csv
alter table facet_values add column if not exists definition_version text;

-- Provisional values for rows seeded under v2; `catalog seed-taxonomy` rewrites both from
-- config/facet_policy.csv straight after this migration.
alter table facets drop constraint if exists facets_source_policy_check;
update facets set source_policy = 'text_only' where source_policy = 'extract_only';
update facets set on_missing = 'unknown' where on_missing is null;
alter table facets alter column on_missing set not null;

alter table facets add constraint facets_source_policy_check
    check (source_policy in ('text_only', 'text_first', 'judge', 'derived', 'not_tagged'));
alter table facets drop constraint if exists facets_on_missing_check;
alter table facets add constraint facets_on_missing_check
    check (on_missing in ('unknown', 'review_if_required', 'default'));
alter table facets drop constraint if exists facets_default_value_check;
alter table facets add constraint facets_default_value_check
    check (on_missing <> 'default' or default_value is not null);

-- ===================================================================== brands

create table if not exists brands (
    brand             text primary key,           -- exactly as the scraper stored it
    brand_tier        text check (brand_tier in ('contemporary', 'designer', 'luxury', 'ultra-luxury')),
    is_local_designer boolean not null default false,
    created_at        timestamptz not null default now()
);
alter table brands enable row level security;
revoke all on brands from anon, authenticated;   -- not behind any website view

-- ===================================================================== runs and catalog

alter table tagging_runs add column if not exists rubric_version text;

alter table catalog_products add column if not exists launch_date          date;
alter table catalog_products add column if not exists launch_date_is_dummy boolean not null default false;
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'catalog_products_brand_fkey') then
        alter table catalog_products add constraint catalog_products_brand_fkey
            foreign key (brand) references brands (brand);
    end if;
end $$;

-- ===================================================================== tags

alter table product_facet_tags drop constraint if exists product_facet_tags_evidence_check;
alter table product_facet_tags add constraint product_facet_tags_evidence_check
    check (evidence in ('text', 'image', 'both', 'rule'));
-- auto: exactly one value; review: at most one (none = a person must supply it);
-- abstained / not_applicable: none.
alter table product_facet_tags drop constraint if exists product_facet_tags_value_count;
alter table product_facet_tags add constraint product_facet_tags_value_count check (
    case status
        when 'auto'   then num_nonnulls(facet_value_id, value_number, value_text) = 1
        when 'review' then num_nonnulls(facet_value_id, value_number, value_text) <= 1
        else num_nonnulls(facet_value_id, value_number, value_text) = 0
    end);
alter table product_facet_tags drop constraint if exists product_facet_tags_rule_confidence;
alter table product_facet_tags add constraint product_facet_tags_rule_confidence
    check (evidence is distinct from 'rule' or confidence is null);

-- Who may write what (PRD v3 section 5), value column vs data type, tag run, settled category,
-- and applicability.
create or replace function product_facet_tags_check_applicable() returns trigger
language plpgsql set search_path = public as $$
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
    if f.source_policy <> 'derived' and new.evidence = 'rule' then
        raise exception 'facet % is %, evidence rule is only for derived facets', new.facet_id, f.source_policy
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
    if not exists (select 1 from tagging_runs where tagging_run_id = new.tagging_run_id and stage = 'tag') then
        raise exception 'run % is not a tag run', new.tagging_run_id using errcode = 'check_violation';
    end if;
    select category_id, category_status into cp from catalog_products where product_id = new.product_id;
    if cp.category_status <> 'auto' then
        raise exception 'product % has category status %; only auto categories are tagged',
            new.product_id, cp.category_status using errcode = 'check_violation';
    end if;
    if not exists (select 1 from category_facets
                   where category_id = cp.category_id and facet_id = new.facet_id) then
        raise exception 'facet % does not apply to category % (product %)',
            new.facet_id, cp.category_id, new.product_id using errcode = 'check_violation';
    end if;
    return new;
end $$;

-- ===================================================================== views

create view v_catalog_products with (security_invoker = true) as
select cp.product_id, cp.name, cp.brand, cp.price, cp.original_price, cp.currency, cp.url,
       cp.description, cp.details, cp.size_and_fit, cp.primary_image_path,
       cp.category_id, c.name as category_name, c.path as category_path,
       case when c.is_leaf then c.slug end as product_type,
       c.audience, c.department, cp.category_confidence, cp.launch_date,
       cp.current_run_id, cp.updated_at
from catalog_products cp
join categories c on c.node_id = cp.category_id
where cp.is_published and cp.category_status = 'auto';

create view v_product_facets with (security_invoker = true) as
select t.product_id, t.facet_id, f.name as facet_name, f.data_type,
       fv.value, fv.label as value_label, t.value_number, f.unit, t.value_text,
       coalesce(fv.label, trim_scale(t.value_number)::text || coalesce(' ' || f.unit, ''), t.value_text)
           as display_value,
       t.confidence, t.evidence, f.sort_order as facet_sort_order, fv.sort_order as value_sort_order,
       f.is_filterable, t.tagging_run_id
from catalog_products cp
join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
join facets f on f.facet_id = t.facet_id
left join facet_values fv on fv.facet_value_id = t.facet_value_id
where cp.is_published and cp.category_status = 'auto' and t.status = 'auto';

-- needs_value: a review row with no value, which a person must fill in.
create view v_review_queue with (security_invoker = true) as
select 'category'::text as item_type, cp.product_id, cp.name as product_name, cp.brand,
       cp.category_run_id as tagging_run_id, null::bigint as tag_id,
       null::text as facet_id, null::text as facet_name, null::text as display_value,
       false as needs_value, null::text as evidence, null::text as evidence_quote,
       cp.category_id, c.path as category_path, cp.category_runner_up_id,
       cp.category_confidence as confidence, cp.category_reason as reason
from catalog_products cp
join categories c on c.node_id = cp.category_id
where cp.category_status = 'review'
union all
select 'tag', cp.product_id, cp.name, cp.brand, t.tagging_run_id, t.tag_id,
       t.facet_id, f.name,
       coalesce(fv.label, trim_scale(t.value_number)::text || coalesce(' ' || f.unit, ''), t.value_text),
       num_nonnulls(t.facet_value_id, t.value_number, t.value_text) = 0,
       t.evidence, t.evidence_quote,
       cp.category_id, c.path, null, t.confidence, t.reason
from catalog_products cp
join categories c on c.node_id = cp.category_id
join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
join facets f on f.facet_id = t.facet_id
left join facet_values fv on fv.facet_value_id = t.facet_value_id
where t.status = 'review';

create view v_invalid_tags with (security_invoker = true) as
with valued as (   -- tags that carry a value, with counts per product / run / facet
    select t.*,
           count(*) over (partition by t.product_id, t.tagging_run_id, t.facet_id) as n_values,
           count(*) filter (where t.status = 'auto')
               over (partition by t.product_id, t.tagging_run_id, t.facet_id) as n_auto
    from product_facet_tags t
    where t.status in ('auto', 'review')
      and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) = 1
)
select 'facet_not_applicable'::text as rule, t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       format('facet %s is not mapped to category %s', t.facet_id, cp.category_id) as detail
from product_facet_tags t
join catalog_products cp on cp.product_id = t.product_id
where not exists (select 1 from category_facets cf
                  where cf.category_id = cp.category_id and cf.facet_id = t.facet_id)
union all
select 'facet_not_taggable', t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       format('facet is %s with source policy %s, evidence %s', f.status, f.source_policy, t.evidence)
from product_facet_tags t
join facets f on f.facet_id = t.facet_id
where f.status <> 'active' or f.source_policy = 'not_tagged'
   or (f.source_policy = 'derived') <> (t.evidence is not distinct from 'rule')
   or (f.source_policy = 'text_only' and t.evidence in ('image', 'both'))
union all
select 'single_facet_multiple_auto', v.tag_id, v.product_id, v.tagging_run_id, v.facet_id,
       format('%s auto values on a single-value facet', v.n_auto)
from valued v
join facets f on f.facet_id = v.facet_id
where f.cardinality = 'single' and v.status = 'auto' and v.n_auto > 1
union all
select 'too_many_values', v.tag_id, v.product_id, v.tagging_run_id, v.facet_id,
       format('%s values, limit %s', v.n_values, f.max_values)
from valued v
join facets f on f.facet_id = v.facet_id
where f.max_values is not null and v.n_values > f.max_values
union all
select 'none_with_other_values', v.tag_id, v.product_id, v.tagging_run_id, v.facet_id,
       'none cannot be combined with other values'
from valued v
join facets f on f.facet_id = v.facet_id
join facet_values fv on fv.facet_value_id = v.facet_value_id
where f.none_is_exclusive and fv.value = 'none' and v.n_values > 1
union all
select 'text_without_quote', t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       'auto row with evidence text has no evidence_quote'
from product_facet_tags t
where t.status = 'auto' and t.evidence = 'text' and coalesce(btrim(t.evidence_quote), '') = '';

-- ===================================================================== access
revoke all on tagging_runs, golden_products, golden_tags, brands from anon, authenticated;
grant select on v_catalog_products, v_product_facets, v_review_queue to anon, authenticated;
revoke all on v_invalid_tags from anon, authenticated;
