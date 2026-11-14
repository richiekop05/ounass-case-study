-- Evidence 'rule' is allowed on any taggable facet, not only derived ones.
-- Code writes rule rows for non-derived facets too: the default value when on_missing = 'default'
-- (exclusivity = none, is_set = false) and exclusivity = local_designer from the brands table
-- (facet_definitions.csv v1). PRD v3 section 5 rules are unchanged: derived rows must have evidence
-- 'rule', text_only rows may not use 'image' or 'both', not_tagged and retired facets take no rows.
-- Safe to run more than once.

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

drop view if exists v_invalid_tags;
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
   or (f.source_policy = 'derived' and t.evidence is distinct from 'rule')
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

revoke all on v_invalid_tags from anon, authenticated;
