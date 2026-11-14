# Tagger app: build plan and progress

**Resume here.** This file is the single source of truth for the app build. Read it first in any new
session, then only the PRD sections the current step needs. Update "Progress" at every checkpoint.

## Sources (in this folder)

| File | Use | Read |
| --- | --- | --- |
| `build_prd_v2.md` | What is stored, API, jobs, build order, tests (wins on data and build) | Section for the current step only |
| `design_prd_v7.md` | Every screen, wording, tokens, acceptance checks in section 14 (wins on look) | Section for the current tab only |
| `metrics_requirements.md` | Metrics tab requirements | Step M only |
| `design_handoff/README.md` | Summary of the design, prototype decisions on open points | Once per tab |
| `design_handoff/prototype/Tagger.dc.html` | Clickable prototype: markup at the top shows exact layout and styles per screen; logic at the bottom is sample data | grep the screen's block; never read whole |

Missing: the Review Screen PRD (section 6, keyboard shortcuts). Asked for; until it arrives, shortcuts
follow the prototype (none defined) and are added in step 2 once supplied.

## Stack

- **Server:** FastAPI + uvicorn in `src/app/` (new package in this repo, Python 3.13 via uv). Calls
  scraper, pipeline, evaluation and config code as functions. `tagger app [--port N]` starts it on
  127.0.0.1 and opens the browser. Serves `/api/*` and the built front end from `src/app/static/`.
- **Front end:** React + TypeScript + Vite in `web/` (repo root). Plain CSS; every value from
  `web/src/styles/tokens.css` as CSS variables. One file per component named in Design PRD section 12.
  `npm run build` writes to `src/app/static/`. No component library, no CSS framework, no chart library.
- **Database:** existing Supabase Postgres through the existing `SUPABASE_DB_URL`; the browser never
  gets a key. Photos: signed URLs from the private bucket, via the existing `ImageStore` code.
- **Jobs:** in-process, one at a time, state in memory; the page polls `GET /api/jobs/{id}` each second.

## Decisions and facts found in the repository

1. **Thresholds today.** Per facet: `facets.auto_threshold`, written by `catalog seed-taxonomy` from
   `config/facet_policy.csv`, read by `tagger.facets` (FACET_SQL) and `tagger.tagging.value_status`.
   Category gate: `category_auto_threshold` in `config/pipeline.toml`, read via `Settings` in
   `tagger.steps.categorise_one`. Default models: `categorise_model` / `tag_model` in `pipeline.toml`.
2. **Text-only and text-first facets have no confidence level today** (null `auto_threshold`; accepted
   on text evidence whatever the confidence). The PRD treats null as "use the default". Proposed: the
   level applies only where the pipeline applies one today. DECIDED (14 Nov): **apply levels to all**
   model-written answers. Every value answer is compared with its level (`facets.review_threshold`, else
   `app_settings.default_review_threshold`); text-only answers without text evidence and quotes not found
   stay in review for those reasons. `tagging.value_status` changes the same way, so pipeline and view agree.
3. **Default level.** Seed `app_settings.default_review_threshold = 0.80`; set `facets.review_threshold`
   only where today's value differs (aesthetic, occasion, formality at 0.70), so "default" is meaningful.
4. **Category status.** The pipeline already stores `auto`, `review` and `flagged` (tagged, category to
   review). The state view works from confidence vs `category_review_threshold` (seed 0.75) as the PRD
   says; `review` products have no tag run and show as Needs review.
5. **review_reason.** Not stored today (only inside the free-text reason). Add the column and set it in
   `tagging.rows_for`: `low_confidence`, `quote_not_found`, `missing_required`, `text_policy` (text-only
   facet without text evidence), `text_only_model` where relevant. Backfill existing rows from reason text.
6. **Facet rules are already read from the database per product** (`facets_for_tagging`, no cache).
   Changes needed: filter `facet_values.is_active`, and make the seed skip facets with `edited_in_app`.
