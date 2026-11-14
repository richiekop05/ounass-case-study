-- 020: the rows behind v_review_outcomes, one per reviewed facet (or category) of an approved product,
-- so the Metrics tab can filter outcomes by run and category with the same definition (Build PRD 11).
-- v_review_outcomes is rebuilt on top of it; its columns and figures are unchanged.

create or replace view v_review_outcome_rows with (security_invoker = true) as
select distinct t.product_id, t.facet_id, t.review_outcome as outcome,
       coalesce(t.review_was_flagged, false) as was_flagged, pr.model_profile,
       rr.parent_run_id as ai_run_id, cp.approved_via
  from catalog_products cp
  join tagging_runs rr on rr.tagging_run_id = cp.current_run_id and rr.stage = 'review'
  join product_facet_tags t on t.tagging_run_id = rr.tagging_run_id and t.product_id = cp.product_id
  left join tagging_runs pr on pr.tagging_run_id = rr.parent_run_id
 where cp.is_published and t.review_outcome is not null
union all
select cp.product_id, 'category', cp.category_review_outcome, coalesce(cp.category_was_flagged, false),
       pr.model_profile, rr.parent_run_id, cp.approved_via
  from catalog_products cp
  join tagging_runs rr on rr.tagging_run_id = cp.current_run_id and rr.stage = 'review'
  left join tagging_runs pr on pr.tagging_run_id = rr.parent_run_id
 where cp.is_published and cp.category_review_outcome is not null;

create or replace view v_review_outcomes with (security_invoker = true) as
select facet_id, model_profile,
       count(*) filter (where was_flagged and outcome <> 'not_reviewed') as reviewed,
       count(*) filter (where was_flagged and outcome = 'approved_unchanged') as approved_unchanged,
       count(*) filter (where was_flagged and outcome in ('changed', 'set_unknown')) as changed,
       count(*) filter (where was_flagged and outcome = 'set_unknown') as set_unknown,
       count(*) filter (where not was_flagged and outcome in ('changed', 'set_unknown')) as changed_though_confident,
       round(count(*) filter (where was_flagged and outcome in ('changed', 'set_unknown'))::numeric
             / nullif(count(*) filter (where was_flagged and outcome <> 'not_reviewed'), 0), 4) as change_rate
  from (select product_id, facet_id, outcome, was_flagged, model_profile from v_review_outcome_rows) r
 group by grouping sets ((facet_id, model_profile), (facet_id), (model_profile), ());
