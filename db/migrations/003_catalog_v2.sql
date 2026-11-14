-- Catalog v2 (PRD version 2): facet rules, number/text tag values, not_applicable status,
-- evidence quotes, richer golden tags, and separate categorise / tag runs.
-- Applied on top of 002_catalog.sql. Safe to run more than once.
-- Like 002, it never changes the scraper's tables.

-- Views are rebuilt at the end (their columns change).
drop view if exists v_catalog_products;
drop view if exists v_product_facets;
drop view if exists v_review_queue;
drop view if exists v_invalid_tags;

-- ===================================================================== facets: rules per facet

alter table facets add column if not exists unit              text;
alter table facets add column if not exists min_values        integer;
alter table facets add column if not exists none_is_exclusive boolean not null default false;
alter table facets add column if not exists source_policy     text;
alter table facets add column if not exists unknown_policy    text;
alter table facets add column if not exists auto_threshold    numeric(3, 2);
alter table facets add column if not exists status            text not null default 'active';
alter table facets alter column definition drop not null;   -- the dictionary has no definitions yet

do $$
begin
    -- v1 called the Register's source_of_truth column `source_of_truth`; v2 calls it source_rule.
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'facets' and column_name = 'source_of_truth') then
        alter table facets rename column source_of_truth to source_rule;
    end if;
    -- v1's is_derived flag becomes source_policy = 'derived' (provisional; seed-taxonomy rewrites it).
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'facets' and column_name = 'is_derived') then
        execute $q$update facets set source_policy = case when is_derived then 'derived' else 'judge' end
                   where source_policy is null$q$;
        alter table facets drop column is_derived;
    end if;
    -- v1 marked the exclusive `none` on the value; v2 marks it on the facet.
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'facet_values' and column_name = 'is_exclusive') then
        execute $q$update facets f set none_is_exclusive = true where exists
                   (select 1 from facet_values v where v.facet_id = f.facet_id and v.is_exclusive)$q$;
        alter table facet_values drop column is_exclusive;
    end if;
end $$;
alter table facets add column if not exists source_rule text;
update facets set data_type = 'ordinal' where data_type like '%ordinal%';
update facets set source_policy = 'judge' where source_policy is null;
alter table facets alter column source_policy set not null;

alter table facets drop constraint if exists facets_data_type_check;
alter table facets add constraint facets_data_type_check
    check (data_type in ('enum', 'ordinal', 'boolean', 'number', 'text'));
alter table facets drop constraint if exists facets_source_policy_check;
alter table facets add constraint facets_source_policy_check
    check (source_policy in ('judge', 'extract_only', 'derived', 'not_tagged'));
alter table facets drop constraint if exists facets_status_check;
alter table facets add constraint facets_status_check check (status in ('active', 'retired'));
alter table facets drop constraint if exists facets_auto_threshold_check;
alter table facets add constraint facets_auto_threshold_check check (auto_threshold between 0 and 1);
alter table facets drop constraint if exists facets_value_limits_check;
alter table facets add constraint facets_value_limits_check
    check (min_values >= 0 and (max_values is null or max_values >= min_values));

-- ===================================================================== runs: two steps

alter table tagging_runs add column if not exists stage text;
update tagging_runs set stage = 'tag' where stage is null;
alter table tagging_runs alter column stage set not null;
alter table tagging_runs drop constraint if exists tagging_runs_stage_check;
alter table tagging_runs add constraint tagging_runs_stage_check check (stage in ('categorise', 'tag'));

alter table catalog_products add column if not exists category_runner_up_id text references categories (node_id);
alter table catalog_products add column if not exists category_run_id bigint references tagging_runs (tagging_run_id);

-- ===================================================================== tags: number/text values

alter table product_facet_tags add column if not exists value_number   numeric;
alter table product_facet_tags add column if not exists value_text     text;
alter table product_facet_tags add column if not exists evidence_quote text;

