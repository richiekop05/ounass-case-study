-- 016: holdout golden products (14 Nov 2026).
-- Golden products kept out of every review against tagger output: their answer key comes only from the
-- Opus pre-fill and a person's review. tagger evaluate scores them separately from the reviewed products.
alter table golden_products add column if not exists is_holdout boolean not null default false;
