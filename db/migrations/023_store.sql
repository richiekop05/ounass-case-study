-- 023: Richie's store (engineering PRD 3, owner's decisions of 14 Nov 2026, docs/store/PLAN.md).
--
-- Products: `source` says where an approved product came from: `tagged` (reviewed in the tagger app) or
-- `sample` (scraped and tagged for the store through Live tag's "Store sample" mode, published without review).
-- `in_stock` is new; there is no stock data, so everything is in stock unless set.
-- Runs: `purpose` marks the tagging runs made for store samples, so the tagger's run figures and drift
-- monitoring skip them.
-- The tagger app's lists, review queue and run costs never show store samples.
-- Store tables are prefixed `store_`.

alter table catalog_products add column if not exists source text not null default 'tagged'
    check (source in ('tagged', 'sample'));
alter table catalog_products add column if not exists in_stock boolean not null default true;
alter table catalog_products drop constraint if exists catalog_products_approved_via_check;
alter table catalog_products add constraint catalog_products_approved_via_check
    check (approved_via in ('detail', 'list', 'bulk', 'sheet', 'auto', 'sample'));
alter table tagging_runs add column if not exists purpose text not null default 'tagger'
    check (purpose in ('tagger', 'store_sample'));

-- ---------------------------------------------------------------- the tagger never sees store samples
do $$
begin
    execute format('create or replace view v_product_review_state with (security_invoker = true) as
                    select q.* from (%s) q
                     where not exists (select 1 from catalog_products s
                                        where s.product_id = q.product_id and s.source = ''sample'')',
                   rtrim(pg_get_viewdef('v_product_review_state'::regclass), ';'));
    execute format('create or replace view v_review_queue with (security_invoker = true) as
                    select q.* from (%s) q
                     where not exists (select 1 from catalog_products s
                                        where s.product_id = q.product_id and s.source = ''sample'')',
                   rtrim(pg_get_viewdef('v_review_queue'::regclass), ';'));
    execute format('create or replace view v_run_costs with (security_invoker = true) as
                    select q.* from (%s) q
                     where exists (select 1 from tagging_runs r
                                    where r.tagging_run_id = q.tagging_run_id and r.purpose = ''tagger'')',
                   rtrim(pg_get_viewdef('v_run_costs'::regclass), ';'));
end $$;

-- ---------------------------------------------------------------- rules (rows, not code)
create table if not exists store_occasion_profiles (
    occasion        text primary key,              -- a value of the occasion facet
    display_name    text not null,
    formality_min   text not null,                 -- values of the formality facet
    formality_max   text not null,
    seasons         text[] not null,               -- values of the season facet
    hero_roles      text[] not null,               -- outfit roles that can be the hero piece
    active          boolean not null default true,
    reviewed        boolean not null default false,   -- drafted from the occasion rubric until the owner reviews
    note            text
);

create table if not exists store_outfit_roles (
    category_node_id text primary key references categories (node_id),
    role             text not null check (role in ('one_piece', 'top', 'bottom', 'outerwear', 'shoes', 'bag',
                                                   'jewellery', 'accessory', 'none')),
    rule             text,                          -- why: the rule that mapped it, or 'owner'
    needs_review     boolean not null default false
);

create table if not exists store_look_templates (
    anchor_role text not null,
    slot_role   text not null,
    position    int not null,
    required    boolean not null default false,
    primary key (anchor_role, slot_role)
);

create table if not exists store_colour_pairings (
    colour_a text not null,                         -- values of colour_primary, stored with colour_a <= colour_b
    colour_b text not null,
    score    numeric not null check (score between 0 and 1),
    rule     text,
    primary key (colour_a, colour_b),
    check (colour_a <= colour_b)
);

create table if not exists store_score_weights (
    feature text not null check (feature in ('shortlist', 'look', 'profile')),
    signal  text not null,
    weight  numeric not null,
    primary key (feature, signal)
);

create table if not exists store_personas (
    id                 text primary key,
    name               text not null,
    full_name          text not null,
    description        text not null,
    department_default text not null,               -- women | men | all
    starting_intent    text not null check (starting_intent in ('low', 'high')),
    sort_order         int not null default 0
);

create table if not exists store_events (
    id          bigserial primary key,
    persona_id  text not null references store_personas (id),
    session_id  text not null,
    type        text not null check (type in ('view', 'save', 'purchase', 'add_to_bag', 'add_look_to_bag',
                                              'size_select', 'filter_apply', 'occasion_select', 'refine_apply',
                                              'shortlist_view', 'look_view', 'swap_out', 'swap_in',
                                              'search_referral')),
    product_id  text references catalog_products (product_id) on delete set null,
    context     jsonb not null default '{}',
    is_seed     boolean not null default false,
    created_at  timestamptz not null default now()
);
create index if not exists store_events_session on store_events (persona_id, session_id, created_at);
