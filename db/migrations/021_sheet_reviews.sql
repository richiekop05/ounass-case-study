-- 021: reviews imported from a review sheet (`tagger review import`) are approvals like any other, marked
-- approved_via = 'sheet' so the review metrics can show them apart and live precision can leave them out.
alter table catalog_products drop constraint if exists catalog_products_approved_via_check;
alter table catalog_products add constraint catalog_products_approved_via_check
    check (approved_via in ('detail', 'list', 'bulk', 'sheet'));
