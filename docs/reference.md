# Developer reference

The detailed build notes for every part of the system: each command and its options, how each table is used, and the decisions taken along the way. Start with the [README](../README.md); this page is for when you need the detail.

## The scraper

One-off tool that collects about 250 public products from ounass.ae for the case study,
spread across the v5 category spine, and saves them to Supabase. It reads the JSON each page
embeds, obeys robots.txt, and sends one request at a time with a gap of 1.5 to 2.5 s between
requests. It stops on any sign of blocking. See [docs/discovery.md](discovery.md) for the evidence.

## Setup

1. Python 3.11+ and [uv](https://docs.astral.sh/uv/): `uv sync --extra dev`
2. Create a free Supabase project. From **Project settings** copy the project URL, the
   `service_role` key, and the **session pooler** connection string (Database → Connect).
3. `cp .env.example .env` and fill in `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL`,
   and `SCRAPER_USER_AGENT` with a real contact address. `.env` is gitignored.
4. The spine is `ounass_spine_v5_nodes.csv` in the repo root (1,169 nodes, L1 to L5). Pass it to
   `plan --spine`. Holding nodes are the ones whose definition says "holding node".
   (`config/spine_v5_nodes_from_xlsx.csv` is the earlier stand-in built from the workbook; no longer used.)

## Commands (run in this order, prefix with `uv run`)

| Command | What it does |
| --- | --- |
| `scraper discover` | robots.txt, one listing and one product page; rewrites `docs/discovery.md` |
| `scraper migrate` | applies every migration not yet applied (scraper tables, view, private `product-images` bucket, catalog tables); safe to rerun |
| `scraper plan --spine <nodes.csv> --target 250` | loads the spine into `categories` and drafts `config/sample_plan.csv` (`--force` to redraft, `--skip-db` for the CSV only). **Review the plan by hand.** |
| `scraper scrape --limit 20` | smoke test; then `scraper scrape` for the full run. `--category <node_id>`, `--no-images`, `--refresh`, and `--dry-run --spine <csv>` (no database; writes `data/dry_run.jsonl`) |
| `scraper validate` | re-checks every row against the schema and the PRD acceptance rules, and opens a sample of stored images |
| `scraper export` | writes `data/products.jsonl` (`--with-raw` to include the source JSON) |
| `uv run pytest` | offline tests: parsers on saved fixtures, client rate limit and stop rules, plan rules, a full scrape against a fake site |

Pages and images are cached in `.cache/`. A stopped run resumes from the cache, and a repeat
run makes zero requests to the site. `--refresh` ignores the cache.

## Schema (`db/migrations/001_scraper.sql`)

* `categories`: one row per spine node (`node_id`, `parent_id`, `level`, `name`, `slug`, `path`, `is_leaf`, `aliases`, ...).
* `products`: one row per colourway, keyed on `product_id` (= the site's `styleColorId`). It has the
  section 5 fields; lists and objects (including `attributes` and `content_tabs`) are JSONB; `spine_node_id`
  is a foreign key to `categories`; `raw` holds the untouched `initialState.pdp`. Provenance columns:
  `parent_sku`, `sample_area`, `listing_url`.
* `products_with_type` (view): `product_type` is the node's slug on leaf nodes, null otherwise.
* `product_images` (`product_id`, `position`, `source_url`, `storage_path` = `<product_id>/<n>.jpg`, up to 4 at 1000 px).
* `product_rejects`, `scrape_runs` (requests, counts per node and area against quota, fill rates, errors, shortfall reasons).

RLS is on with no policies, so only the service key can read or write. The storefront will
need its own read policies and signed URLs for images.

## Catalog tables (`db/migrations/002`–`005`)

The layer between the scraper and the demo website. It reads the scraper's tables and never
changes them. Its only change to them is a read-only RLS policy on `categories`.
`catalog migrate` applies each migration once and records it in `schema_migrations`.

| Layer | Tables | Written by |
| --- | --- | --- |
| Taxonomy | `facets`, `facet_values`, `category_facets`, `brands` (plus the existing `categories`) | `catalog seed-taxonomy` |
| Catalog | `tagging_runs`, `catalog_products`, `product_facet_tags` | pipeline: a `categorise` run, then a `tag` run |
| Answer key | `golden_products`, `golden_tags` | a person |

`facets` holds each facet's rules:
* source policy: `text_only`, `text_first`, `judge`, `derived` or `not_tagged`;
* `on_missing`: `unknown`, `review_if_required` or `default`, with `default_value` for the last;
* auto threshold, value limits, none-is-exclusive and status;
* the rubric (`definition`).

**Enforced by the database (trigger and checks).** A tag:
* must use a value that belongs to its facet;
* must be for a facet that applies to the product's category;
* must come from a `tag` run, for a product whose category status is `auto`.

The trigger rejects rows for `not_tagged` or retired facets. Derived facets accept only
`evidence = rule`, written by code, and `text_only` facets reject `image` and `both`.

Value columns by status:
* `auto` carries exactly one of value / number / text, in the column that fits the facet.
* `review` carries at most one; with none, a person supplies it.
* `abstained` and `not_applicable` carry none.

`catalog_products.brand` references `brands`, and the category must be a leaf.

**Views.**
* `v_catalog_products`: published products whose category status is `auto`.
* `v_product_facets`: auto tags from the current run, with `display_value`.
* `v_review_queue`: items with status `review`; `needs_value` marks a review row with no value.
* `v_invalid_tags`: must be empty. It lists:
  * wrong category;
  * untaggable facet;
  * two auto values on a single facet;
  * over `max_values`;
  * `none` beside another value;
  * an `auto` tag with `evidence = text` and no quote.

The public key reads only the catalog and taxonomy tables behind the views.

```
uv run catalog migrate
uv run catalog seed-taxonomy
uv run catalog seed-launch-dates      # dummy dates for catalog products with none (fixed seed; never overwrites)
```

**Inputs to `seed-taxonomy`:**
* The workbook's `Register` gives facets and values: list facets split on `;`. Number facets get
  their unit instead of values. `price_band` loses `ultra`.
* `Matrix_long` gives `category_facets`, one row per row for an active facet.
* **`config/facet_policy.csv` (supplied, never generated)** gives status, policy, `on_missing`,
  threshold, limits and none-is-exclusive. `default_value` comes from the Register's
  `unknown_policy` ("defaults false").
* `config/facet_definitions.csv` gives rubrics, loaded when present.
* `config/brands.csv` is exported on the first run for a person to fill in, and loaded after that.

**Seed behaviour:**
* Stops if a Register facet has no policy row, unless `--skip-facets-without-policy`.
* Stops if a `Matrix_long` node is missing from `categories`, unless `--skip-missing-nodes`.
* Lists skipped rows in `data/seed_skipped_rows.csv`.
* Is safe to rerun, and refuses to drop a value that tags still use.

**Current load:**
* 110 facets: 92 active (42 text_only, 33 judge, 8 text_first, 7 derived, 2 not_tagged) and 18 retired.
  The policy file of 13 Nov 2026 adds `colour_secondary` (text_only) and `is_metallic` (derived) and
  retires the 8 type facets the Register already retires. Nothing is skipped.
* 630 values.
* 14,785 category-facet rows on 746 L3/L4 nodes.
* 142 brands.
* Rubrics from `facet_definitions.csv` v1: 33 facets and 195 values.

Code may write `evidence = rule` rows on any taggable facet (005), for `on_missing` defaults and
`exclusivity = local_designer`. Derived facets take only rule rows.

**Left out:**
* `Matrix_long` has no L5 rows, so the 388 L5 nodes have no facets. No product sits on one today.

`tests/test_catalog_db.py` runs every PRD acceptance check against the live database inside a
rolled-back transaction.

## Where this differs from the PRD

* **No pagination.** robots.txt disallows every URL with a `?`, so each listing gives only its
  first 24 products. The plan uses many narrow category pages instead, and picks every 3rd
  product on page one.
* **`product_id` is `styleColorId`**, for example `220163574_195`, because each colour counts as a product.
* **Beauty pages have a "Key Details" tab instead of "Design details"**, and it fills `details`.
* `SUPABASE_DB_URL` is an extra setting, needed to run SQL (migrations and upserts).
* Plan CSV has extra columns `area`, `placement_level`, `note`.

## Tagging pipeline (`tagger`, `db/migrations/006_tagger.sql`)

Takes each scraped product through three steps. Step 1 (categorise) is one model call per product.
Step 2 (tag) is one model call per product. Step 3 (derive) is code only. Results are saved to
`catalog_products` and `product_facet_tags`, and every run can be scored against a hand-labelled
golden set. Nothing about a facet is hard-coded: values, definitions, policies, thresholds and
limits are read from the database on every run. The only fixed text is the two skill files in
`skills/`, which are loaded as they are.

### Setup

1. `uv run catalog migrate` applies `006_tagger.sql`. It adds the run columns, `tagging_run_products`,
   `tagging_calls`, `evaluation_results` and `v_run_costs`, and makes `golden_products.category_id`
   nullable.
2. Add `ANTHROPIC_API_KEY=...` to `.env`. Nothing else about models goes in `.env`.
3. Check `config/models.toml` (model profiles and prices) and `config/pipeline.toml` (defaults,
   thresholds, step 3 parameters). Then review `config/giftable_categories.csv` and
   `config/gift_occasion_rules.csv` (drafts; see below).

### Commands (`uv run tagger ...`)

| Command | What it does |
| --- | --- |
| `categorise [--limit N] [--product ID] [--model NAME] [--run ID]` | Step 1 for products not yet in `catalog_products`. |
| `tag [--limit N] [--product ID] [--model NAME] [--golden-only] [--run ID] [--no-derive]` | Step 2 for products with category status `auto`, then step 3. Always tags from the saved categories. |
| `derive --run ID` | Step 3 for a tag run. Replaces that run's `derive:` rows and nothing else. |
| `run [--limit N] [--product ID] [--categorise-model NAME] [--tag-model NAME] [--with-total]` | All three steps. `--limit 10` is the smoke test. |
| `publish --run ID` | Sets `current_run_id` and `is_published` for the run's products. |
| `golden sample --n 50` / `golden export` / `golden import FILE` | Golden set workflow (below). |
| `evaluate --run ID [--compare ID]` | Scores a run against the golden set and saves to `evaluation_results`. |
| `compare --runs A --runs B [--stage tag] [--project 3000000] [--with-total]` | Runs side by side on quality, cost and time. Projections are labelled as estimates. |
| `models` | Lists profiles and marks the two defaults. |
| `show --product ID [--run ID] [--full]` | Prints the stored request, reply and saved rows. |

To resume a stopped run, re-run the same command with `--run ID`. Products already done are skipped,
and failed ones are tried again. One product's failure never stops a run: it is logged, counted as
attempted and not as succeeded.

### Adding models and choosing one per step

Model profiles live in `config/models.toml`. The model each step uses by default lives in the database
(`app_settings`, set on the review app's Models tab or with `tagger models use`). Keys live only in `.env`. The `tagger models` commands edit these files
with their comments kept. They are thin wrappers over `src/tagger/model_admin.py`, whose functions
return plain JSON-ready data, so a front end can call them directly.

| Command | What it does |
| --- | --- |
| `tagger models [--json]` | Lists profiles, the default for each step, and whether each key is set. It never shows the key. |
| `tagger models providers` | Lists the adapters available and what each needs. |
| `tagger models add NAME --provider P --model-id ID --input-price X --output-price Y [...]` | Adds a profile. Other options: `--cached-input-price`, `--batch-*-price`, `--api-key-env`, `--base-url`, `--no-images`, `--json-mode`, `--effort`, `--max-tokens`, `--max-tokens-field`, `--extra '{json}'`, `--replace`. |
| `tagger models set-key ENV_NAME` | Prompts for the key without echoing it and writes it to `.env` with mode 600. |
| `tagger models test NAME` | Sends one tiny request to check the key, the model ID, JSON replies and image input, and shows the cost. |
| `tagger models use NAME --step categorise\|tag` | Makes a profile the default for a step (saved in the database, as the Models tab does). |
| `tagger models remove NAME` | Removes a profile. It refuses while the profile is a step's default. Past runs keep their recorded model and prices. |

There are two adapters:

* **`anthropic`**: Claude models. The key is `ANTHROPIC_API_KEY` unless `--api-key-env` names
  another variable.
* **`openai_compatible`**: any provider with an OpenAI-style chat API. That covers OpenAI, Google
  Gemini (through its OpenAI endpoint), Mistral, Groq, DeepSeek, xAI, OpenRouter and Together, and
  local servers such as Ollama, LM Studio and vLLM. The profile gives the base URL and the name of
  the key variable; use `--api-key-env ""` for a local server with no key. With `--json-mode`:
  * `json_schema` (the default) sends the reply schema strictly.
  * `json_object` and `none` are for providers without schema support; the schema then goes into
    the instructions.

  Every reply is checked against the schema in code either way.

Example, with an illustrative model ID and prices. Look up the real ones before adding a model:

```bash
uv run tagger models add gpt-mini --provider openai_compatible --base-url https://api.openai.com/v1 \
    --api-key-env OPENAI_API_KEY --model-id <model id> --input-price 0.40 --output-price 1.60
uv run tagger models set-key OPENAI_API_KEY
uv run tagger models test gpt-mini
uv run tagger models use gpt-mini --step categorise              # change the default
uv run tagger tag --golden-only --model gpt-mini                 # or just one run
uv run tagger compare --runs 12 --runs 13 --stage tag --project 3000000
```

A provider whose API is neither of these needs one new file, `src/tagger/providers/<name>.py`,
with an `Adapter` class (see `providers/base.py` and the two existing adapters). It then appears in
`tagger models providers`. The pipeline steps don't change.

**Thresholds and default models (since 14 Nov 2026):** they live in the database and are changed in the
review app's Confidence and Models tabs. Per-facet levels are `facets.review_threshold` (empty means the
default); the default level, the category level and the two default model profiles are in `app_settings`.
The `auto_threshold` column of `config/facet_policy.csv` and the gate and model names in
`config/pipeline.toml` were the original seed and are no longer read.

**Review app:** see "Review app" below.

Each run records its profile, model ID and prices, so editing `models.toml` later never changes the
cost of a past run. Categorise and tag runs are costed separately. `v_run_costs` has one row per run;
filter `stage = 'tag'` for tagging cost. `compare --stage tag --project N` gives the tagging-only
projection.

### What the code decides

* **Step 1:**
  * Candidates are the leaf nodes under the product's audience and department, without "Other ..."
    holding nodes. Only leaves are offered, because `catalog_products` accepts only leaf categories.
  * If the scraper stored no spine node, the candidates come from the site breadcrumb, and the run
    notes this in `tagging_run_products.note`.
  * Confidence at or above the threshold gives `auto`, otherwise `review`.
  * After the run, products without a launch date get a dummy one (`catalog seed-launch-dates`
    logic), which `recency` needs.
* **Step 2:** applies the checks in PRD 4.4 in order, then the status table in 4.5 (`src/tagger/tagging.py`).
  * A quote must appear in the product text after lower-casing and collapsing whitespace. Curly and
    straight quote marks are treated as equal.
  * `function` is left out under the categories in `[function_categories]`; step 3 sets it.
* **Step 3** (`src/tagger/derive.py`) writes rows with evidence `rule`, status `auto` and a
  `derive:...` reason. It only writes facets mapped to the product's category. The rule parameters
  are in `[derive]` in `pipeline.toml`.
  * `price_band` ranks the price within the product's audience and department. Fewer than nine
    priced products falls back to the whole catalog.
  * `exclusivity` replaces a "none" answer with "local designer" for flagged brands.
* **Retries:** up to 3 retries on rate limits, server errors and network errors, with exponential
  backoff. A rate limit pauses every worker. A reply that doesn't match the schema is retried once.
  Every attempt is a row in `tagging_calls`, stored without image bytes and without API keys.

### Monitoring: unknown rate and confidence shift (`009_monitoring.sql`)

After every `tagger tag` and `tagger run`, the monitor checks the new run against a baseline run.
It needs no golden set: it works on every product in the run, and it skips runs with no succeeded
products.

* **Stats:** for each facet it records how many products came back auto, review, unknown or not
  applicable, and how the confidence scores spread over five bands, in `run_facet_stats`.
  * Every facet gets an `all` row.
  * A department, L3 category or brand gets its own row only with at least `min_segment_products`
    (15) products.
  * Categorise runs use facet `category`.
* **Baseline:** the run pinned with `tagger monitor baseline --run ID`. Without a pin, it's the most
  recent earlier non-drill run of the same stage that covered at least half as many products.
* **Checks** (`[monitor]` in `config/pipeline.toml`):
  * unknown rate and review rate up by 10 points (warning) or 20 points (alert), or more than double
    with a rise of at least 10 points (alert);
  * coverage down by 10 or 20 points;
  * mean confidence down by 0.05 or 0.10;
  * the stability index of the confidence bands at 0.10 or 0.25.

  Rate checks need 15 applicable products in both runs; confidence checks need 30 values. A fall in
  unknown rate or a rise in confidence is never flagged.
* **Alerts:** written to `monitor_alerts`. Re-running the monitor for the same two runs replaces
  that pair's alerts but keeps any acknowledgement.

| Command | What it does |
| --- | --- |
| `tagger monitor --run ID [--baseline ID]` | Stats, comparison, alerts, and the ten largest changes. |
| `tagger monitor baseline --run ID` | Pins the baseline for that run's stage. |
| `tagger monitor history --facet ID [--segment brand=NAME] [--json]` | One line per non-drill run, in date order. |
| `tagger monitor ack --alert ID [--note TEXT]` | Acknowledges an alert. |
| `tagger tag --golden-only --drill NAME` | A drill run with one input removed: `no-design-details`, `no-images` or `title-only`. |

Drill runs are flagged `is_drill` and can never be published. They are left out of baseline
selection, `v_run_costs`, `v_monitor_latest`, `v_facet_trends` and `monitor history`. Asking for one
by ID still works.

The views for the front end:
* `v_monitor_latest`: the latest tag run against its baseline, per facet, with the stability index
  and the worst severity.
* `v_facet_trends`: per facet and run, for charts.
* `v_confidence_bands`: band shares for a run and its baseline.
* `v_open_alerts`: open alerts, most severe first.

### Golden set

1. `tagger golden sample --n 50` picks products with a fixed seed, in proportion to each department,
   with at least 3 per department.
2. `tagger golden export` writes `golden/labelling_sheet.xlsx`. Fill in the Categories sheet's
   `category_id` (the candidate list is next to it), then run `golden import` and `golden export`
   again. The Facets sheet then lists every model-facing facet for each product, with its rule and
   allowed values. Fill in `state` (value / unknown / not applicable) and `answer` (values separated
   by `;`).
3. `tagger golden import golden/labelling_sheet.xlsx` validates every row, lists the invalid ones and
   loads the rest. It is safe to re-run.

The sheet is built only from scraped data and the taxonomy: it never reads `catalog_products` or
`product_facet_tags`. Derived facets aren't on it; tests check them.

### Golden set extension and AI-assisted labelling

The golden set has 85 products: the original 50 plus 35 added with
`tagger golden sample --add clothing=10,beauty=7,accessories=10,bags=8`.
* Department names are matched without regard to case.
* Existing products are never removed or picked again.
* Picks spread across categories and brands, with the same fixed seed as the first sample.
* A department with too few products left reports the shortfall; the gap isn't filled from another
  department.
* Repeating the same `--add` adds nothing.

Labelling runs in four steps:

1. `tagger golden prefill` drafts a category and facet answers for every golden product into
   `golden/prefilled_labels.xlsx`.
   * It uses the `golden_prefill_model` profile (`opus`), not a model under test. `--model` or
     `tagger models use NAME --step golden_prefill` changes it.
   * It builds the pipeline's own requests, with the same skill files, candidates, facet lists and
     answer form.
   * There is no gate and there are no thresholds: the chosen category is always carried into
     tagging, and every answer goes into the sheet as given, after the pipeline's checks.
   * `certainty` is high (0.90 or above), medium (0.75 to 0.90) or low. Unknown answers, and answers
     a check changed, are low.
   * It writes nothing to the database: it opens a read-only connection and never reads
     `catalog_products` or `product_facet_tags`.
   * It refuses to overwrite a sheet that already has review entries unless `--force` is given.
2. A person reviews the sheet:
   * The yellow columns are for the reviewer.
   * Products sheet: `category_verdict` (ok / change), `corrected_category_id`, and
     `all_facets_reviewed` (yes). The candidate list is in the last column.
   * Facets sheet: `verdict` (ok / change), `corrected_state` and `corrected_values`, with values
     separated by `;`.
   * Filter on `certainty` to start with the doubtful rows.
3. `tagger golden import golden/prefilled_labels.xlsx` loads the final answers.
   * Verdict `ok` keeps the draft and `change` takes the correction.
   * A blank verdict keeps the draft only if the product is marked `all_facets_reviewed = yes`;
     otherwise that row is skipped and counted.
   * If a category was changed, the new category is saved, none of that product's facet rows are
     imported, and the product is listed for a second pass.
   * Every problem is listed together, and valid rows still load.
   * Re-importing replaces a product's labels without duplicates.
   * The import still accepts the older `labelling_sheet.xlsx` format.
4. `tagger golden prefill --only-recategorised golden/prefilled_labels.xlsx` drafts the
   re-categorised products under their corrected category into
   `golden/prefilled_labels_pass2.xlsx`, for a second review and import.

Only final answers are stored. Nothing about the drafts or the review is kept, the golden tables'
columns are unchanged, and nothing is written to `evaluation_results`.

### Gift rule drafts

`scripts/draft_gift_rules.py` turned the giftable and gift occasion definitions into
`config/giftable_categories.csv` (55 rows) and `config/gift_occasion_rules.csv` (78 rows):

* A row covers its node and everything below it.
* For giftable, the most specific row wins.
* For occasions, the lowest rank wins, up to three per product.
* Rows with keywords match on the product text (Eid, Ramadan, Christmas, Diwali).

A person should check both files before the first full run. Re-running the script needs `--force`,
so reviewed edits aren't lost by accident.

### Additions to the PRD

* `cache_write_price` / `cache_write_tokens`: the provider bills cache writes separately.
* Optional profile fields `effort`, `max_tokens` and `fallbacks`.
* `tagging_run_products`, the per-run product ledger used for resume, notes and evaluation.
* Tag requests also carry the "Size & Fit", "Dimensions" and "Ingredients" tabs. Measurement and
  ingredient facets need them.

## Review app (`tagger app`, `src/app/`, `web/`)

A local web app where a person approves or corrects the tagger's output. Approval is the only way a product
reaches the website's views; `tagger publish` no longer publishes.

```bash
cd web && npm install && npm run build && cd ..   # once, and after any front-end change
uv run tagger app                                  # http://127.0.0.1:8765 (localhost only), opens the browser
uv run tagger app --port 8800 --no-open
```

| Tab | What it does |
| --- | --- |
| Products | Every tagged product not yet approved, as Ready or Needs review, with filters (state, search, category at any level, facet value or flag). Approve a Ready row, or "Approve all ready" for the filtered list. A row opens the product page: photos and text with quotes highlighted, the category, every facet; approve or change each flagged answer, Save, then "Approve and publish". |
| Approved | Approved products, with Undo / send back to review and "View on website". |
| Live tag | Paste an Ounass product link (or a listing page and a number up to 25): scrape, categorise and tag it now, then review it on the same page. Costs about 5 cents a product. |
| Metrics | Read-only: golden benchmark against live figures, facet scorecard, calibration, review work, tagging cost, health. Same numbers as `tagger evaluate`. |
| Confidence | The level each facet (and the category step) must reach, how much of the catalog reaches it, and a preview of what a change moves before Apply. Lowering the category level tags held products that now clear it. |
| Facets | Each facet's rules, rubric (the definition sent to the model, versioned v6 -> v6.1 on each change) and values. Saved changes apply to the next product tagged; History with Restore; "Test on a product" re-tags one product and shows before and after. |
| Models | Which profile categorises and which tags; used by everything tagged from then on. |

Notes:

* Facets edited in the app are kept by `catalog seed-taxonomy` unless it is run with `--force`.
* Review in a spreadsheet instead: `uv run tagger review export --run <tag run>` writes
  `review/review_run<ID>.xlsx` (flagged or held categories, and every answer in review; golden products and
  accepted answers left out). Fill in `verdict` (approve / change / unknown) and the corrected value, then
  `uv run tagger review import review/review_run<ID>.xlsx`. It writes exactly what the app writes; a product with
  every row filled in is approved and published (marked `sheet` in the metrics), a blank verdict keeps it in
  review, a value that is not allowed is rejected and listed, and a decided category re-tags that product.
* Tests: `uv run pytest tests/test_app_*.py` (against the database, inside rolled-back transactions; site
  and model calls stubbed).
* Plan, decisions and progress: `docs/app/PLAN.md`; acceptance checks: `docs/app/ACCEPTANCE.md`; hand-over:
  `docs/app/HANDOVER.md`.

## Richie's store (`store/`, branch `store`)

A sample luxury store for the occasion assistant and Complete the Look. It reads only approved products from the
same database (products approved in the tagger app, plus store samples), and writes only its own event log.

```bash
uv run richies setup                                   # migrations, rule tables, persona histories (safe to rerun)
cd store/web && npm install && npm run build && cd ../..
uv run richies serve --with-tagger                     # store http://127.0.0.1:8780, tagger app http://127.0.0.1:8765
```

* Rules live in the database (`store_*` tables), seeded from `store/sample_data/rules/*.json` by
  `uv run richies seed-rules`; every value is checked against the tagger's vocabulary.
* Persona histories: `store/sample_data/personas.json`, loaded by `uv run richies load-personas`
  (`uv run richies draft-personas` proposes them from the approved catalogue).
* More products for the store: the tagger app's Live tag, "Store sample (listing)" mode. They are tagged as usual,
  published for the store only, and never shown in the tagger app or its figures.
* `uv run richies coverage` (products per outfit role for each occasion and department) and `uv run richies funnel`
  (sessions that see a shortlist, tap a hero, view a look, swap, add a look to bag).
* Tests: `uv run pytest store/tests`. Plan and progress: `docs/store/PLAN.md`.