alter table product_facet_tags drop constraint if exists product_facet_tags_status_check;
alter table product_facet_tags add constraint product_facet_tags_status_check
    check (status in ('auto', 'review', 'abstained', 'not_applicable'));
-- auto / review carry exactly one value; abstained / not_applicable carry none.
alter table product_facet_tags drop constraint if exists product_facet_tags_value_vs_status;
alter table product_facet_tags drop constraint if exists product_facet_tags_value_count;
alter table product_facet_tags add constraint product_facet_tags_value_count check (
    case when status in ('auto', 'review') then num_nonnulls(facet_value_id, value_number, value_text) = 1
         else num_nonnulls(facet_value_id, value_number, value_text) = 0 end);
alter table product_facet_tags drop constraint if exists product_facet_tags_unique;
create unique index if not exists product_facet_tags_unique_value
    on product_facet_tags (product_id, tagging_run_id, facet_id, coalesce(facet_value_id, 0));

-- ===================================================================== golden tags: any answer

alter table golden_tags add column if not exists golden_tag_id bigint generated always as identity;
alter table golden_tags add column if not exists value_number numeric;
alter table golden_tags add column if not exists value_text   text;
alter table golden_tags add column if not exists status       text not null default 'value';
do $$
begin
    if exists (select 1 from pg_constraint where conname = 'golden_tags_pkey'
               and conrelid = 'golden_tags'::regclass and array_length(conkey, 1) > 1) then
        alter table golden_tags drop constraint golden_tags_pkey;
        alter table golden_tags add primary key (golden_tag_id);
    end if;
end $$;
alter table golden_tags alter column facet_value_id drop not null;
alter table golden_tags drop constraint if exists golden_tags_status_check;
alter table golden_tags add constraint golden_tags_status_check
    check (status in ('value', 'unknown', 'not_applicable'));
alter table golden_tags drop constraint if exists golden_tags_value_count;
alter table golden_tags add constraint golden_tags_value_count check (
    case when status = 'value' then num_nonnulls(facet_value_id, value_number, value_text) = 1
         else num_nonnulls(facet_value_id, value_number, value_text) = 0 end);
create unique index if not exists golden_tags_unique_value
    on golden_tags (product_id, facet_id, coalesce(facet_value_id, 0));

-- ===================================================================== triggers

-- catalog_products: leaf category; category_run_id is a categorise run, current_run_id a tag run.
create or replace function catalog_products_before_write() returns trigger
language plpgsql set search_path = public as $$
begin
    if not exists (select 1 from categories where node_id = new.category_id and is_leaf) then
        raise exception 'category % is not a leaf node', new.category_id using errcode = 'check_violation';
    end if;
    if new.category_run_id is not null and not exists
       (select 1 from tagging_runs where tagging_run_id = new.category_run_id and stage = 'categorise') then
        raise exception 'category_run_id % is not a categorise run', new.category_run_id
            using errcode = 'check_violation';
    end if;
    if new.current_run_id is not null and not exists
       (select 1 from tagging_runs where tagging_run_id = new.current_run_id and stage = 'tag') then
        raise exception 'current_run_id % is not a tag run', new.current_run_id using errcode = 'check_violation';
    end if;
    new.updated_at := now();
    return new;
end $$;

-- product_facet_tags: the model may tag this facet, the value column fits the facet's type,
-- the row comes from a tag run, the category is settled, and the facet applies to it.
create or replace function product_facet_tags_check_applicable() returns trigger
language plpgsql set search_path = public as $$
declare
    f   facets%rowtype;
    cp  record;
begin
    select * into f from facets where facet_id = new.facet_id;
    if f.status <> 'active' or f.source_policy not in ('judge', 'extract_only') then
        raise exception 'facet % is not taggable (status %, source policy %)',
            new.facet_id, f.status, f.source_policy using errcode = 'check_violation';
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
       c.audience, c.department, cp.category_confidence, cp.current_run_id, cp.updated_at
