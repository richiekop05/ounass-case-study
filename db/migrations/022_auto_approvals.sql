-- 022: approvals made without a person (`tagger review auto-approve`, owner, 14 Nov 2026) are marked
-- approved_via = 'auto'; their answers are recorded as 'not_reviewed', so review metrics count people only.
alter table catalog_products drop constraint if exists catalog_products_approved_via_check;
alter table catalog_products add constraint catalog_products_approved_via_check
    check (approved_via in ('detail', 'list', 'bulk', 'sheet', 'auto'));
