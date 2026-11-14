# Tagger app: hand-over note

14 Nov 2026

## Start it

```bash
cd web && npm install && npm run build && cd ..   # once, and after any front-end change
uv run tagger app                                  # http://127.0.0.1:8765, local only
```

It needs `SUPABASE_DB_URL`, `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in `.env` (already there) and the
migrations up to 020 (`uv run catalog migrate`). The browser never sees a key; photos are signed links.

## What was built

A local web app with seven tabs where a person approves or corrects the tagger's output. Approval is the
only way a product reaches the website's views; `tagger publish` no longer publishes.

- **Products / Approved:** Ready and Needs review lists with filters; the product page to approve or change
  each flagged answer, save, approve and publish, undo. Approving from the list, one product or all Ready.
- **Live tag:** an Ounass product link, or the first 1 to 25 products of a listing page, scraped,
  categorised and tagged on the spot with the same steps, models and rules as a batch run.
- **Metrics:** golden benchmark against live figures, facet scorecard, calibration, review work, tagging
  cost and health; the same numbers as `tagger evaluate`. Loads in about 0.3 s once warm.
- **Confidence:** per-facet and category levels with a preview of what moves before applying.
- **Facets:** rules, versioned rubrics and values, saved to the database and used by the next product
  tagged; History with Restore; "Test on a product".
- **Models:** which profile categorises and which tags.

Code: `src/app/` (FastAPI), `web/` (React, built into `src/app/static/`), migrations 017 to 020. Plan,
designs and the log of every step: `docs/app/PLAN.md`. Acceptance checks: `docs/app/ACCEPTANCE.md`.

## Decisions the owner made along the way

1. Levels apply to every model-written answer, text-only facets included.
2. Levels, the category gate and the default models live in the database; the config files are the seed.
3. Live tag writes scraped products through the scraper's own code.
4. Keyboard shortcuts skipped.
5. A product whose flags all have saved decisions is Ready and can be approved from the list.
6. Metrics: coverage = golden values whose facet got an accepted value / golden values (recall can never
   exceed it); calibration's lowest band is "below 0.7"; "Correctly left unknown"; auto-accepted is
   accepted / tag answers; live figures cover every tagged product at its latest tagging; quality comes
   from the latest run that covered the golden set; "Benchmark versus live" in the table and per facet.
7. Lowering the category level tags the held products that now clear it, after showing the cost.
8. Facet and value definitions (the rubric sent to the model) are editable and versioned on every change.

## Checks not met, and why

- **Live tag shows the product's photos and text only once it is categorised**, not straight after the
  fetch: the product page reads the catalog row, which exists from categorising on.
- **No separate Rubric card on the Facets tab:** the skill file has no per-facet sections; the database
  definition is the rubric (decision 8).
- **Confidence has an Apply per edited row**, so more than one primary button can show at once, as the PRD's
  row flow describes.
- **Models tab "results so far"** average cost and time over every run of the model, and show the latest
  scored run's accuracy; the scorecard reports one run at a time.
- **Not tested automatically:** re-saving an approved product, Stop on a listing job, and "Test on a
  product" (paid). They are covered by code review and, for Stop and re-saving, by use.
- **Searchable category filter:** the category pickers are plain lists (all levels, indented). Fine for this
  catalog; a search box would help a larger tree.

## Live tag timings (real model calls)

| Product | Department | Whole job (seen on the page) | Categorise + tag (stored) | Cost |
| --- | --- | --- | --- | --- |
| 220232969_14 | Women / Clothing | 45 s | 7 s + 23 s | $0.062 |
| 220038313_14 (from a 3-product listing; 2 already tagged) | Women / Clothing | not recorded | 7 s + 23 s | $0.050 |
| 219853482_15 Porsche Pasha T-shirt (live-tagged by the owner) | Men / Clothing | not recorded | 9 s + 27 s | $0.061 |

The rest of each job is fetching the page and storing the photos (about 10 to 15 s). One more department (for example Beauty or Kids) is still to be timed by hand.

## Tests

`uv run pytest tests/test_app_*.py`: review, live tag, metrics, confidence and models, facets, and the
step 5 checks. They run against the database inside transactions that are rolled back, with the site and
the model stubbed, so they cost nothing and change nothing. Last full run (14 Nov): 211 passed, 7 skipped, in
24 minutes; the skill files were byte-identical before and after. Six of the skips need a product waiting
for review, and every product was approved at the time; they passed in earlier runs. The full suite is `uv run pytest`; don't run two
test runs at once against the same database (they can deadlock).

## Left for the owner

1. Lower a level on the Confidence tab and Undo it (live tag a couple of products first: everything is
   approved now, so nothing would move).
2. Switch the tagging model and live tag one product; the result names the new model (paid).
3. Edit a facet, save, and press "Test on a product" (about 5 cents).
4. Live tag one product from a third department (Beauty or Kids) and add its time above.