from catalog_products cp
join categories c on c.node_id = cp.category_id
where cp.is_published and cp.category_status = 'auto';

-- display_value: the value label, the number (with unit) or the text, whichever is set.
create view v_product_facets with (security_invoker = true) as
select t.product_id, t.facet_id, f.name as facet_name, f.data_type,
       fv.value, fv.label as value_label, t.value_number, f.unit, t.value_text,
       coalesce(fv.label, trim_scale(t.value_number)::text || coalesce(' ' || f.unit, ''), t.value_text)
           as display_value,
       t.confidence, f.sort_order as facet_sort_order, fv.sort_order as value_sort_order,
       f.is_filterable, t.tagging_run_id
from catalog_products cp
join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
join facets f on f.facet_id = t.facet_id
left join facet_values fv on fv.facet_value_id = t.facet_value_id
where cp.is_published and cp.category_status = 'auto' and t.status = 'auto';

create view v_review_queue with (security_invoker = true) as
select 'category'::text as item_type, cp.product_id, cp.name as product_name, cp.brand,
       cp.category_run_id as tagging_run_id, null::bigint as tag_id,
       null::text as facet_id, null::text as facet_name, null::text as display_value,
       null::text as evidence_quote,
       cp.category_id, c.path as category_path, cp.category_runner_up_id,
       cp.category_confidence as confidence, cp.category_reason as reason
from catalog_products cp
join categories c on c.node_id = cp.category_id
where cp.category_status = 'review'
union all
select 'tag', cp.product_id, cp.name, cp.brand, t.tagging_run_id, t.tag_id,
       t.facet_id, f.name,
       coalesce(fv.label, trim_scale(t.value_number)::text || coalesce(' ' || f.unit, ''), t.value_text),
       t.evidence_quote,
       cp.category_id, c.path, null, t.confidence, t.reason
from catalog_products cp
join categories c on c.node_id = cp.category_id
join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
join facets f on f.facet_id = t.facet_id
left join facet_values fv on fv.facet_value_id = t.facet_value_id
where t.status = 'review';

-- Tags that break a rule in PRD section 5. Must return no rows.
create view v_invalid_tags with (security_invoker = true) as
with valued as (   -- tags that carry a value, with counts per product / run / facet
    select t.*,
           count(*) over (partition by t.product_id, t.tagging_run_id, t.facet_id) as n_values,
           count(*) filter (where t.status = 'auto')
               over (partition by t.product_id, t.tagging_run_id, t.facet_id) as n_auto
    from product_facet_tags t
    where t.status in ('auto', 'review')
)
select 'facet_not_applicable'::text as rule, t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       format('facet %s is not mapped to category %s', t.facet_id, cp.category_id) as detail
from product_facet_tags t
join catalog_products cp on cp.product_id = t.product_id
where not exists (select 1 from category_facets cf
                  where cf.category_id = cp.category_id and cf.facet_id = t.facet_id)
union all
select 'facet_not_taggable', t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       format('facet is %s with source policy %s', f.status, f.source_policy)
from product_facet_tags t
join facets f on f.facet_id = t.facet_id
where f.status <> 'active' or f.source_policy not in ('judge', 'extract_only')
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
select 'extract_only_without_quote', t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       'auto tag on an extract-only facet has no evidence_quote'
from product_facet_tags t
join facets f on f.facet_id = t.facet_id
where t.status = 'auto' and f.source_policy = 'extract_only'
  and coalesce(btrim(t.evidence_quote), '') = '';

-- ===================================================================== access
-- Policies from 002 stay. Views were recreated, so grant them again.
revoke all on tagging_runs, golden_products, golden_tags from anon, authenticated;
grant select on v_catalog_products, v_product_facets, v_review_queue to anon, authenticated;
revoke all on v_invalid_tags from anon, authenticated;
