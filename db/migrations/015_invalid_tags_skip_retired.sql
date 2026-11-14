-- 015: retired facets are archived (14 Nov 2026, shoe_fit_width).
-- Their past answers stay in product_facet_tags and golden_tags on purpose, so v_invalid_tags no longer
-- lists them. New rows for a retired facet are still refused by product_facet_tags_check_applicable.

create or replace view v_invalid_tags with (security_invoker = true) as
with all_rules as (
WITH valued AS (
         SELECT t.tag_id,
            t.product_id,
            t.tagging_run_id,
            t.facet_id,
            t.facet_value_id,
            t.confidence,
            t.evidence,
            t.reason,
            t.status,
            t.created_at,
            t.value_number,
            t.value_text,
            t.evidence_quote,
            count(*) OVER (PARTITION BY t.product_id, t.tagging_run_id, t.facet_id) AS n_values,
            count(*) FILTER (WHERE t.status = 'auto'::text) OVER (PARTITION BY t.product_id, t.tagging_run_id, t.facet_id) AS n_auto
           FROM product_facet_tags t
          WHERE (t.status = ANY (ARRAY['auto'::text, 'review'::text])) AND num_nonnulls(t.facet_value_id, t.value_number, t.value_text) = 1
        )
 SELECT 'facet_not_applicable'::text AS rule,
    t.tag_id,
    t.product_id,
    t.tagging_run_id,
    t.facet_id,
    format('facet %s is not mapped to category %s'::text, t.facet_id,
        CASE
            WHEN t.tagging_run_id = cp.current_run_id OR rp.category_id IS NULL THEN cp.category_id
            ELSE rp.category_id
        END) AS detail
   FROM product_facet_tags t
     JOIN catalog_products cp ON cp.product_id = t.product_id
     LEFT JOIN tagging_run_products rp ON rp.tagging_run_id = t.tagging_run_id AND rp.product_id = t.product_id
  WHERE NOT (EXISTS ( SELECT 1
           FROM category_facets cf
          WHERE cf.category_id =
                CASE
                    WHEN t.tagging_run_id = cp.current_run_id OR rp.category_id IS NULL THEN cp.category_id
                    ELSE rp.category_id
                END AND cf.facet_id = t.facet_id))
UNION ALL
 SELECT 'facet_not_taggable'::text AS rule,
    t.tag_id,
    t.product_id,
    t.tagging_run_id,
    t.facet_id,
    format('facet is %s with source policy %s, evidence %s'::text, f.status, f.source_policy, t.evidence) AS detail
   FROM product_facet_tags t
     JOIN facets f ON f.facet_id = t.facet_id
  WHERE f.status <> 'active'::text OR f.source_policy = 'not_tagged'::text OR f.source_policy = 'derived'::text AND t.evidence IS DISTINCT FROM 'rule'::text OR f.source_policy = 'text_only'::text AND (t.evidence = ANY (ARRAY['image'::text, 'both'::text]))
UNION ALL
 SELECT 'single_facet_multiple_auto'::text AS rule,
    v.tag_id,
    v.product_id,
    v.tagging_run_id,
    v.facet_id,
    format('%s auto values on a single-value facet'::text, v.n_auto) AS detail
   FROM valued v
     JOIN facets f ON f.facet_id = v.facet_id
  WHERE f.cardinality = 'single'::text AND v.status = 'auto'::text AND v.n_auto > 1
UNION ALL
 SELECT 'too_many_values'::text AS rule,
    v.tag_id,
    v.product_id,
    v.tagging_run_id,
    v.facet_id,
    format('%s values, limit %s'::text, v.n_values, f.max_values) AS detail
   FROM valued v
     JOIN facets f ON f.facet_id = v.facet_id
  WHERE f.max_values IS NOT NULL AND v.n_values > f.max_values
UNION ALL
 SELECT 'exclusive_with_other_values'::text AS rule,
    v.tag_id,
    v.product_id,
    v.tagging_run_id,
    v.facet_id,
    format('%s cannot be combined with other values'::text, fv.label) AS detail
   FROM valued v
     JOIN facet_values fv ON fv.facet_value_id = v.facet_value_id
  WHERE fv.is_exclusive AND v.n_values > 1
UNION ALL
 SELECT 'text_without_quote'::text AS rule,
    t.tag_id,
    t.product_id,
    t.tagging_run_id,
    t.facet_id,
    'auto row with evidence text has no evidence_quote'::text AS detail
   FROM product_facet_tags t
  WHERE t.status = 'auto'::text AND t.evidence = 'text'::text AND COALESCE(btrim(t.evidence_quote), ''::text) = ''::text AND NOT (EXISTS ( SELECT 1
           FROM facets f
             JOIN facet_values fv ON fv.facet_id = f.facet_id
          WHERE f.facet_id = t.facet_id AND fv.facet_value_id = t.facet_value_id AND f.default_value IS NOT NULL AND (f.default_value = fv.value OR f.default_value = fv.label)))
)
select r.rule, r.tag_id, r.product_id, r.tagging_run_id, r.facet_id, r.detail
from all_rules r
where not exists (select 1 from facets f where f.facet_id = r.facet_id and f.status = 'retired');
