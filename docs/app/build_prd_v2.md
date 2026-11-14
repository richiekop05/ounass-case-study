# Ounass Tagger App: Build PRD

Nov 13, 2026 · @Richard George

## 1. Goal and what to read first

Build the tagger app: a local web app with seven tabs (Products, Approved, Live tag, Metrics, Confidence, Facets, Models) where a person approves or corrects the tagger's output, and where approval is the only way a product reaches the sample website.

**Read before writing any code:**

1. The repository: the scraper, the tagging pipeline, its config, the migrations, and the views. Use the real table, column and function names you find; this PRD names them as the earlier PRDs specified them, and the build may differ.
2. The [Design PRD](https://claude.ai/artifact/RnfcQQjb7oCkkNb3u4bf1N): what every screen shows and how it looks. It is the source for all layout, wording and visual rules.
3. The [Review Screen PRD](https://claude.ai/artifact/VTW36QiCd5TTCjUhYccjwi), section 6 only: the keyboard shortcuts for the detail page.

**Which document wins**

| Topic | Source |
| --- | --- |
| What is on screen, and how it looks | Design PRD |
| What is stored and how the app is built | This PRD |
| Database rules for tags, categories and facets | Tables PRD, except where section 3 here changes them |
| How a product is categorised and tagged | Pipeline PRD, except that `tagger publish` no longer publishes (section 5) |

If two documents still disagree after that, stop and ask; do not pick one silently.

**Before you start, post a short plan:** the stack you will use and why, the schema changes as migration files, and anything in the repository that contradicts this PRD.

## 2. Stack and parts

The app has a server in the tagger's own language, so it can call the scraper and pipeline directly, and a small single-page front end.

| Part | Build | Notes |
| --- | --- | --- |
| Server | In the tagger's language and repository, as a new `app` module. A JSON API plus the built front end, served from one process. | Calls the scraper, pipeline, validation and config code as functions. It never shells out to the `tagger` command and never duplicates their rules. |
| Front end | React with TypeScript, built with Vite. Plain CSS using the design tokens as CSS variables. | No component library and no CSS framework. The Design PRD's components are few enough to write by hand, and a library would fight the tokens. |
| Database | The existing Supabase project. | The server uses the service key from `.env`. The browser never receives a key and never talks to Supabase directly. |
| Photos | The existing private bucket. | The server returns short-lived signed URLs. |
| Start command | `tagger app [--port N]` starts the server and opens the browser. | Listens on localhost only. No login. One reviewer. |

- If the repository's stack makes a different choice clearly simpler, say so in the plan and wait for a yes.
- Keep the front end in one folder with one file per component named in the Design PRD, section 12, so the sample website can reuse the evidence panel later.
- Add one line to the README: how to start the app.

## 3. Database changes

A reviewer's decisions are stored as a new run, never by editing the tagger's rows. That keeps the AI's original answers intact, so scoring against the golden set and run-to-run comparison stay honest after products are corrected.

**The idea.** The tables PRD already says re-runs insert new rows and the website follows `current_run_id`. A human review is treated as one more run, for one product:

- The reviewer's final answers for a product are written as tag rows under a run with stage `review`.
- On approval, the product's `current_run_id` points at that review run and `is_published` becomes true.
- The tagger's own run and its rows are left exactly as they were.

**Changes, as one migration**

| Object | Change | Why |
| --- | --- | --- |
| `tagging_runs` | Allow stage `review`. Add nullable `product_id` and `parent_run_id` (the tag run the review started from). | One review run per product; undo needs to know which run to return to. |
| `catalog_products` | Add nullable `approved_at` and `approved_by`. Add nullable `ai_run_id`. | The Approved tab's date. `ai_run_id` always holds the latest tagger run for the product, wherever `current_run_id` points. |
| `product_facet_tags` | Allow evidence `human` for a value the reviewer set. Such rows have null confidence and need no quote. | A changed value has no model evidence. The triggers and `v_invalid_tags` must accept it. |
| `v_product_review_state` | New view: one row per categorised product with its state and the counts in section 4. | The Products and Approved tabs read this, so the rule lives in one place. |
| `v_review_queue` | Leave as is. | Still correct for the tagger's runs. |

**Added for the Confidence tab.** Confidence levels move from the tagger's config file into the database, so the app can change them and the pipeline and the app always read the same values.

| Object | Change | Why |
| --- | --- | --- |
| `facets` | Add nullable `review_threshold`, numeric from 0 to 1. Null means "use the default". | One level per facet. |
| `app_settings` | New single-row table with `default_review_threshold` and `category_review_threshold`. Seed both from the values in the tagger's config today. | The default level and the level for the category step. |
| `product_facet_tags` | Add nullable `review_reason`: `low_confidence`, `missing_required`, `quote_not_found`, or whatever other reasons the pipeline has. The pipeline sets it whenever it gives a tag status `review`. Skip this if the pipeline already records the reason in a usable way. | The app must tell a tag that is in review only for low confidence, which a level change can clear, from one in review for another reason, which it cannot. |
| Pipeline config | The pipeline reads its thresholds from these tables at the start of every run and every live tag job. Remove the config-file values, or keep them only as the seed. | One source of truth. |

**Where the levels are today.** They may be in a CSV, in the config file, or in both, and there may be one level per facet or a single global one. Find every place the pipeline reads a confidence level from, and list them in the plan. Then:

- Seed the database from those values once, per facet where the source has per-facet values.
- Change every one of those read points to use the database. After this build, no code path reads a level from the CSV or the config file.
- Leave the CSV in the repository, with a first-line comment or a README note saying it was the original seed and is no longer read. Do not delete it and do not write app changes back to it.
- Test: change a level in the database only, run the pipeline on one product, and confirm the new level was applied.

**Added for the Facets tab.** The tab edits only what is already in the database: each facet's rules and its values. The skill files are not changed by this build, and the app never writes to them. Rubrics stay in the tagging skill file and are shown in the app read-only.

| Object | Change | Why |
| --- | --- | --- |
| `facets` | Add `updated_at` and `edited_in_app` (boolean, default false). Confirm the existing columns cover display name, definition, evidence policy, required, on-missing behaviour and maximum values, and list in the plan any that are missing. | The editable rules are already columns if the tables PRD was followed. |
| `facet_values` | Add `is_active` (boolean, default true), if not already present. | Retiring a value without deleting it. |
| `facet_changes` | New table: `change_id`, `facet_id`, `changed_at`, `changed_by`, `summary`, and `before` and `after` as JSON snapshots of the facet and its values. | History and Restore. |
| `app_settings` | Add `taxonomy_version` (integer), increased by one on every facet save. | Lets a run record which rules it used. |
| `tagging_runs` | Add nullable `taxonomy_version`, set by the pipeline at the start of each run. | Two runs can only be compared fairly if you can see whether the rules changed between them. |

**Showing the rubric.** The server reads the tagging skill file and returns each facet's section as text for display. If the file has no clear section per facet, return nothing and leave the Rubric card out; do not restructure the skill file to make this work.

**Added for the Models tab.** The list of models stays where it is, in the tagger's model profiles file, and the app does not edit that file. Only the choice of which two profiles to use is stored in the database.

| Object | Change | Why |
| --- | --- | --- |
| `app_settings` | Add `categorise_model` and `tag_model` (text, the profile names). Seed both from the defaults in the tagger's config today. | The app's selection. |
| Pipeline defaults | When a run does not name a model, the pipeline uses the two names in `app_settings`, read at the start of every run and every live tag job. A model named on the command line still wins. | One place decides the default, and a saved choice is used by the next product tagged. |

- A saved name must match a profile in the profiles file. If a stored name no longer matches one, the pipeline stops with a clear message; it does not fall back silently to another model.
- Each run already records which two profiles it used. Keep that; the Models tab's "results so far" are read from those run records and the existing scores.

**Added for review outcomes.** Alongside the review rate, the app records what the reviewer did with each facet, so it can report how many reviewed answers were changed and how many were approved as they stood.

| Object | Change | Why |
| --- | --- | --- |
| `product_facet_tags` | Add nullable `review_outcome`, set only on rows in a review run, at approval: `approved_unchanged` (was flagged, reviewer kept the AI's answer), `changed` (reviewer set a different value), `set_unknown` (reviewer cleared it), `not_reviewed` (was not flagged and was not touched). | Whether a facet was flagged is fixed at the moment of approval, so later changes to confidence levels do not rewrite history. |
| `catalog_products` | Add nullable `category_review_outcome` with the same values. | The category step is reviewed too. |
| `v_review_outcomes` | New view over approved products: per facet, and overall, the number reviewed (`approved_unchanged` + `changed` + `set_unknown`), the number in each outcome, and the change rate (`changed` + `set_unknown`, divided by reviewed). A second grouping by the model profile of the tagger run. | One definition, used by the app and by any report. |

- A value the reviewer changed on a facet that was not flagged counts as `changed` and is reported separately as "changed though confident", because it means the AI was confidently wrong.
- Undo or "Send back to review" clears the outcomes for that product; approving again sets them afresh.
- Only approved products count. Saved but unapproved decisions are not included.
- "Approve all ready" and Approve from the list write `not_reviewed` for every facet.

**Rules**

- The database is the only source for facet rules after this. `catalog seed-taxonomy` must refuse to overwrite a facet whose `edited_in_app` is true unless run with `--force`, and must say which facets it skipped.
- The pipeline reads facet rules at the start of every run and every live tag job, with no caching between jobs, so a saved edit is used by the next product tagged.
- An inactive value is never offered to the model. Existing tags that use it are kept and still shown.
- A facet save and its `facet_changes` row are written in one transaction.

**Rules that must still hold**

- Every rule in the tables PRD applies to review-run rows: allowed values, value limits, applicability to the category, single-value facets.
- `v_invalid_tags` returns no rows after any save or approval.
- Scoring, comparison and the unknown-rate and confidence-shift monitors ignore runs with stage `review`.
- The pipeline sets `ai_run_id` whenever it finishes tagging a product, and sets `current_run_id` to the same run only if the product is not approved.
- The website's views are not changed. They already show published products from `current_run_id`.

These changes alter the tables PRD and need a go-ahead before they are applied (section 10).

## 4. Product state

Every categorised product is in exactly one of three states, worked out by `v_product_review_state` from the tagger's latest run for it (`ai_run_id`).

| State | Rule | Shown in |
| --- | --- | --- |
| Approved | `is_published` is true. | Approved tab |
| Needs review | Not approved, and any of: the category is flagged; any tag in the tagger's run is flagged; the product has no tag run yet because its category is unsettled. | Products tab, with the Review button |
| Ready | Not approved, has a tag run, and nothing is flagged. | Products tab, with the Approve button |

- **When a tag is flagged.** A model-written tag is flagged when its confidence is below its facet's current level (`facets.review_threshold`, or the default when that is null), or when it is in review for a reason other than confidence (`review_reason` is not `low_confidence`). Rule-written tags are never flagged.
- **When the category is flagged.** Its confidence is below `category_review_threshold`, unless a reviewer has already approved or changed it.
- **The view compares against the current levels every time it is read.** The status the pipeline stored on each tag is a record of the level in force at that run and is not rewritten when a level changes. This is what lets a change on the Confidence tab take effect at once.
- The view also returns, per product: the number of applicable facets, the number flagged, the names of the flagged facets, and the lowest confidence among model-written tags.
- A product with a saved but unapproved review run is still Needs review. The detail page loads the saved decisions on top of the tagger's answers.
- A level change never alters an approved product.
- A product not yet categorised is not in the view.

## 5. Review and approval

Each action below is one database transaction: it fully happens or leaves no trace.

| Action | What the server does |
| --- | --- |
| Save | Creates the product's review run if there is none (stage `review`, `parent_run_id` = the tagger's run). Replaces that run's tag rows with the reviewer's decisions so far. `current_run_id` and `is_published` do not change. |
| Approve and publish | Does a Save, then fills in the rest: every facet the reviewer did not touch is copied from the tagger's run as it stands. Sets `category_status` to `auto`, `current_run_id` to the review run, `is_published` to true, and `approved_at` and `approved_by`. |
| Approve from the list | The same as Approve and publish with no decisions. Allowed only when the product's state is Ready; the server checks this, not just the page. |
| Approve all ready | Runs Approve from the list for each Ready product in the filter. One product failing does not stop the others; the response lists any that failed and why. |
| Undo, or Send back to review | Sets `is_published` to false, `current_run_id` back to `parent_run_id`, and clears `approved_at`. Keeps the review run, so the reviewer's decisions reappear on the detail page. |
| Save changes on an approved product | Replaces the review run's rows. The website shows the change on its next read. |

**How a decision becomes a row**

| Reviewer's decision on a facet | Row in the review run |
| --- | --- |
| Approved as shown | A copy of the tagger's row with status `auto`. Confidence, evidence, quote and reason are kept. |
| Changed to another value | Status `auto`, evidence `human`, confidence null, reason "Changed by reviewer (was: …)". |
| Set to unknown | Status `abstained`, no value, evidence `human`. |
| Not touched, and not flagged | A copy of the tagger's row, unchanged. |

**Checks before approval.** The server refuses, with a message per facet, if any flagged facet has no decision, a required facet has no value, or the result would put a row in `v_invalid_tags`. The page disables the button for the same reasons, but the server check is the one that counts.

**Changing the category.** Facets depend on the category, so the tagger's tags no longer apply.

1. The page asks for confirmation: "Changing the category re-tags this product. This takes about 30 seconds."
2. The server saves the new category with status `auto`, discards any saved review run for the product, and runs the pipeline's tag step for this one product under the new category.
3. The page shows the tagging progress, then reloads the facet rows from the new run.

Approving the category as shown, when it was in review and has no tags yet, triggers the same tag step.

**Publishing.** Remove the setting of `is_published` from `tagger publish`. After this build, the approval transaction is the only code that sets it to true.

## 6. API

The front end talks only to these endpoints. All return JSON; errors return a status code and a `message` the page can show as written.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/summary` | Counts for the nav badges and the summary line: ready, needs review, approved, tagged, total scraped; the threshold; the latest run ID. |
| `GET /api/products?tab=&state=&q=&department=&facet=&sort=&page=` | Rows for the Products or Approved tab from `v_product_review_state`, with a signed thumbnail URL each. 50 per page. |
| `GET /api/products/{id}` | Everything the detail page needs: product text by field, signed photo URLs, category with path, runner-up and confidence, every applicable facet with its tagger answer, any saved decision, allowed values and limits, and the product's state. |
| `GET /api/categories?q=` | Category search for the Change category box. Excludes holding nodes. |
| `PUT /api/products/{id}/review` | Save. Body: the decisions per facet. |
| `POST /api/products/{id}/approve` | Approve and publish. Body: the decisions per facet, or empty for a Ready product. |
| `POST /api/products/approve-ready` | Approve all ready. Body: the current filters. |
| `POST /api/products/{id}/unapprove` | Undo, or Send back to review. |
| `PUT /api/products/{id}/category` | Change or approve the category. Returns a job ID for the re-tag. |
| `POST /api/live-tag` | Start a live tag job. Body: the URL. Returns a job ID, or the existing product ID if the URL is already in the database. |
| `GET /api/jobs/{id}` | Job progress: the step reached, time per step, cost so far, the product ID once known, and any error. |

**Added for filters and the Confidence tab**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/products` extra parameters | `category=` (a category ID at any level; includes all categories beneath it), `facet=` with either `value=` or `flagged=true`. These replace `department=`. They combine with `tab`, `state`, `q` and `sort`, and `GET /api/summary` accepts the same filters so the summary line matches. |
| `GET /api/facets` | Facets and their allowed values, for the facet filter. |
| `GET /api/categories/tree` | The category tree with a product count per node, for the category filter. |
| `GET /api/confidence?category=` | One row per facet, plus Category: current level, whether it is the default, percentage confident, average confidence, unknown percentage, needs-review count, and the product count behind each figure. Worked out from the tagger's latest run per product. |
| `POST /api/confidence/preview` | Body: proposed levels for one or more facets or the default. Returns the new percentages and how many products would move in each direction. Writes nothing. |
| `PUT /api/confidence` | Applies the proposed levels in one transaction and returns the previous values, which the page sends back for Undo. |

- Work out the Confidence figures in SQL, in one query per request; do not load tags into the server to count them.
- Preview and the real state view must share one definition of "flagged", so a preview never disagrees with the result.

**Added for the Facets tab**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/facets?category=&q=` | The facet list for the left pane: name, group, number of values, edited marker. |
| `GET /api/facets/{id}` | One facet in full: basics, judging rules, rubric, values with guidance, use counts and active state, the categories it applies to, its confidence level, and its last five changes. |
| `PUT /api/facets/{id}` | Save. Body: the editable fields and the full list of values. Validates, writes the facet, its values and a `facet_changes` row, and increases `taxonomy_version`. Returns how many products were tagged with the earlier version. |
| `POST /api/facets/{id}/restore` | Body: a change ID. Saves that change's `before` snapshot as a new change. |
| `POST /api/facets/{id}/test` | Body: a product ID or an Ounass URL. Starts a job that tags that one product with the current rules (scraping it first if it is new), and returns a job ID. The finished job carries this facet's answer from the product's previous tagger run and from the new one. |

- The server rejects edits to read-only fields even if the request includes them.
- Deleting a value is not offered by the API. Values are only added, edited or made inactive.

**Added for the Models tab**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/models` | Every profile from the profiles file: name, model, prices, whether it reads photos. Which profile is selected for each step. Per profile and step, from past runs where they exist: golden set accuracy, average cost per product, average time per product. |
| `PUT /api/models` | Body: `categorise_model`, `tag_model`, either or both. Checks each is a known profile, then saves to `app_settings`. |

- `GET /api/summary` also returns the two selected model names, for the rail footer.
- The job result for Live tag and "Test on a product" includes the two profiles used.
- No endpoint returns an API key or any other secret from the model configuration.

**Added for review outcomes.** `GET /api/confidence` also returns, per facet and for Category, the figures from `v_review_outcomes`: reviewed, approved unchanged, changed, change rate, and changed though confident. `GET /api/summary` returns the overall figures.

**Added for listing pages.** `POST /api/live-tag/check` takes a URL and returns its type (product, listing or neither) and, for a listing, the time and cost estimate for a given X; it makes no request to the Ounass site. `POST /api/live-tag` accepts an optional `limit` for a listing URL. `GET /api/jobs/{id}` returns, for a listing job, the list of items with position, product ID once known, step, state and any error, plus the running totals. `POST /api/jobs/{id}/stop` stops a listing job, and `POST /api/jobs/{id}/items/{n}/retry` retries one failed item.

- Validate every request on the server against the taxonomy tables, whatever the page already checked.
- A request for a product that has changed state since the page loaded returns a clear conflict message, and the page reloads the product.
- No endpoint returns the service key, a storage path that is not signed, or a raw database error.

## 7. Live tag job

A live tag job runs the existing scraper and pipeline on one Ounass URL in the background and reports each step, so the page can fill in as the work happens.

**Steps**

1. **Check the URL.** Accept only product pages on the Ounass website's own domains, using the scraper's existing URL rules. Reject anything else before any network call.
2. **Look it up.** If the product URL is already in the raw table, return that product's ID and stop; "Tag again" starts from step 4.
3. **Fetch.** Run the scraper for this one URL. Save the raw row and photos exactly as a normal scrape does.
4. **Categorise.** Run the categorise step for this product. If `category_status` comes back as `review`, the job pauses in a `waiting_for_category` state until the reviewer decides the category (section 5), then continues.
5. **Tag and derive.** Run the tag step and the derive step. Set `ai_run_id`, and `current_run_id` if the product is not approved.
6. **Done.** Report total time and cost.

**Rules**

- Use the pipeline's default models and the same skill files, config and threshold as a batch run. A live-tagged product must be indistinguishable in the database from a batch-tagged one.
- Jobs run in the server process, one at a time; a second request while one is running gets a clear "busy" message. Job state is kept in memory, since the results are in the database.
- The page polls `GET /api/jobs/{id}` once a second. No web sockets.
- Each step has a timeout. A failure records the step, a plain-language reason, and leaves whatever earlier steps saved. "Try again" resumes from the failed step.
- A failed or half-finished job never publishes anything; only approval does.
- "Tag again" creates a new tagger run for the product. If the product was approved, it stays approved and live on its review run until the reviewer sends it back to review.
- Target: under two minutes from paste to result. Log the time per step so a slow step is visible.

**Listing pages: tag the first X products**

A live tag job can also start from an Ounass listing page. It finds the first X product links on that page, then runs the single-product steps above for each one.

**First, check the scraper.** Before building this, read how the 241 products were collected and report in one line which of these is true:

- The scraper already has a listing step that collects product links from a listing page and then visits each product page. Reuse that step.
- The scraper only takes product URLs. Add a listing step to the scraper module, next to the existing code and following its conventions for requests, delays and errors.

Either way, the product details always come from each product's own page, through the existing product scrape. Nothing is taken from the listing tile except the link and its position.

**Steps**

1. **Classify the URL** as product, listing or neither, using the site's URL patterns. Neither is rejected before any network call.
2. **Read the listing page once** and collect product links in page order, skipping duplicates and anything that is not a product link. Take the first X. Do not follow pagination or scroll for more.
3. **Create one job** with a child item per product, in page order. Mark products already in the raw table as "already tagged" and skip them.
4. **Run the items** through fetch, categorise, tag and derive, at most three at a time, with the scraper's usual delay between requests to the site.
5. **Finish** with totals: tagged, already tagged, failed, time and cost.

**Rules**

- X is from 1 to 25. The server enforces this.
- A category below its confidence level does not pause a listing job. The item ends as "needs review" without a tag run, as it would in a batch run.
- An item failing records its reason and does not affect the others. Retrying an item reruns it from its failed step.
- "Stop" lets items in progress finish and cancels the ones not started.
- The estimate shown before starting is X times the average time and cost per product from past runs with the current models, or a fixed fallback when there are none.
- If the listing page loads its products with scripts and the scraper cannot see them in the page as fetched, say so and stop; do not add a headless browser without a go-ahead.
- The one-job-at-a-time rule still applies: a listing job is one job.

**Single product or listing: detection and the toggle**

- `POST /api/live-tag/check` is the only place that decides a link's type. The page calls it whenever the link changes and sets the toggle from the answer. Do not duplicate the URL patterns in the front end.
- Base the detection on the real URL shapes of the Ounass site, checked against the product and listing links already in the raw table, and cover it with a test of at least ten real product links and ten real listing links.
- The number field is rendered only when the toggle is on "Listing page". It must not appear, even disabled, in single-product mode.
- `POST /api/live-tag` takes the mode the reviewer chose as well as the URL. The server checks the two agree and refuses with a clear message when they do not; it never guesses and runs anyway.
- A `limit` sent with a single-product request is ignored.

## 8. Build order

Build in five steps, each ending in something that can be opened and checked. Stop after each step, say what works, and wait for a go-ahead before the next.

| Step | Build | Checkpoint |
| --- | --- | --- |
| 1. Foundations | The plan from section 1. The migration and the `v_product_review_state` view. The server skeleton with `GET /api/summary` and `GET /api/products`. The app shell, design tokens and nav. | `tagger app` opens the Products tab listing real tagged products with the right Approve or Review button. Buttons do nothing yet. |
| 2. Detail page | `GET /api/products/{id}`. The evidence panel, category card, facet rows, value editor and confidence badge. Quote highlighting. Keyboard shortcuts. | Any product opens with photos, text and every facet. A facet can be approved or changed on the page; nothing is stored yet. |
| 3. Save and approve | The review run, Save, Approve and publish, Approve from the list, Approve all ready, Undo, the Approved tab, and the change to `tagger publish`. Category change with re-tag. | Approving a product makes it appear on the website's listing data and in the Approved tab. Undo removes it. The tagger's own rows are unchanged. |
| 4. Live tag | The job runner, the two job endpoints, the Live tag tab with its step tracker and special cases. | Pasting an Ounass URL that is not in the database produces a tagged product on the same page, ready to approve or review. |
| 5. Finish | Loading, empty and error states. The acceptance checks in the Design PRD, section 14. Tests from section 9. | Every acceptance check is ticked, with a note against any that could not be met. |

If time runs short, steps 1 to 3 are a complete, demonstrable app. Step 4 is the one the panel will ask for, so do not spend step 5 time before it works.

**Additions to the order.** Build the category and facet filters as part of step 1, and include the threshold tables and `review_reason` in step 1's migration, since the state view depends on them. Build the Confidence tab as its own step after Live tag and before Finish, with this checkpoint: lowering one facet's level in the tab visibly moves products from Needs review to Ready, and Undo moves them back.

**Facets tab in the order.** Build the Facets tab as the last feature step, after the Confidence tab and before Finish. Before starting it, confirm in one line that the pipeline already takes each facet's rules and allowed values from the database on every run; if any of them are hard-coded or read from a file instead, say which and stop. Checkpoint: add a value to one facet in the tab, press "Test on a product", and see that facet's answer before and after.

**Models tab in the order.** Build it in the same step as the Confidence tab; both are small settings pages on the same table. Checkpoint: switch the tagging model, run Live tag on one product, and see the new model named in the result.

## 9. Tests and definition of done

The build is done when the Design PRD's acceptance checks pass and the automated tests below pass against a test copy of the database, never the live one.

**Automated tests**

- [ ] State view: a product with all tags `auto` is Ready; one `review` tag makes it Needs review; a category in `review` makes it Needs review; a published product is Approved.
- [ ] Approving a Ready product creates a review run whose rows match the tagger's rows, points `current_run_id` at it, and sets `is_published` and `approved_at`.
- [ ] Approving with one changed value stores that value with evidence `human`, and the tagger's original row is byte-for-byte unchanged.
- [ ] Approval is refused while a flagged facet has no decision or a required facet has no value, and nothing is written.
- [ ] Approve from the list is refused by the server for a Needs review product.
- [ ] Undo restores `current_run_id` to the tagger's run and unpublishes; approving again reuses the saved decisions.
- [ ] Changing a category discards the saved review run and produces tags only for facets that apply to the new category.
- [ ] `v_invalid_tags` is empty after each of the above.
- [ ] `tagger publish` no longer changes `is_published`.
- [ ] `tagger evaluate` gives the same score for a tagger run before and after its products are corrected and approved.
- [ ] A live tag job on a saved test page runs all steps and leaves the same rows as a batch run of that product. Model calls are stubbed.
- [ ] A live tag job with a non-Ounass URL makes no network call and returns the error message.
- [ ] A failure injected at each job step leaves nothing published and reports that step.
- [ ] No API response contains the service key or an unsigned storage path.

* [ ] A tag with confidence 0.75 is flagged at level 0.80 and not at 0.70; a tag with `review_reason` `missing_required` is flagged at any level.
* [ ] A facet with no level of its own follows the default; changing the default changes its state, and does not change a facet that has its own level.
* [ ] Preview returns the same counts that the state view shows after the same change is applied.
* [ ] Applying a level changes no row in `product_facet_tags` and no approved product.
* [ ] The pipeline uses a changed level on its next run without a restart or a config edit.
* [ ] The Confidence figures for a category filter equal the figures computed by hand for a small fixture.
* [ ] The category filter includes descendants; the facet filter by value and by flagged each return the expected products.

- [ ] Saving a changed judging rule or a new value changes the request built for the next product, with no restart.
- [ ] No code path in the app writes to the skill files; their contents are identical before and after the full test run.
- [ ] A facet save writes a `facet_changes` row with correct before and after snapshots and increases `taxonomy_version`; a run started afterwards records the new version.
- [ ] A value in use cannot be removed; making it inactive removes it from the request and leaves its existing tags and `v_invalid_tags` untouched.
- [ ] A new value can be chosen by the model and by a reviewer, and passes the allowed-value rules.
- [ ] Restore reproduces the earlier facet exactly and records a new change.
- [ ] `catalog seed-taxonomy` skips facets edited in the app and reports them.
- [ ] A facet save changes no row in `product_facet_tags` and no approved product.
- [ ] Edits to read-only fields, including the rubric, are rejected by the server.

* [ ] After saving a different tagging model, the next single-product run records that profile on its run, with no restart.
* [ ] A model named on the command line overrides the saved choice for that run only.
* [ ] Saving an unknown profile name is refused; a stored name with no matching profile stops the pipeline with a clear message.
* [ ] `GET /api/models` matches the profiles file and contains no secret.

- [ ] Approving a product with three flagged facets, one kept, one changed and one set to unknown, writes those three outcomes and `not_reviewed` on the rest; `v_review_outcomes` shows 3 reviewed and a change rate of 2 in 3.
- [ ] Changing a confident facet is counted under "changed though confident" and not in the reviewed total.
- [ ] Changing a facet's confidence level after approval does not alter any stored outcome.
- [ ] Undo removes the product from the outcome figures; approving again restores them.

* [ ] From a saved listing page fixture, the listing step returns the product links in page order, without duplicates or non-product links, and stops at X.
* [ ] A listing job for X = 3 produces the same rows per product as three single-product jobs. Model calls and site requests are stubbed.
* [ ] A product already in the raw table is skipped and reported as already tagged.
* [ ] An injected failure on item 2 of 3 leaves items 1 and 3 complete and item 2 retryable.
* [ ] A limit of 0 or 26 is refused by the server.
* [ ] No more than three items run at once, and requests to the site respect the scraper's delay.

**Check by hand, once, with real model calls**

- [ ] Live tag three real Ounass products not in the database, from three different departments, and record the time and cost of each.
- [ ] Approve one product and confirm it appears in the website's views with the values shown on screen.
- [ ] Walk through all three tabs at 1280 pixels wide and compare each against the Design PRD.

**Hand over** a short note: how to start the app, what was built, any check not met and why, and the three live tag timings.

## 10. Out of scope, and decisions that need a go-ahead

**Out of scope**

- The sample website's listing and product pages.
- The run dashboard.
- Golden set review; the golden tables are never read or written by this app.
- Any change to the skill files. Rubrics and general instructions stay there and are not editable in the app.
- Creating or deleting a facet, changing a facet's type, and changing which categories a facet applies to. The Facets tab edits existing facets only.
- Editing the category tree or the categorising rules.
- Re-tagging the whole catalog from the app after a facet edit. That stays a command-line run.
- A different confidence level for the same facet in different categories.
- Logins, several reviewers, phone layouts, deployment anywhere but the local machine.
- Editing product text or photos.

**Needs a go-ahead before it is built**

1. **The schema changes in section 3.** They add a run stage, an evidence value, about a dozen nullable columns, two small tables (settings and facet change history) and one view. Nothing existing is dropped or renamed.
2. **Removing publishing from `tagger publish`.** After this, a tagged product is not on the website until approved in the app.
3. **Moving confidence levels from the config file to the database.** After this, levels are changed in the app's Confidence tab, not by editing config.
4. **Storing the default model choice in the database.** The model profiles file is not changed; only which two profiles are the default moves to the app's Models tab.
5. **Any stack choice other than the one in section 2.**

**Assumptions to confirm in the plan**

- The pipeline already has a single-product path the server can call as a function. If it only exists as a command, refactor it into a function first, with no change in behaviour.
- The scraper can fetch one URL on demand from the server process.
- The tagger's `review` status already covers both "below the threshold" and "required value missing". If it does not, say what it covers and stop.

## 11. Metrics tab

The Metrics tab is a read-only page that reports on one tag run. It makes no model call and writes nothing except the two timestamps in the data table below. What it shows is in the Design PRD, section 11; this section says how each number is calculated. Both follow the "Tagger app: Metrics tab requirements" document.

**The first rule: one set of definitions.** The page must give the same numbers as `tagger evaluate` and the facet scorecard for the same run. Call the existing evaluation and monitoring code, or read what it has stored; do not write a second implementation of any metric it already produces. Where a definition below differs from what that code does, stop and say so; do not pick one.

**Terms**

| Term | Meaning |
| --- | --- |
| Tag answer | One model-written answer for one product and facet in the selected tag run. Rule-written rows, not-applicable rows and retired facets are excluded everywhere. |
| Accepted | A tag answer the tagger stored as accepted without review (status `auto`). |
| In review | A tag answer the tagger stored as needing review (status `review`). |
| As stored | Run-level metrics use the statuses stored on that run, so a past run's numbers do not change when a confidence level is edited later. Only "Product progress" uses the live state from section 4. |
| Tuned set, holdout | The two parts of the golden set. Use the marker the golden tables already carry. If there is none, stop and ask how the split is recorded; do not infer it. The two are never added together. |
| Scored product | A golden product whose category from the tagger matches the golden category. Only scored products count towards tag metrics. |
| Reviewed tag | A tag that was in review and that a person approved or changed in the app: `review_outcome` is `approved_unchanged`, `changed` or `set_unknown` (section 3). |

**Top strip**

| Tile | Number | Supporting line |
| --- | --- | --- |
| Tag precision | Precision on the holdout. If no holdout product is labelled, precision on the tuned set, flagged as such. | Tuned-set precision and the number of answers scored. |
| Auto-accepted | Accepted ÷ tag answers. | Accepted count and total. |
| Review rate | In review ÷ tag answers. | Products with at least one tag in review. |
| Category accuracy | Accepted categories that match the golden category ÷ accepted categories on golden products. | Number of categories flagged and held in the run. |
| Tagging cost per product | As in block 3. | Projection at standard pricing. |

**Block 1: quality.** Each metric is calculated twice, once on the tuned set and once on the holdout.

| Metric | Calculation |
| --- | --- |
| Precision | Accepted tags that match the golden answer ÷ accepted tags, on scored products. |
| Recall | Accepted tags that match the golden answer ÷ golden answers that have a value, on scored products. |
| Coverage | Accepted tags ÷ tag answers, on scored products. |
| Facet scorecard | Per facet: number of answers, average confidence, precision, recall, coverage, review rate. Return the number of golden answers per facet so the page can mark those under 20 as a small sample. |
| Calibration | For each confidence band (below 0.7, 0.7 to under 0.8, 0.8 to under 0.9, 0.9 to 1.0) and each evidence type (text, photo, both): answers that match the golden answer ÷ answers in that cell, with the count. All tag answers with a value count here, accepted or in review. |
| Unknown agreement | Tagger "unknown" answers where the golden answer is also unknown ÷ tagger "unknown" answers. |
| Invented quote rate | Text-evidence answers whose quote is not found in the product text ÷ text-evidence answers. Use the pipeline's own quote check; do not re-match quotes with different rules. This one is counted on all products in the run, not only golden ones. |

For a multi-value facet, each value is one answer, as in the existing evaluation.

**Block 2: review**

| Metric | Calculation |
| --- | --- |
| Review rate | In review ÷ tag answers. |
| Products needing review | Products with at least one tag or the category in review ÷ products tagged in the run. |
| Approved without change | Reviewed tags with outcome `approved_unchanged` ÷ reviewed tags. |
| Changed on review | Reviewed tags with outcome `changed` or `set_unknown` ÷ reviewed tags. With the line above, always 100%. |
| Accepted tags corrected | Accepted tags a person changed ("changed though confident", section 3) ÷ accepted tags, on products approved through the detail page. |
| Review outcomes per facet | The two outcome counts and shares, per facet, from `v_review_outcomes`. |
| Category overrides | Categories that were flagged or held and that a person changed ÷ flagged or held categories a person has reviewed. |
| Product progress | Live counts of ready, needs review and approved, from `v_product_review_state`. |
| Review time per product | Median of `approved_at` minus `review_opened_at`, over products approved through the detail page. Return the number of products it is based on. |

Review metrics count only products whose latest tagger run is the selected run.

**Block 3: cost.** Tag step only. No figure here includes the categorise run.

| Metric | Calculation |
| --- | --- |
| Tagging cost per run | The stored cost of the tag run. |
| Tagging cost per product | Tagging cost per run ÷ products tagged in that run. |
| Time per product | Tag run duration ÷ products tagged. |
| Projection for the full catalog | Tagging cost per product × the catalog size setting. Two figures: standard pricing, and batch pricing at half the standard rate. |
| Model comparison | For each model run on the same products: tagging cost per product and precision. Use the existing comparison of runs. Return nothing when no comparison run exists. |

**Block 4: health.** Read the monitoring the pipeline already stores (tagging pipeline PRD, section 9). Do not recalculate it.

| Metric | Source |
| --- | --- |
| Unknown rate per facet | Stored baseline and latest-run values, and the change in points. |
| Confidence shift per facet | The stored population stability index and its status (stable, watch, alert), using the limits already set in monitoring. |
| Review rate and coverage over time | One value of each per tag run, oldest to newest. |
| Open alerts | Monitoring alerts not yet closed: facet, what moved, by how much, when. |

**Block 5: business.** Not specified in the requirements document. Do not build it.

**Data**

| Object | Change | Why |
| --- | --- | --- |
| `catalog_products` | Add nullable `review_opened_at`: set the first time the detail page is opened for a product that is not approved, and cleared by Undo. Add nullable `approved_via`: `detail`, `list` or `bulk`. | Review time, and telling products a person actually looked at from those approved in one click. |
| `app_settings` | Add `projection_catalog_size`, default 3,000,000. | The catalog size for the cost projection. |

No other schema change. Everything else is read from the run records, the evaluation results, the monitoring tables, and the review outcomes in section 3.

**Filters**

- Run: the latest tag run by default. A run with stage `review` is never offered.
- Category: any level of the tree, including everything beneath it. Applies to blocks 1, 2 and 5.
- Facet: one facet. Applies to the facet scorecard, calibration and review outcomes.

**API**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/runs` | Tag runs for the picker: number, date, models, definitions version, products tagged. |
| `GET /api/metrics?run=&category=&facet=` | Everything for the page in one response: the run line, the top strip and blocks 1 to 4. Each number comes with its numerator and denominator. |

- Every metric is returned as a numerator and a denominator. When the denominator is zero, return null with a short reason code; the page turns that into a dash and a reason. Never return 0 for "nothing to count".
- The response for a run is the same every time until data changes, so it may be cached per run and filter, and cleared on any approval, undo or new run.

**Build order.** Build the Metrics tab straight after Live tag, before the Confidence, Models and Facets tabs. Build blocks 1, 3 and 4 first, since they need no reviews; then block 2. Draw the charts as plain inline graphics; no charting library is needed for two line charts and split bars. Checkpoint: the page for the latest run shows the same precision, recall, coverage and per-facet figures as `tagger evaluate` printed for that run.

**Tests**

- [ ] For a fixture run, every block 1 figure equals the output of `tagger evaluate` and the facet scorecard.
- [ ] Tuned-set and holdout figures are calculated on disjoint products; no response field combines them.
- [ ] Products whose category does not match the golden category are excluded from tag metrics and included in category accuracy.
- [ ] Calibration cells sum to the number of answers with a value, and each carries its count.
- [ ] Approved without change plus changed on review equals 100% of reviewed tags, overall and for each facet.
- [ ] Accepted tags corrected counts only products approved through the detail page.
- [ ] Block 3 figures use the tag run's cost only; adding cost to the categorise run does not change them.
- [ ] The projection equals cost per product times `projection_catalog_size`, and the batch figure is half the standard one.
- [ ] Block 4 values equal the stored monitoring values.
- [ ] A zero denominator returns null with a reason, not 0.
- [ ] Retired facets appear in no part of the response.
- [ ] Editing a confidence level changes no figure for a past run except product progress.
- [ ] The endpoint makes no model call and performs no write.