7. **Rubric card:** `skills/tagging_skill.md` has no section per facet, so the card is left out (PRD rule).
8. **Listing pages:** the scraper already has a listing parser (`scraper.listing.parse_listing`); reuse it.
9. **Holdout marker exists:** `golden_products.is_holdout` (migration 016); tuned set = not holdout.
10. **Live tag writes the scraper's raw tables** (products, product_images) through the scraper's own
    store, exactly as a normal scrape. DECIDED (14 Nov): allowed, through the scraper's own code only.
11. Golden tables: the app never writes them; the Metrics tab reads them only through `tagger.evaluate`.
12. `ai_run_id` is backfilled from each product's latest tag run.

## Build order (each step ends at a checkpoint; stop and wait for a go-ahead)

| Step | Scope | Checkpoint |
| --- | --- | --- |
| 0 | Plan (this file); scaffold server + front end shell with tokens and nav, no schema change | `tagger app` opens the shell |
| 1 | Migration 017 (all section 3 objects incl. thresholds, review_reason, settings), `v_product_review_state`, read points moved to the DB, `/api/summary`, `/api/products`, Products + Approved tables with filters | Products tab lists real products with the right button |
| 2 | `/api/products/{id}`, detail page: evidence panel, category card, facet rows, value editor, badge, quote highlight, shortcuts | Any product opens; decisions held on the page |
| 3 | Review runs: Save, Approve and publish, Approve from list, Approve all ready, Undo, Approved tab, category change + re-tag, `tagger publish` stops publishing, review outcomes | Approve publishes; Undo removes it; tagger rows unchanged |
| 4 | Live tag: job runner, single product and listing modes, toggle detection | Pasting a new Ounass URL tags it on the page |
| M | Metrics tab (blocks 1, 3, 4 then 2) | Same figures as `tagger evaluate` for the run |
| C | Confidence + Models tabs | Lowering a level moves products to Ready; switching model shows in Live tag |
| F | Facets tab | Add a value, Test on a product shows before/after |
| 5 | States, acceptance checks (Design PRD 14), tests (Build PRD 9), README line, hand-over note | All checks ticked or noted |

## Working method (to stay inside the context window)

- One step per session where possible. At each checkpoint: update Progress below, run the tests that
  cover the step, then stop.
- Read PRD sections by heading with `grep -n "^## "` then `sed -n`, never whole files.
- Front-end components are written in small files; large, independent ones may be delegated to a
  subagent with a brief that names the PRD sections and the prototype block to follow.
- Never edit an applied migration; one new migration per step that needs schema.
- Tests against a test copy of the database run inside rolled-back transactions (existing pattern).

## Progress

- [x] Step 0a: PRDs and design copied into `docs/app/`; plan written (14 Nov 2026).
- [x] Step 0b: scaffold done (14 Nov 2026). `src/app/{server,db}.py` (FastAPI, `/api/health`, SPA
      fallback, safe error messages), `tagger app [--port] [--no-open]`, `web/` (Vite 5, React 18, TS),
      `web/src/styles/{tokens,base}.css`, `Rail`, `TopBar`, router with filters in the query string.
      Build: `cd web && npm run build` (writes `src/app/static`). Dev: `tagger app --no-open` + `npm run dev`.
      Checked: build passes, no hex colours outside tokens.css, `/api/health` returns 241 products.
- [x] Go-ahead given (14 Nov): schema changes (10.1), `tagger publish` stops publishing (10.2), levels to
      the DB (10.3), default models to the DB (10.4). Decisions 2 and 10 settled above.
