-- Record which provider adapter each run used (model profiles can now point at any provider).
-- Safe to run more than once.
alter table tagging_runs add column if not exists provider text;
