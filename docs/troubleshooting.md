# Troubleshooting

Run every command from the repository folder. Several commands find `config/`, `.env` and `.cache/` from the folder you run them in.

## Setup

**`SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it in.`** (or the same for another variable)
`.env` is missing, or that line in it is empty. Run `cp .env.example .env` in the repository folder and fill in the values. [Environment variables](../README.md#environment-variables) says where each one comes from.

**`uv sync --locked` fails with "The lockfile needs to be updated".**
`pyproject.toml` was changed without updating `uv.lock`. On a fresh clone this should not happen. Run `uv sync --extra dev` (without `--locked`) to update the lock file.

**`npm ci` fails, or the build fails with a syntax error.**
Node is too old. Check `node --version`: it must be 18 or later.

**`OperationalError: failed to resolve host 'aws-0-region.pooler.supabase.com'`** (seen in the fresh-clone test)
`.env` still has the placeholder from `.env.example`. Replace `SUPABASE_DB_URL` with your own connection string.

**`connection to server ... failed` or `password authentication failed`.**
The connection string in `SUPABASE_DB_URL` is wrong. Use the **Session pooler** string from the Supabase **Connect** button, with your database password filled in where it says `[YOUR-PASSWORD]`. The direct connection string needs IPv6, which many networks do not have.

**`run catalog migrate first (migration 006_tagger.sql)`.**
The tables are not there yet. Run `uv run catalog migrate`.

**`catalog seed-taxonomy` reports nodes "not in categories".**
The categories are not loaded. Run `uv run scraper plan --spine ounass_spine_v5_nodes.csv` first, then the seed again.

**`richies setup` stops with "Persona histories refer to products not approved in the store".**
Expected on a new database: the personas' histories name 23 particular products you have not tagged. Run `uv run catalog migrate` and `uv run richies seed-rules` instead. [Tag your first products](../README.md#tag-your-first-products) explains how to load the histories later.

## Starting the apps

**`The front end is not built yet: run npm run build in web/.`** (tagger app) or **`The store pages are not built.`** (store)
Build them:

```bash
npm ci --prefix web && npm run build --prefix web
npm ci --prefix store/web && npm run build --prefix store/web
```

**`[Errno 48] error while attempting to bind on address ('127.0.0.1', 8780): address already in use`.**
The app is already running in another terminal, or another program uses the port. Stop the other one, or pick another port: `uv run richies serve --port 8781`, or `uv run tagger app --port 8766`.

## Tagging

**`ANTHROPIC_API_KEY is not set. Add it to .env, or run tagger models set-key ANTHROPIC_API_KEY.`**
Add your key to `.env`, or run that command and paste the key when asked.

**Live tag says "This is not an Ounass product or listing link".**
Paste a link from ounass.ae: a product page for **Single product**, or a category, brand or search page for **Listing page** and **Store sample (listing)**.

**Live tag says "Paused: the category is below its confidence level".**
A person decides the category first. Approve or change the category shown below the message, and tagging continues.

**The scraper stops with a message about blocking or robots.txt.**
It stops on purpose at any sign that the site is refusing requests. Wait before trying again, and keep batches small.

## The store

**The store is empty.**
It shows only approved products. Approve some in the tagger app first.

**No occasions under "Where are you going?"**
An occasion is offered only when at least 4 approved products can lead a look for it. `uv run richies coverage` shows what each occasion is missing. Approve more products, or add store samples from a listing page with Live tag's **Store sample (listing)**.

**Looks for you does not appear.**
It needs a taste profile: at least 3 views, saves or similar on products in this session, or a persona's starting history. Browse a few products, or load the persona histories.

## Tests

**`pytest` shows many errors and failures** (seen in the fresh-clone test: 175 passed, 24 failed, 95 errors without a database)
Most tests run against a database with tagged products, inside transactions that are rolled back. Without one they cannot run. These need no database or key, and all pass on a fresh clone (111 tests):

```bash
uv run pytest tests/test_client.py tests/test_listing.py tests/test_product.py tests/test_sampling.py tests/test_scrape_offline.py tests/test_taxonomy.py tests/test_tagger_unit.py tests/test_model_admin.py tests/test_monitor_unit.py
```
