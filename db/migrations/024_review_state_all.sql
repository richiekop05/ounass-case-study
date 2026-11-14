-- 024: v_product_review_state_all is the state of every catalog product (migration 019's definition), for the
-- approval code, which also publishes Richie's store samples. v_product_review_state, which the tagger app reads,
-- is the same minus store samples (as in 023). Same columns as before.

create or replace view v_product_review_state_all with (security_invoker = true) as
with s as (select category_review_threshold as clvl from app_settings),
rr as (   -- the product's latest review run while it is not approved
    select distinct on (r.product_id) r.product_id, r.tagging_run_id
      from tagging_runs r join catalog_products cp on cp.product_id = r.product_id and not cp.is_published
     where r.stage = 'review'
     order by r.product_id, r.tagging_run_id desc
),
decided as (
    select distinct t.product_id, t.facet_id
      from rr join product_facet_tags t on t.tagging_run_id = rr.tagging_run_id and t.product_id = rr.product_id
     where t.review_outcome is distinct from 'not_reviewed'
),
agg as (
    select g.product_id,
           count(*) filter (where g.flagged and d.facet_id is null) as n_flagged,
           count(*) filter (where g.flagged and d.facet_id is not null) as n_resolved,
           coalesce(array_agg(g.facet_id order by g.facet_id) filter (where g.flagged and d.facet_id is null), '{}')
               as flagged_facets,
           min(g.min_confidence) as lowest_confidence
      from catalog_products cp
      join v_tag_flags g on g.product_id = cp.product_id and g.tagging_run_id = cp.ai_run_id
      left join decided d on d.product_id = g.product_id and d.facet_id = g.facet_id
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
            else 'ready' end as state,
       coalesce(agg.n_resolved, 0) as n_resolved
  from catalog_products cp
  join categories c on c.node_id = cp.category_id
  cross join s
  left join agg on agg.product_id = cp.product_id
  left join nf on nf.product_id = cp.product_id;

create or replace view v_product_review_state with (security_invoker = true) as
select s.* from v_product_review_state_all s
 where not exists (select 1 from catalog_products c where c.product_id = s.product_id and c.source = 'sample');
