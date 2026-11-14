-- Exclusive values (13 Nov): any value can be marked exclusive, so it cannot be saved beside another
-- value of the same facet. Generalises facets.none_is_exclusive, which now marks that facet's "none"
-- value. season gains all-season (added by the seed), which is exclusive. Safe to run more than once.
alter table facet_values add column if not exists is_exclusive boolean not null default false;

update facet_values fv set is_exclusive = true
from facets f
where f.facet_id = fv.facet_id and f.none_is_exclusive and fv.value = 'none' and not fv.is_exclusive;

-- v_invalid_tags: 'none_with_other_values' becomes 'exclusive_with_other_values'.
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
select 'exclusive_with_other_values', v.tag_id, v.product_id, v.tagging_run_id, v.facet_id,
       format('%s cannot be combined with other values', fv.label)
from valued v
join facet_values fv on fv.facet_value_id = v.facet_value_id
where fv.is_exclusive and v.n_values > 1
union all
select 'text_without_quote', t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       'auto row with evidence text has no evidence_quote'
from product_facet_tags t
where t.status = 'auto' and t.evidence = 'text' and coalesce(btrim(t.evidence_quote), '') = '';

revoke all on v_invalid_tags from anon, authenticated;