- [x] Keyboard shortcuts: skipped at the user's request (14 Nov). No Review Screen PRD needed.
- [x] Step 1 DONE (14 Nov), awaiting go-ahead for step 2. Full suite: 150 tests pass after fixing 3 stale test
      expectations (new trigger wording, holdout flag reset in fixtures, golden column list).
  - [x] Migration 017 applied (14 Nov). State view: 24 ready, 77 needs review; ai_run_id on 98/101.
  - [x] Read points: FACET_SQL level = coalesce(review_threshold, default); `tagging.review_reason` +
        `value_status` (levels apply to all); review_reason stored on new rows; `tagger.dbsettings` applies
        app_settings in `cli._settings()`; tag_one sets ai_run_id/current_run_id; start_run records
        taxonomy_version; `tagger publish` no longer publishes; pipeline.toml and README note the seed.
        Offline tests pass (62).
  - [x] API: `src/app/listing.py` + `/api/summary`, `/api/products`, `/api/facets`, `/api/categories/tree`;
        signed photo URLs in `src/app/photos.py`. Smoke-tested on real data.
  - [x] Front end (14 Nov): `web/src/pages/ProductList.tsx` (both tabs), `components/{ConfidenceBadge,
        StatusBadge,Chip}.tsx`, rail counts from /api/summary. Build passes, no hex outside tokens.
        Follow-ups for step 5: category filter is an indented native select (PRD 5 wants a searchable
        drop-down); list Approve / Approve all / toast / fade wired in step 3.
  - [x] facets_for_tagging skips inactive values (14 Nov).

## Step 2 progress (go-ahead given 14 Nov)
- [x] Migration 018: `v_tag_flags` (one flag definition: list, detail, Confidence); state view rebuilt on it.
- [x] `src/app/detail.py`: `GET /api/products/{id}` (text = product.parts() as the model saw it, signed
      photos, category + runner-up + level, facets with answer/flag/flagReason/allowed values) and
      `GET /api/categories?q=` (assignable nodes only). Smoke-tested.
- [x] Front end detail page (14 Nov): `pages/ProductDetail.tsx`, `components/{EvidencePanel,CategoryCard,
      FacetRow,ValueEditor}.tsx`, `productTypes.ts`; list stores its query in sessionStorage `tagger:listQuery`;
      `/approved/<id>` route. Build passes; one headless screenshot checked. Reasons cleaned server-side.
      Known gaps: no number range (none stored), runner-up has no confidence (not stored), prev/next only
      within the current list page. STEP 2 DONE, awaiting go-ahead for step 3 (save/approve/publish).

## (old note) step 2 (detail page)
Read Design PRD 6 and 12, Build PRD 6 (`GET /api/products/{id}`), prototype detail block (`showDetail`).
No keyboard shortcuts (user decision). Decisions are held on the page only; storing comes in step 3.

## Step 1 design (written before coding)

