-- 012: a product may sit on a parent category when no sub-category fits (13 Nov 2026).
-- The categorising skill already says "if no sub-category fits, choose the parent"; until now
-- catalog_products accepted leaf nodes only, so the parent could never be chosen.
-- A parent is accepted when the facet dictionary lists facets for it directly (not inherited), so a
-- product placed there gets that node's own facet list. Holding nodes ("Other ...") stay excluded.

create or replace function catalog_products_before_write() returns trigger
language plpgsql set search_path = public as $$
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
       (select 1 from tagging_runs where tagging_run_id = new.current_run_id and stage = 'tag') then
        raise exception 'current_run_id % is not a tag run', new.current_run_id using errcode = 'check_violation';
    end if;
    new.updated_at := now();
    return new;
end $$;
