-- v_run_costs gains the provider column (added to tagging_runs in 007). Safe to run more than once.
drop view if exists v_run_costs;
create view v_run_costs with (security_invoker = true) as
select r.tagging_run_id,
       r.stage,
       r.provider,
       r.model_profile,
       r.model_id,
       r.started_at,
       r.finished_at,
       r.products_attempted,
       r.products_succeeded,
       r.input_tokens,
       r.cached_input_tokens,
       r.cache_write_tokens,
       r.output_tokens,
       r.cost_usd,
       round(r.cost_usd / nullif(r.products_succeeded, 0), 6)        as cost_per_product,
       round(1000 * r.cost_usd / nullif(r.products_succeeded, 0), 4) as cost_per_1000_products,
       extract(epoch from (r.finished_at - r.started_at))::numeric   as seconds
from tagging_runs r;

revoke all on v_run_costs from anon, authenticated;