**Migration 017 `017_review_app.sql`** (all of Build PRD 3, plus 11's data columns so later steps need none):
- `tagging_runs`: stage check adds `review`; add `product_id text`, `parent_run_id bigint` (FK runs),
  `taxonomy_version int`.
- `catalog_products`: add `approved_at`, `approved_by`, `ai_run_id` (FK runs), `category_review_outcome`,
  `category_was_flagged bool`, `category_reviewed_at` (set when a reviewer approves/changes the category,
  so it is no longer flagged), `review_opened_at`, `approved_via` (detail|list|bulk).
- `product_facet_tags`: evidence check adds `human`; add `review_reason`, `review_outcome`
  (approved_unchanged|changed|set_unknown|not_reviewed), `review_was_flagged bool` (fixes "changed though
  confident" at approval time). Trigger: allow runs with stage `review`.
- `facets`: `review_threshold numeric(0..1)`, `updated_at`, `edited_in_app bool default false`.
  Seed review_threshold from auto_threshold only where it differs from 0.80 (aesthetic, occasion,
  formality = 0.70). `facet_values.is_active bool default true`.
- `facet_changes` table; `app_settings` single row: default_review_threshold 0.80, category_review_threshold
  0.75, taxonomy_version 1, categorise_model 'haiku', tag_model 'sonnet', projection_catalog_size 3000000.
- Backfill: ai_run_id = latest non-drill tag run per product; current_run_id = ai_run_id where unpublished;
  review_reason from status and reason text.
- Views: `v_product_review_state` (state, counts, flagged facet ids, lowest confidence, category flag),
  `v_review_outcomes`; `v_run_costs` excludes stage review.

**Flag rule (one SQL definition, used by the view and the Confidence preview):** a model-written row
(evidence not `rule`) is flagged when (status `review` and review_reason <> `low_confidence`) or (it has
a value and confidence < coalesce(facet.review_threshold, default)). Exempt: answers equal to the facet's
default value (the 13 Nov default-value rule). Category flagged: category_confidence < category level and
category_reviewed_at is null.

**Pipeline read points moved to the DB:** FACET_SQL level = coalesce(review_threshold, default);
`value_status` applies the level to every value answer (decision 2); `tagger.dbsettings.apply(settings,
conn)` overrides category gate and default models from app_settings at the start of every run (CLI
model flags still win; unknown profile name stops with a message); tag_one sets ai_run_id and, when not
approved, current_run_id; start_run records taxonomy_version; `tagger publish` no longer sets
is_published; facets_for_tagging skips inactive values. Seed edited_in_app guard: step F.

## Step 3 design (go-ahead 14 Nov; no push notifications wanted)
`src/app/review.py` (one transaction per action), `src/app/jobs.py` (in-process runner, one job at a time,
used by step 4 too). Endpoints: `PUT /api/products/{id}/review` (Save), `POST /api/products/{id}/approve`
(detail, or empty body from the list = Ready only), `POST /api/products/approve-ready` (filters),
`POST /api/products/{id}/unapprove`, `PUT /api/products/{id}/category` (approve as shown / change → job),
`GET /api/jobs/{id}`.
- Review run: tagging_runs stage `review`, product_id, parent_run_id = ai_run_id, model `reviewer`. Save
  writes only the decided facets. Decision → row per Build PRD 5 table (approved = copy as auto; changed =
  auto/human/null confidence/"Changed by reviewer (was: …)"; unknown = abstained/human).
- Approve: decisions (body, else saved) + untouched facets copied from the tagger run; an untouched row in
  `review` with a value that is not flagged at the current level is copied as `auto` (else the website,
  which shows only auto rows, would hide it). Refuse with a message per facet if a flagged facet has no
  decision, a required facet has no value, or v_invalid_tags gets a row. Sets outcomes + was_flagged,
  category outcome, category_status auto, current_run_id = review run, is_published, approved_at/by/via.
- Unapprove: is_published false, current_run_id = parent_run_id, approved_at null, outcomes cleared;
  review run kept so decisions reappear.
- Category: approve as shown → category_reviewed_at, outcome approved_unchanged, re-tag only if no tag run
  under this category; change → new category, status auto, reviewed_at, outcome changed, saved review run
  deleted, re-tag job (paid model call with the app_settings tag model).

**Decision taken in step 3 (tell the user):** "required" for approval and the Required marker = the
dictionary marks it required for the category AND its policy is `review_if_required` (the 14 facets the
pipeline sends to review when empty). Facets whose policy is `unknown` may stay unknown even when the
dictionary marks them required (e.g. gender_positioning). The stricter reading would move 6 of 24 Ready
products to Needs review.

## Step 3 progress
- [x] Backend (14 Nov): `src/app/{review,jobs,pipeline}.py`; endpoints PUT review, POST approve (list,
      Ready only), POST approve-detail, POST approve-ready, POST unapprove, PUT category (+ re-tag job),
      GET jobs/{id}; detail returns `saved` decisions and stamps review_opened_at. Tested on real data:
      list approve publishes (website views show it), tagger rows byte-identical, v_invalid_tags 0, undo
      unpublishes, Needs review refused from the list, outcomes view 2 reviewed / 50% changed, re-tag job
      $0.06 in 27 s. Test review runs cleaned up; nothing left approved.
- [x] Front end wiring (14 Nov): Toast + ConfirmDialog providers; list Approve / Approve all ready / Undo;
      detail Save / Approve and publish / Send back / Save changes / category approve+change with job polling;
      saved decisions loaded on open. HTTP round trip checked (approve → Approved tab → undo; list refusal).
- [x] Tests: `tests/test_app_review.py` (6, rolled back): ready approve/undo, refusals write nothing, changed
      value is human + tagger rows unchanged, outcomes 3 reviewed, `tagger publish` publishes nothing,
      evaluate unchanged by approval. App code commits through `app.db.commit/end` (Db commit flag).
- Follow-ups (step 5): problems carry no facet id (rows not marked, banner lists them); leaving with
  unsaved changes uses window.confirm; `saved.categoryDecision` now returned (approved|changed) but the
  page does not use it yet. One real product (220151041_26) had its category approved and was re-tagged
  during testing (run 1109, $0.06); it is unpublished.
STEP 3 DONE (14 Nov), awaiting go-ahead for step 4 (Live tag).

## Step 4 progress (go-ahead 14 Nov)
- [x] Scraper: `src/scraper/live.py` (`classify`, `listing_links`, `scrape_one`, same Client/store rules;
      Live tag uses refresh=True so it never reads the old disk cache).
- [x] App: `src/app/live.py` + endpoints POST live-tag/check, POST live-tag (mode checked server-side),
      POST jobs/{id}/retry, POST jobs/{id}/stop, POST jobs/{id}/items/{n}/retry; GET jobs/{id} adds item
      states. Pauses with status `waiting_for_category` when the category comes back `review` (the page then
      uses PUT category, which starts the tag job). Listing: 3 at a time, one request at a time.
- [x] Real checks: product 220232969_14 tagged in 45 s for $0.06 (fetch 11, categorise 9, tag 24);
      listing X=3 → 2 already tagged, 1 new (220038313_14) $0.05. Tests `tests/test_app_live.py` (9).
- NOTE: the user approved 24 products with "Approve all ready" at 03:22 on 14 Nov (+1 from the detail page).
      Tests no longer assume nothing is published.
- [x] Front end Live tag page (14 Nov): `pages/LiveTag.tsx`; detail body refactored into `ProductReview`
      (embedded on Live tag; `/products/<id>` unchanged). Fixes after review: recent links only fill the box
      (no paid run on one click); listing rows show real photos (`thumb` in item states); list shows
      "N reviewed" (nResolved). 16 app tests pass. Known gaps: evidence appears after categorising (no product
      endpoint before categorisation); a listing job that fails reading the page has no retry.
STEP 4 DONE (14 Nov), awaiting go-ahead for step M (Metrics).
- [x] Step M, Metrics tab (14 Nov). Migration 020 `v_review_outcome_rows` (rows behind v_review_outcomes, which
      is rebuilt on it, same figures) so outcomes filter by run and category. `tagger.evaluate`: every share
      now carries {num, den} in its detail (holdout too); `evaluate(..., products=, facet=)` narrows scoring for
      the app's filters (never saved). CLI output unchanged. `src/app/metrics.py` + `/api/runs`, `/api/metrics`;
      `web/src/pages/Metrics.tsx` (tiles, run line, filters, blocks 1-4, inline SVG charts). Whole response
      cached per run+filter, keyed on a one-query data stamp (first load ~8 s, then ~0.3 s). Default run: the
      latest tag run with >= 10 products (live tag makes one-product runs). Tests: tests/test_app_metrics.py (7).
      Checked: run 1003 tuned precision 95.8 / recall 84.3 / coverage 78.5 / unknown agreement 91.3 = CLI.
      Test fixes for live data: products count >= 241 (live tag adds rows); review tests pick products
      without saved decisions. Full suite: 168 passed, then the 4 data-dependent failures fixed (52 pass).
  Definition differences found (Build PRD 11 says stop and say; the page shows the CLI's numbers meanwhile):
  1. Calibration bands: evaluate uses 0.5-0.7 as the lowest band; PRD says "below 0.7" (1 answer < 0.5 in 1003).
  2. Coverage: evaluate = golden facet answers with an accepted value / golden facet answers; PRD = accepted
     tags / tag answers.
  3. Unknown agreement: evaluate = agreeing / golden unknowns (incl. not-applicable); PRD = / tagger unknowns.
  4. Invented quote rate counts evidence text and both (answers that carry a quote).
  5. Confidence shift (PSI) is not stored by monitor except in alerts: computed with monitor.stability_index
     on the stored bands; status = most severe of monitor's own compare_stat findings for the facet.
  6. Over-time review rate and coverage are monitor's per product-facet figures (run_facet_stats), not the
     tile's per-answer ones.
- [x] Metrics decisions applied (14 Nov, product owner): (1) calibration lowest band "below 0.7" in
      tagger.evaluate (BANDS 0.0-0.7; band mean confidence kept for the diagonal); (2) coverage = existing code;
      "Auto-accepted" = accepted / tag answers on the tile and the over-time chart; (3) "Correctly left unknown"
      (CLI label too); (4) invented quote denominator "answers that cite a quote"; (5) monitor table status read
      from stored monitor_alerts (segment all), PSI via monitor.stability_index, tested against compare_stat;
      (6) over-time points counted as the tiles, plus a "Now" point equal to the live tiles; (7) default scope
      = every tagged product at its latest tagging (catalog_products.ai_run_id); quality from the latest run
      covering >= half the golden set (1003); run picker narrows all; (8) Benchmark versus live table
      (`live_quality` scores the original answers against approved ones via score_tags, non-golden, detail-page
      approvals only; status from monitor rate/coverage limits once the live figure has >= 30 products).
      Finished-run figures are kept in memory; page ~6-10 s after an approval, then cached. 12 metrics tests.
  OPEN: decision 2 describes coverage as "/ golden answers that have a value"; the code divides by all golden
  answers (incl. unknown and not applicable): 78.5% vs 87.5% on 1003. Kept the code; asked the owner.
- [x] Facet scorecard: golden (tuned set) and live side by side for precision, recall, coverage, review rate,
      precision difference, drift status (rate limits) once a facet has >= 20 live answers; default sort alert,
      watch, lowest live precision. Golden review rate now counted per answer as the benchmark table counts, so
      it differs from the CLI scorecard's per-golden-answer review rate (largest gap: Colour Secondary 15% vs 5%).
      13 metrics tests.
- [x] Coverage (owner, 14 Nov): golden values whose facet got an accepted value / golden values, in
      tagger.evaluate (so recall <= coverage always). Run 1003: tuned 87.3% (817/936), holdout 88.3% (173/196).
- [x] Speed: metrics.py reads one snapshot (6 queries: meta json, run products, catalog + state, every tag row of
      non-drill tag runs and review runs, outcome rows) and computes in Python; golden evaluation cached per
      golden stamp; finished runs' cost kept. Stale-while-refresh: a changed data stamp returns the last page
      at once (`stale: true`) and recomputes in the background; the page polls every 2 s. Warmed at server
      start. Warm load 0.27 s; a fresh compute 4-7 s in the background. Old module kept in scratchpad only.
- [x] Bug (owner hit it): an exclusive value (Season all-season) could be saved with another value, so the
      product showed Ready but approval refused. Fixed: ValueEditor clears the others; save refuses with the
      reason; list approval toast now lists the problems. Test added. The one existing case (220339542_2709,
      review run 1373) is left for the owner to re-save; until then v_invalid_tags has 1 row and 3 tests fail.
- Full suite (before the swap): 176 passed, 3 failed (that one invalid tag), 1 skipped (expects the original
  50-product golden set).
STEP M DONE (14 Nov), awaiting go-ahead for step C (Confidence + Models).
- DECIDED (14 Nov, owner) for step C: lowering the category level tags the held products that now clear it,
  automatically, as approving a category by hand does; the preview shows the count and cost before Apply.
- [x] Step C (14 Nov): `src/app/settings_tabs.py` + `/api/confidence` (GET, one SQL query: per facet and
      Category, levels in force and proposed), `/api/confidence/preview`, `PUT /api/confidence` (one
      transaction, returns previous values for Undo; lowering the category level accepts held products that
      clear it and tags them in a `retag-held` job), `/api/models` GET/PUT. Pages `Confidence.tsx`,
      `Models.tsx`. The preview recomputes v_tag_flags' rule and 019's saved-decision rule for proposed levels;
      a test checks it agrees with the view at today's levels. `tagger models use` now also saves the choice
      to app_settings (before, the database silently won). Tests: tests/test_app_settings.py (12).
      Hand checks left for the owner: lower a level and Undo (needs unapproved products; all 104 approved now);
      switch the tagging model and live tag one product (paid).
STEP C DONE (14 Nov), awaiting go-ahead for step F (Facets tab).
- DECIDED (14 Nov, owner) for step F: facet and value definitions (the rubric sent to the model) are editable in
  the app; every save that changes a rubric bumps the facet's definition_version (v6 -> v6.1 -> v6.2; none ->
  v1) and stamps changed values with it; facet_changes keeps before/after with both versions.
- [x] Step F (14 Nov): `src/app/facets_tab.py`; `/api/facet-list` (the old `/api/facets` stays for the list
      filters), `/api/facets/{id}` GET/PUT, `/changes/{cid}`, `/restore`, `/test`, `/api/product-search`. Page
      `Facets.tsx`. Save: one transaction (facet, values, required flags per category, facet_changes before/after,
      taxonomy_version + 1), refused when any stored tag of the facet would then break v_invalid_tags. Rubric
      versions per facet (v6 -> v6.1). No Rubric card: the skill file has no per-facet sections; the database
      definition is the rubric and is shown as "Rubric sent to the model". "Required" sets
      category_facets.is_required for every category (mixed shown as "required in N of M"). Seed guard:
      `catalog seed-taxonomy` keeps app-edited facets (rules, values, required flags) unless `--force`.
      Tests: tests/test_app_facets.py (8). Hand check left: "Test on a product" (paid, ~5c).
STEP F DONE (14 Nov), awaiting go-ahead for step 5 (states, acceptance checks, tests, hand-over).
- [x] Step 5 (14 Nov): router leave guard (app dialog when leaving a page with unsaved edits: product page,
      Facets); refusal problems marked on the facet row; Facets field errors beside the field (ApiError.fields);
      grey placeholders and "Saving…"/"Applying…" on Metrics, Confidence, Models, Facets; plain words for
      statuses and types; Live tag "Try again" when the listing page cannot be read; list scroll position
      restored; 1280 px fit for the Metrics scorecard and the Confidence table. `pipeline.settings_for` ends
      its read with `end(db)` (it rolled back a test's transaction). tests/test_app_finish.py (15) covers the
      remaining Build PRD 9 items. README "Review app" section; docs/app/ACCEPTANCE.md; docs/app/HANDOVER.md.
      Not done (noted in the hand-over): photos before categorising in Live tag; a searchable category picker.

## User change (14 Nov): saved decisions make a product Ready
Migration 019: in v_product_review_state a flag is resolved once the product's latest (unapproved) review
run holds a decision for that facet; when every flag is resolved and the category is decided the product
is Ready and can be approved from the list (approval uses the saved decisions). New column `n_resolved`.
This overrides Build PRD 4 ("saved but unapproved stays Needs review"). Test added (7 review tests pass).
Front end follow-up: the Facets column could say "N reviewed" using n_resolved (not yet shown).

## 14 Nov: tagging the rest, and review sheets
- 140 untagged products (none golden) run with the frozen settings: categorise run 1649 (116 accepted, 14
  flagged, 10 held, $0.137), tag run 1650 (130 products, $5.70). No failures, no invalid tags.
- `tagger review export --run` / `tagger review import` (src/tagger/review_sheet.py) through app.review;
  migration 021 adds approved_via 'sheet'. Metrics review block splits outcomes by source (app / sheet); live
  precision uses detail-page approvals only, so sheet reviews stay out. tests/test_review_sheet.py (2).
- review/review_run1650.xlsx: 24 category rows, 189 facet rows, 107 products.
