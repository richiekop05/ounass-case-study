# Richie's store: plan, decisions and progress

Read this first in any new session. Sources (copy them into `docs/store/`): `engineering_prd.md` (Customer
Feature Engineering PRD, 14 Nov 2026), `design_prd.md` (Customer Feature Design PRD), `design_handoff/README.md`,
`design_handoff/Richies Store.dc.html` (+ `support.js`). Rules come from the engineering PRD; anything visual
from the design PRD and the handoff. Where the prototype's logic differs from the engineering PRD (shortlist
formula, slot templates, learning amounts), the engineering PRD wins.

## What exists (inspected 14 Nov 2026)

- Repo `ounass-scraper`: Python 3.13 with uv; FastAPI tagger app in `src/app/`, React 18 + TypeScript + Vite
  in `web/` (plain CSS tokens), Supabase Postgres, migrations in `db/migrations/` applied by `catalog migrate`
  (latest 022). Not a git repository.
- Approved products: `catalog_products` where `is_published` (222 now), exposed as `v_catalog_products`
  (name, brand, price, currency AED, description, details, size_and_fit, primary image, category, audience,
  department) with tags in `v_product_facets` (the published review run's accepted values).
- Images: `product_images` (position, `source_url` on Ounass's CDN, `storage_path` in a private bucket,
  signed by `app/photos.sign`).
- Departments: `categories.audience` (Women 138, Men 76, Kids 3, All 5) x `department` (Clothing, Shoes,
  Bags, Accessories, Jewellery, Watches, Beauty, Home).
- Vocabulary (active values) the rule tables must use:
  - occasion: everyday, work, evening, wedding guest, special occasion, Ramadan/Eid, National Day,
    resort/beach, travel, sport
  - formality (ordered): casual, smart-casual, smart, formal, black-tie
  - season: warm, transitional, cold, all-season
  - aesthetic: minimalist, classic, romantic, bold/statement, bohemian, streetwear, glamorous, modest-chic, sporty
  - colour_primary: black, white, neutral, grey, brown, blue, green, red, pink, purple, yellow, orange,
    burgundy, gold, silver, rose gold, metallic, multicolour, colourless, cream, beige, mud grey
  - sleeve_length, length, sheerness ("More covered" = long sleeves, midi or longer, not sheer)

## Decisions (owner, 14 Nov 2026)

1. **Git:** `git init`, commit the current state on `main` (with `.env`, caches and build output ignored),
   then build on branch `store`.
2. **Sample products come from scraping, not a JSON file.** A third Live tag mode in the tagger app, "Store
   sample (listing)": a listing URL and a number; each product is scraped, categorised and tagged by the normal
   pipeline (same models, skills and levels) and added for the store straight away, with no review.
   - Marked `catalog_products.source = 'sample'` (existing tagger-approved products are `tagged`), published
     for the store (`approved_via = 'sample'`, approver `sample-loader`).
   - Unsure ones are added anyway: a category below the gate is accepted as given and the product tagged; a
     required facet left empty stays empty. Catalog rules (`v_invalid_tags`) still hold.
   - Never shown in the tagger app's Products / Approved tabs or review queue, and excluded from Metrics,
     Confidence, review outcomes, evaluation and monitoring. Their tagging runs are marked as store-sample runs
     so run-level figures and drift monitoring skip them.
   - Every existing tagger flow stays exactly as it is.
3. **JSON loader only for persona histories** (`personas.json`: Layla, Noor, James, Sara). No invented products.
4. **Database vocabulary everywhere** (occasions, five formality levels, seasons). Noor's engagement is shown
   as "special occasion" / "wedding guest"; chip wording can still be friendly.
5. Defaults kept: FastAPI + React/Vite (not Next.js); engineering PRD slot templates (editable rows).

## Proposed structure

```
store/                      all store code (engineering PRD 10)
  richies/                  Python package: FastAPI server, engine, features, loader, CLI
    engine.py               filter / score / diversify (no page concerns, no persona names)
    features.py             occasion_shortlist, build_look, looks_for_you
    profile.py              taste profile and intent from events
    rules.py                rule tables, vocabulary validation
    loader.py               sample products, persona histories, coverage report, funnel
    server.py               /store/* endpoints, serves the built front end
  web/                      React + Vite front end (Richie's brand tokens)
  sample_data/              products.json, personas.json, rule seeds
  tests/
db/migrations/023_store.sql the new tables and columns (one `catalog migrate` for the whole project)
```

Run: `uv run richies` (store on http://127.0.0.1:8780), alongside `uv run tagger app` (8765); one shared
database. Tagger files touched: only metric, review-queue and monitor queries (`source = 'sample'`), plus
item 3 if agreed.

## Build order (engineering PRD 9), one checkpoint each

0. `git init`, commit on `main`, branch `store`.
1. Migration 023: `source`, `in_stock`, store-sample run marker; rule tables seeded (occasion profiles and
   outfit roles drafted for the owner's review); tagger exclusions + a test that samples change no tagger figure.
2. Live tag "Store sample" mode; persona histories loader; coverage report.
3. Engine with unit tests.
4. Store shell: header, persona bar, PLP grid, PDP, sessions, event log.
5. Complete the Look with swap.
6. Taste profile, session learning, behind-the-scenes panel.
7. Occasion assistant: occasions, shortlist, refinements.
8. Intent rule and high-intent layouts.
9. Looks for you.
10. Own words (only if all else is done).

## Progress

- [x] PRDs read, codebase and schema inspected, plan written, decisions recorded (14 Nov).
- [x] Step 0: git initialised; state committed on `main`; work on branch `store`.
- [x] Step 1 (14 Nov): migration 023 (`catalog_products.source`, `in_stock`, `approved_via 'sample'`,
      `tagging_runs.purpose`; tables `store_occasion_profiles`, `store_outfit_roles`, `store_look_templates`,
      `store_colour_pairings`, `store_score_weights`, `store_personas`, `store_events`; v_product_review_state,
      v_review_queue and v_run_costs wrapped to drop store samples). `uv run richies seed-rules` loads the rule
      tables from `store/sample_data/rules/*.json` and the spine, validated against the vocabulary (fails loudly).
      Tagger exclusions: app/metrics.py, app/listing.py (counts), app/settings_tabs.py, app/server.py (health
      count), tagger/monitor.py (baseline, history), tagger/steps.py (`products_to_tag`). Tests:
      store/tests/test_store_rules.py (16) incl. a sample product changing no tagger figure while the store sees it.
      tests/test_catalog_db.py fixtures now free a product inside their transaction (every product is categorised).
      Owner to review: occasion profile drafts, outfit roles flagged (activewear, swimwear, luggage, skiwear,
      beach accessories, suits, co-ords, watches, wallets), complementary colour pairs.
STEP 1 DONE.
- [x] Step 2 (14 Nov): Live tag third mode "Store sample (listing)" (`start_listing(sample=True)`, job kind
      `sample`): new products are categorised and tagged with runs marked `purpose = 'store_sample'`, marked
      `source = 'sample'` right after categorising (a held category is accepted as flagged so it can be tagged),
      then `review.publish_sample` publishes them through `approve` (via `sample`, approver `sample-loader`,
      review run purpose `store_sample`, every answer `not_reviewed`, required facets may stay empty). Products
      already in the catalog are skipped. Migration 024: `v_product_review_state_all` (019's definition) for the
      approval code; `v_product_review_state` = all minus samples. `richies draft-personas` / `load-personas`
      (seed events, validated against the store), `richies coverage`. Tests: store/tests/test_store_samples.py (4).
      Persona histories are DRAFTS for the owner to choose (personas.json). Paid hand check left: one store sample.
STEP 2 DONE.
- [x] Step 3 (14 Nov): `store/richies/engine.py`: `filter(candidates, rules)` (named rules, first failure
      recorded), `score(candidates, signals, weights)` (signals 0..1; a None signal is dropped and weights
      rescaled per product; ties break on ID), `diversify(ranked, limit, caps)`; shared rules and signals
      (season compatibility, formality range / centre / steps, occasion fit, tag confidence, price fit, occasion
      overlap, "more covered", colour score via `Rules`). `store/richies/catalog.py`: approved products (views
      only) + tags + outfit roles + rule tables into memory, re-read when a stamp changes, checked at most every
      5 s (warm load ~0 ms; the stamp query is a 0.3 s round trip). Tests: store/tests/test_engine.py (7, pure).
STEP 3 DONE.
- [x] Step 4 (14 Nov): `store/richies/server.py` (FastAPI; `/store/personas`, `/store/session`, `/store/products`,
      `/store/facets`, `/store/products/{id}`, `/store/events`, `/store/state`; persona and session in cookies;
      Sara's session starts with her search referral and size; photos signed by app/photos.sign), `uv run richies
      serve` (http://127.0.0.1:8780). Front end `store/web/` (Vite + React, built into store/richies/static):
      persona bar, header, toast, behind-the-scenes drawer (intent, profile placeholder, last five events, product
      tags with confidences / Sample marker), PLP (filter bar + drawer, applied pills, grid, placeholders), PDP
      (gallery, sizes, add to bag, About this piece, accordions). Photos shown uncropped in 3:4 frames (photos are
      2:3) on `--photo-bg`. Tests: store/tests/test_store_server.py (5). Sample-product drawer view not yet seen
      on screen (no samples in the store yet).
STEP 4 DONE.
- OWNER (14 Nov): proceed through the remaining steps without waiting for approval; at the end, list everything
  the owner must do. Paid model calls still need the owner's yes (only the optional own-words step uses one).
- [x] Step 5: `features.build_look` (PRD 6 filters, signals, two-colour rule, alternates, high intent), endpoint
      `/store/products/{id}/look?occasion=` with persona-agnostic reason wording; front end
      `components/CompleteTheLook.tsx` (slots, swap with cross-fade and counter, back, total, add look to bag,
      look_view / swap_out / swap_in / add_look_to_bag events, "Styling for" line). Tests: store/tests/test_look.py.
- [x] Steps 6, 7, 9 back end: `profile.py` (profile, taste match, event effects, intent rule), in-memory session
      events, effective intent set on page load (`/store/state?load=1`), taste-ordered grid; `/store/occasions`
      (past-purchase occasions first), `/store/shortlist`, `/store/looks-for-you`. Tests: store/tests/test_profile.py,
      more in test_store_server.py.
- [x] Steps 6-9 front end (profile panel, feature band, shortlist, refinements, opener, looks for you,
      high-intent layouts); suggestions from the most recent order's occasion-specific values; looks for you after
      5+ weighted events. `richies setup`, `serve --with-tagger`, `funnel`, `reset-events`. README section,
      docs/store/HANDOVER.md. Step 10 (own words) not built: paid model call, owner to decide.
ALL STEPS DONE except step 10 (14 Nov).
