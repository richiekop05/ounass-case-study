-- 018: one definition of "flagged" (Build PRD 4), shared by the product state view, the detail page and
-- the Confidence tab. Compares each model-written answer with the level in force now.
create or replace view v_tag_flags with (security_invoker = true) as
with s as (select default_review_threshold as dlvl from app_settings),
t as (
    select t.product_id, t.tagging_run_id, t.facet_id, t.status, t.confidence, t.review_reason,
           num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0 as has_value,
           coalesce(f.review_threshold, s.dlvl) as lvl,
           (f.default_value is not null and fv.facet_value_id is not null
            and f.default_value in (fv.value, fv.label)) as is_default
      from product_facet_tags t
      join facets f on f.facet_id = t.facet_id
      left join facet_values fv on fv.facet_value_id = t.facet_value_id
      cross join s
     where f.status = 'active' and f.source_policy in ('text_only', 'text_first', 'judge')
       and t.evidence is distinct from 'rule'
)
select product_id, tagging_run_id, facet_id, max(lvl) as level,
       min(confidence) filter (where has_value) as min_confidence,
       bool_or((status = 'review' and coalesce(review_reason, 'low_confidence') <> 'low_confidence')
               or (has_value and not is_default and confidence is not null and confidence < lvl)) as flagged,
       coalesce(max(review_reason) filter (where status = 'review'
                                            and coalesce(review_reason, 'low_confidence') <> 'low_confidence'),
                case when bool_or(has_value and not is_default and confidence is not null and confidence < lvl)
                     then 'low_confidence' end) as flag_reason
  from t
 group by product_id, tagging_run_id, facet_id;

create or replace view v_product_review_state with (security_invoker = true) as
with s as (select category_review_threshold as clvl from app_settings),
agg as (
    select g.product_id, count(*) filter (where g.flagged) as n_flagged,
           coalesce(array_agg(g.facet_id order by g.facet_id) filter (where g.flagged), '{}') as flagged_facets,
           min(g.min_confidence) as lowest_confidence
      from catalog_products cp
      join v_tag_flags g on g.product_id = cp.product_id and g.tagging_run_id = cp.ai_run_id
     group by g.product_id
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
