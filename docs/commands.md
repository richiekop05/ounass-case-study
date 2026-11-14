# Commands

Run every command from the repository folder, starting with `uv run`. Add `--help` to any command for its options. 💲 marks commands that call a model and cost money.

The [developer reference](reference.md) has the background to each command.

## Setup and data

| Command | What it does |
| --- | --- |
| `catalog migrate` | Apply the migrations in `db/migrations/` not applied yet, in order. Creates the photo bucket. Safe to rerun. |
| `scraper plan --spine ounass_spine_v5_nodes.csv` | Load the category spine into `categories`. Drafts `config/sample_plan.csv` only if it does not exist (`--force` redrafts). |
| `catalog seed-taxonomy` | Load facets, values, rubrics, which facets apply to which category, and brands from `config/`. Keeps facets edited in the app unless `--force`. |
| `scraper scrape [--limit N] [--category NODE]` | Scrape products from the listing pages in the sample plan, with their photos. No model calls. |
| `scraper validate` | Re-check stored products against the schema and the acceptance rules. |
| `scraper export` | Write the products table to `data/products.jsonl`. |
| `scraper discover` | Fetch robots.txt, one listing and one product page, and rewrite `docs/discovery.md`. |

## Tagging

| Command | What it does |
| --- | --- |
| `tagger categorise [--limit N] [--product ID]` 💲 | Step 1, for products not yet categorised. |
| `tagger tag [--limit N] [--product ID] [--golden-only]` 💲 | Step 2 for products whose category was accepted, then step 3 (rules). |
| `tagger run [--limit N] [--product ID]` 💲 | All three steps. `--limit 10` is a good first test. |
| `tagger derive --run ID` | Step 3 only, for a tag run. Code, no model. |
| `tagger restatus --run ID` | Recalculate each answer's status from its stored confidence and the current levels. No model call. |
| `tagger publish --run ID` | Point products not yet approved at this run. Publishing to the store happens only by approval. |
| `tagger show --product ID` | Print the stored request, reply and saved answers for one product. |
| `tagger app [--port 8765]` | Start the tagger app on http://127.0.0.1:8765/. |

To continue a stopped run, repeat the command with `--run ID`. Finished products are skipped, and failed ones are tried again.

## Review in a spreadsheet

| Command | What it does |
| --- | --- |
| `tagger review export --run ID` | Write `review/review_run<ID>.xlsx`: held categories and answers in review. |
| `tagger review import FILE` 💲 | Apply the verdicts as the app would. A product with every row decided is approved. A changed category re-tags that product, which costs money; `--no-retag` skips it. |
| `tagger review auto-approve --run ID` | Approve every waiting product of a run with the tagger's answers as they stand, marked as not reviewed. |

## Quality

| Command | What it does |
| --- | --- |
| `tagger evaluate --run ID [--compare ID]` | Score a tag run, and its categorise run, against the golden set. Writes `reports/facet_scorecard_run<ID>.xlsx`. |
| `tagger compare --runs A --runs B` | Runs side by side on quality, cost and time. |
| `tagger monitor --run ID` | Unknown rate and confidence shift against the baseline run. Also: `monitor baseline`, `monitor history`, `monitor ack`. |
| `tagger golden import FILE` | Load golden answers from a filled-in sheet or from `golden/golden_master.xlsx`. Only for products already in the golden set. |
| `tagger golden export-master` | Write the golden answers as they are now to `golden/golden_master.xlsx`. |
| `tagger golden sample --n 50` | Pick a golden set from the scraped products, spread across departments. |
| `tagger golden export` / `export-holdout` 💲 / `import-holdout` | Labelling and holdout sheets. `export-holdout` has a model draft the answers. |
| `tagger golden export-disagreements --run ID` / `import-disagreements FILE` | Review the golden answers a run disagrees with. |
| `tagger golden prefill` 💲 | Have a model draft golden answers into a review sheet. Writes nothing to the database. |

## Models

| Command | What it does |
| --- | --- |
| `tagger models` | List the model profiles and the default for each step. |
| `tagger models use NAME --step categorise` | Make a profile the default for a step (`categorise`, `tag` or `golden_prefill`). |
| `tagger models set-key ANTHROPIC_API_KEY` | Store an API key in `.env` without showing it. |
| `tagger models test NAME` 💲 | Send one tiny request to check the key and model (a fraction of a cent). |
| `tagger models add` / `remove` / `providers` | Manage profiles in `config/models.toml`. |

## Store

| Command | What it does |
| --- | --- |
| `richies serve [--with-tagger] [--port 8780]` | Start the store on http://127.0.0.1:8780/, and the tagger app on 8765 with `--with-tagger`. |
| `richies seed-rules` | Load the store's rules and personas from `store/sample_data/`. |
| `richies load-personas` | Load the personas' starting histories. Needs all their products approved. |
| `richies setup` | `catalog migrate`, `seed-rules` and `load-personas` in one go. Stops at the personas on a store without their products. |
| `richies coverage` | Approved products per outfit role for each occasion: where more products are needed. |
| `richies funnel` | What shoppers did, per persona: shortlists seen, looks viewed, swaps, looks added to bag. |
| `richies reset-events` | Delete every session event, keeping the personas' histories. Restart `richies serve` afterwards. |
| `richies draft-personas` | Propose persona histories from the approved catalogue into `store/sample_data/personas.json`. |

## Tests and docs

| Command | What it does |
| --- | --- |
| `pytest` | Every test. Most tests need a database with tagged products. They run inside transactions that are rolled back, so they change nothing. |
| `pytest tests/test_client.py tests/test_listing.py tests/test_product.py tests/test_sampling.py tests/test_scrape_offline.py tests/test_taxonomy.py tests/test_tagger_unit.py tests/test_model_admin.py tests/test_monitor_unit.py` | The tests that need no database or key. |
| `python scripts/db_reference.py` | Rewrite [database.md](database.md) from the migrations (`--check` to only check it). |
