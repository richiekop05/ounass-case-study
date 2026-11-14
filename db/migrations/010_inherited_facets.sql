-- Facets for nodes the dictionary does not list (tables PRD, 13 Nov): such a node gets a copy of its
-- nearest ancestor's category_facets rows, made by `catalog seed-taxonomy`. inherited_from holds that
-- ancestor's node ID; it is null for rows that came from the dictionary. Safe to run more than once.
alter table category_facets
    add column if not exists inherited_from text references categories (node_id);
create index if not exists category_facets_inherited_idx on category_facets (inherited_from)
    where inherited_from is not null;
