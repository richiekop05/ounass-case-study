-- Scraper tables, view and image bucket. Safe to run more than once.
-- Only creates the scraper's own objects; never touches anything else.

create table if not exists categories (
    node_id     text primary key,
    parent_id   text references categories (node_id) deferrable initially deferred,
    level       text not null,
    audience    text,
    department  text,
    name        text not null,
    slug        text not null,
    path        text not null,
    is_leaf     boolean not null,
    definition  text,
    aliases     jsonb not null default '[]'::jsonb,
    review_flag text
);

create table if not exists products (
    product_id      text primary key,
    url             text not null,
    name            text not null,
    brand           text not null,
    price           numeric(12, 2) not null,
    original_price  numeric(12, 2),
    currency        text not null,
    site_breadcrumb jsonb not null,
    spine_node_id   text references categories (node_id),
    spine_path      text,
    placement_level text check (placement_level in ('L3', 'L4', 'L5')),
    needs_placement boolean not null,
    audience        text not null check (audience in ('women', 'men', 'kids', 'unisex')),
    description     text not null,
    details         jsonb not null default '[]'::jsonb,
    attributes      jsonb not null default '{}'::jsonb,
    content_tabs    jsonb not null default '[]'::jsonb,
    size_and_fit    jsonb not null default '[]'::jsonb,
    colour          text,
    sizes           jsonb not null default '[]'::jsonb,
    in_stock        boolean not null,
    badges          jsonb not null default '[]'::jsonb,
    scraped_at      timestamptz not null,
    source          text not null check (source in ('json', 'embedded', 'html')),
    -- provenance (not in the product schema, useful for the run report)
    parent_sku      text,
    sample_area     text,
    listing_url     text,
    raw             jsonb not null
);
create index if not exists products_spine_node_idx on products (spine_node_id);

create table if not exists product_images (
    product_id   text not null references products (product_id) on delete cascade,
    position     int  not null check (position >= 1),
    source_url   text not null,
    storage_path text,
    primary key (product_id, position)
);

create table if not exists product_rejects (
    id          bigserial primary key,
    run_id      bigint,
    product_id  text,
    url         text not null,
    reason      text not null,
    payload     jsonb,
    rejected_at timestamptz not null default now()
);

create table if not exists scrape_runs (
    run_id            bigserial primary key,
    started_at        timestamptz not null,
    finished_at       timestamptz,
    status            text not null,
    args              jsonb,
    requests          int not null default 0,
    requests_by_host  jsonb,
    cache_hits        int not null default 0,
    products_written  int not null default 0,
    rejects           int not null default 0,
    node_counts       jsonb,
    area_counts       jsonb,
    field_fill_rates  jsonb,
    errors            jsonb,
    notes             text
);

-- One source for product type: the node's slug when the node is a leaf.
-- security_invoker so the view obeys the RLS on products (Postgres 15+, as on Supabase).
create or replace view products_with_type with (security_invoker = true) as
select p.*,
       case when c.is_leaf then c.slug end as product_type
from products p
left join categories c on c.node_id = p.spine_node_id;

-- Tables sit in the public schema, which Supabase exposes over its REST API.
-- Turn on RLS with no policies: only the service role (which bypasses RLS) can read
-- or write until the storefront adds its own read policies.
alter table categories      enable row level security;
alter table products        enable row level security;
alter table product_images  enable row level security;
alter table product_rejects enable row level security;
alter table scrape_runs     enable row level security;

-- Private bucket for product images (served later through signed URLs).
insert into storage.buckets (id, name, public)
values ('product-images', 'product-images', false)
on conflict (id) do nothing;
