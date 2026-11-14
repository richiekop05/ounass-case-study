-- Catalog layer: facet taxonomy, the tagging agent's output, and the answer key.
-- Safe to run more than once. Reads the scraper's tables (001) and never changes their
-- columns or rows. The one addition on a scraper table is a read-only RLS policy on
-- `categories`, so the website can show category names and paths (PRD section 2).

-- ===================================================================== taxonomy

create table if not exists facets (
    facet_id        text primary key,
    name            text not null,
    definition      text not null,
    cardinality     text not null check (cardinality in ('single', 'multi')),
    kind            text not null check (kind in ('objective', 'subjective')),
    is_filterable   boolean not null default true,
    sort_order      integer,
    -- extra columns carried over from the facet dictionary
    data_type       text not null,                         -- enum, enum (ordinal), boolean
    max_values      integer check (max_values >= 1),       -- multi (1-3) -> 3; null = no limit
    is_derived      boolean not null default false,        -- computed by rule, never tagged
    facet_group     text,
    source_of_truth text,
    created_at      timestamptz not null default now()
);

create table if not exists facet_values (
    facet_value_id bigint generated always as identity primary key,
    facet_id       text not null references facets (facet_id),
    value          text not null check (value ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
    label          text not null,
    definition     text,
    sort_order     integer,
    is_exclusive   boolean not null default false,          -- e.g. `none`: cannot sit with other values
    created_at     timestamptz not null default now(),
    unique (facet_id, value),
    unique (facet_id, facet_value_id)
);

create table if not exists category_facets (
    category_id text not null references categories (node_id),
    facet_id    text not null references facets (facet_id),
    is_required boolean not null default false,
    created_at  timestamptz not null default now(),
    primary key (category_id, facet_id)
);
create index if not exists category_facets_facet_idx on category_facets (facet_id);

-- ===================================================================== catalog

create table if not exists tagging_runs (
    tagging_run_id     bigint generated always as identity primary key,
    tagger_version     text not null,
    model              text not null,
    instructions_hash  text,
    started_at         timestamptz,
    finished_at        timestamptz,
    products_attempted integer,
    products_succeeded integer,
    input_tokens       bigint,
    output_tokens      bigint,
    cost_usd           numeric(10, 4),
    notes              text,
    created_at         timestamptz not null default now()
);

create table if not exists catalog_products (
    product_id          text primary key references products (product_id),
    category_id         text not null references categories (node_id),
    category_confidence numeric(3, 2) check (category_confidence between 0 and 1),
    category_reason     text,
    category_status     text not null check (category_status in ('auto', 'review')),
    -- display fields, copied from products (same column names) so a re-scrape cannot change the demo
    name                text not null,
    brand               text not null,
    price               numeric(12, 2) not null,
    original_price      numeric(12, 2),
    currency            text not null,
    url                 text not null,
    description         text,            -- the site's "Editor's advice" (editor's notes)
    details             jsonb not null default '[]'::jsonb,
    size_and_fit        jsonb not null default '[]'::jsonb,
    primary_image_path  text,            -- path in the private product-images bucket
    current_run_id      bigint references tagging_runs (tagging_run_id),
    is_published        boolean not null default false,
    updated_at          timestamptz not null default now(),
    created_at          timestamptz not null default now()
);
create index if not exists catalog_products_category_idx on catalog_products (category_id);

create table if not exists product_facet_tags (
    tag_id         bigint generated always as identity primary key,
    product_id     text not null references catalog_products (product_id) on delete cascade,
    tagging_run_id bigint not null references tagging_runs (tagging_run_id),
    facet_id       text not null references facets (facet_id),
    facet_value_id bigint,
    confidence     numeric(3, 2) check (confidence between 0 and 1),
    evidence       text check (evidence in ('image', 'text', 'both')),
    reason         text,
    status         text not null check (status in ('auto', 'review', 'abstained')),
    created_at     timestamptz not null default now(),
    -- a value is required unless the agent abstained, and an abstention carries no value
    constraint product_facet_tags_value_vs_status
        check ((status = 'abstained') = (facet_value_id is null)),
    -- the value must belong to this facet
    constraint product_facet_tags_value_fk
        foreign key (facet_id, facet_value_id) references facet_values (facet_id, facet_value_id),
    constraint product_facet_tags_unique
        unique nulls not distinct (product_id, tagging_run_id, facet_id, facet_value_id)
);
create index if not exists product_facet_tags_value_idx on product_facet_tags (facet_id, facet_value_id);
create index if not exists product_facet_tags_product_idx on product_facet_tags (product_id);

-- ===================================================================== answer key

create table if not exists golden_products (
    product_id  text primary key references products (product_id),
    category_id text not null references categories (node_id),
    labelled_by text,
    notes       text,
    created_at  timestamptz not null default now()
);

create table if not exists golden_tags (
    product_id     text not null references golden_products (product_id) on delete cascade,
    facet_id       text not null references facets (facet_id),
    facet_value_id bigint not null,
    created_at     timestamptz not null default now(),
    primary key (product_id, facet_id, facet_value_id),
    constraint golden_tags_value_fk
        foreign key (facet_id, facet_value_id) references facet_values (facet_id, facet_value_id)
);

-- ===================================================================== triggers

-- catalog_products.category_id must be a leaf; keep updated_at current.
create or replace function catalog_products_before_write() returns trigger
language plpgsql set search_path = public as $$
begin
    if not exists (select 1 from categories where node_id = new.category_id and is_leaf) then
        raise exception 'category % is not a leaf node', new.category_id
            using errcode = 'check_violation';
    end if;
    new.updated_at := now();
    return new;
end $$;

drop trigger if exists catalog_products_before_write on catalog_products;
create trigger catalog_products_before_write
    before insert or update on catalog_products
    for each row execute function catalog_products_before_write();

-- A tag's facet must apply to the product's category (section 5, applicability).
create or replace function product_facet_tags_check_applicable() returns trigger
language plpgsql set search_path = public as $$
declare
    cat text;
begin
    select category_id into cat from catalog_products where product_id = new.product_id;
    if not exists (select 1 from category_facets
                   where category_id = cat and facet_id = new.facet_id) then
        raise exception 'facet % does not apply to category % (product %)',
            new.facet_id, cat, new.product_id
            using errcode = 'check_violation';
    end if;
    return new;
end $$;

drop trigger if exists product_facet_tags_check_applicable on product_facet_tags;
create trigger product_facet_tags_check_applicable
    before insert or update on product_facet_tags
    for each row execute function product_facet_tags_check_applicable();

-- ===================================================================== views
-- security_invoker: each view obeys the RLS of the person reading it.

-- Listing and product pages: published products with their category.
create or replace view v_catalog_products with (security_invoker = true) as
select cp.product_id, cp.name, cp.brand, cp.price, cp.original_price, cp.currency, cp.url,
       cp.description, cp.details, cp.size_and_fit, cp.primary_image_path,
       cp.category_id, c.name as category_name, c.path as category_path, c.slug as product_type,
       c.audience, c.department, cp.category_confidence, cp.current_run_id, cp.updated_at
from catalog_products cp
join categories c on c.node_id = cp.category_id
where cp.is_published and cp.category_status = 'auto';

-- Filters and the product page: auto tags from each published product's current run.
create or replace view v_product_facets with (security_invoker = true) as
select t.product_id, t.facet_id, f.name as facet_name, fv.value, fv.label as value_label,
       t.confidence, f.sort_order as facet_sort_order, fv.sort_order as value_sort_order,
       f.is_filterable, t.tagging_run_id
from catalog_products cp
join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
join facets f on f.facet_id = t.facet_id
join facet_values fv on fv.facet_value_id = t.facet_value_id
where cp.is_published and cp.category_status = 'auto' and t.status = 'auto';

-- The review pile: category picks and tags with status review, from each product's current run.
create or replace view v_review_queue with (security_invoker = true) as
select 'category'::text as item_type, cp.product_id, cp.name as product_name, cp.brand,
       cp.current_run_id as tagging_run_id, null::bigint as tag_id,
       null::text as facet_id, null::text as facet_name, null::text as value, null::text as value_label,
       cp.category_id, c.path as category_path, cp.category_confidence as confidence,
       cp.category_reason as reason
from catalog_products cp
join categories c on c.node_id = cp.category_id
where cp.category_status = 'review'
union all
select 'tag', cp.product_id, cp.name, cp.brand, t.tagging_run_id, t.tag_id,
       t.facet_id, f.name, fv.value, fv.label,
       cp.category_id, c.path, t.confidence, t.reason
from catalog_products cp
join categories c on c.node_id = cp.category_id
join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
join facets f on f.facet_id = t.facet_id
join facet_values fv on fv.facet_value_id = t.facet_value_id
where t.status = 'review';

-- Tags that break a rule. Must return no rows.
create or replace view v_invalid_tags with (security_invoker = true) as
with auto_counts as (
    select t.*, count(*) over (partition by t.product_id, t.tagging_run_id, t.facet_id) as n_auto
    from product_facet_tags t
    where t.status = 'auto'
)
select 'facet_not_applicable'::text as rule, t.tag_id, t.product_id, t.tagging_run_id, t.facet_id,
       format('facet %s is not mapped to category %s', t.facet_id, cp.category_id) as detail
from product_facet_tags t
join catalog_products cp on cp.product_id = t.product_id
where not exists (select 1 from category_facets cf
                  where cf.category_id = cp.category_id and cf.facet_id = t.facet_id)
union all
select 'single_facet_multiple_auto', a.tag_id, a.product_id, a.tagging_run_id, a.facet_id,
       format('%s auto values on a single-value facet', a.n_auto)
from auto_counts a
join facets f on f.facet_id = a.facet_id
where f.cardinality = 'single' and a.n_auto > 1
union all
select 'too_many_values', a.tag_id, a.product_id, a.tagging_run_id, a.facet_id,
       format('%s auto values, limit %s', a.n_auto, f.max_values)
from auto_counts a
join facets f on f.facet_id = a.facet_id
where f.cardinality = 'multi' and f.max_values is not null and a.n_auto > f.max_values
union all
select 'exclusive_value_with_others', a.tag_id, a.product_id, a.tagging_run_id, a.facet_id,
       format('%s cannot be combined with other values', fv.value)
from auto_counts a
join facet_values fv on fv.facet_value_id = a.facet_value_id
where fv.is_exclusive and a.n_auto > 1;

-- ===================================================================== access
-- RLS on every new table. The website's public (anon) key reads the catalog and
-- taxonomy tables only; raw, run and answer-key tables stay service-key only.

alter table facets             enable row level security;
alter table facet_values       enable row level security;
alter table category_facets    enable row level security;
alter table tagging_runs       enable row level security;
alter table catalog_products   enable row level security;
alter table product_facet_tags enable row level security;
alter table golden_products    enable row level security;
alter table golden_tags        enable row level security;

drop policy if exists public_read on facets;
create policy public_read on facets for select to anon, authenticated using (true);
drop policy if exists public_read on facet_values;
create policy public_read on facet_values for select to anon, authenticated using (true);
drop policy if exists public_read on category_facets;
create policy public_read on category_facets for select to anon, authenticated using (true);
drop policy if exists public_read on catalog_products;
create policy public_read on catalog_products for select to anon, authenticated using (true);
drop policy if exists public_read on product_facet_tags;
create policy public_read on product_facet_tags for select to anon, authenticated using (true);
-- Read-only policy on the scraper's categories table (taxonomy layer, read by the website).
drop policy if exists public_read on categories;
create policy public_read on categories for select to anon, authenticated using (true);

-- Belt and braces on top of RLS: the public roles never write, and never touch run or answer-key tables.
revoke insert, update, delete, truncate on facets, facet_values, category_facets,
    catalog_products, product_facet_tags from anon, authenticated;
revoke all on tagging_runs, golden_products, golden_tags from anon, authenticated;
grant select on v_catalog_products, v_product_facets, v_review_queue to anon, authenticated;
revoke all on v_invalid_tags from anon, authenticated;
