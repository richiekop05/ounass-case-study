-- 013: category status 'flagged' (13 Nov 2026).
-- Below the category gate, when the top pick and the runner-up have identical facet lists, the top pick is
-- accepted for tagging (the facets are the same either way) and only the category waits for a person.
-- Such a product is tagged, and its category shows in the review queue. Published views stay 'auto' only.

alter table catalog_products drop constraint if exists catalog_products_category_status_check;
alter table catalog_products add constraint catalog_products_category_status_check
    check (category_status in ('auto', 'review', 'flagged'));
alter table tagging_run_products drop constraint if exists tagging_run_products_category_status_check;
alter table tagging_run_products add constraint tagging_run_products_category_status_check
    check (category_status in ('auto', 'review', 'flagged'));

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
    if not exists (select 1 from tagging_runs where tagging_run_id = new.tagging_run_id and stage = 'tag') then
        raise exception 'run % is not a tag run', new.tagging_run_id using errcode = 'check_violation';
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

create or replace view v_review_queue with (security_invoker = true) as
SELECT 'category'::text AS item_type,
    cp.product_id,
    cp.name AS product_name,
    cp.brand,
    cp.category_run_id AS tagging_run_id,
    NULL::bigint AS tag_id,
    NULL::text AS facet_id,
    NULL::text AS facet_name,
    NULL::text AS display_value,
    false AS needs_value,
    NULL::text AS evidence,
    NULL::text AS evidence_quote,
    cp.category_id,
    c.path AS category_path,
    cp.category_runner_up_id,
    cp.category_confidence AS confidence,
    cp.category_reason AS reason
   FROM catalog_products cp
     JOIN categories c ON c.node_id = cp.category_id
  WHERE cp.category_status = ANY (ARRAY['review'::text, 'flagged'::text])
UNION ALL
 SELECT 'tag'::text AS item_type,
    cp.product_id,
    cp.name AS product_name,
    cp.brand,
    t.tagging_run_id,
    t.tag_id,
    t.facet_id,
    f.name AS facet_name,
    COALESCE(fv.label, trim_scale(t.value_number)::text || COALESCE(' '::text || f.unit, ''::text), t.value_text) AS display_value,
    num_nonnulls(t.facet_value_id, t.value_number, t.value_text) = 0 AS needs_value,
    t.evidence,
    t.evidence_quote,
    cp.category_id,
    c.path AS category_path,
    NULL::text AS category_runner_up_id,
    t.confidence,
    t.reason
   FROM catalog_products cp
     JOIN categories c ON c.node_id = cp.category_id
     JOIN product_facet_tags t ON t.product_id = cp.product_id AND t.tagging_run_id = cp.current_run_id
     JOIN facets f ON f.facet_id = t.facet_id
     LEFT JOIN facet_values fv ON fv.facet_value_id = t.facet_value_id
  WHERE t.status = 'review'::text;
