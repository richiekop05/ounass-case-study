# Tagger app: acceptance checks (Design PRD 14, Build PRD 9)

Checked 14 Nov 2026. ✓ met · ◐ met with a note · ✗ not met (and why) · ☐ a check by hand still to do.
"Test" names a test in `tests/`; "seen" means checked on the running app with real data (screenshots at
1440 and 1280 px wide).

## Products and Approved tabs

| Check | | How |
| --- | --- | --- |
| Products lists every tagged product not approved, and no approved one | ✓ | `v_product_review_state`; test_app_finish `test_every_products_state_follows_the_rules` |
| "Approve" only when category and every facet clear the level and no required facet is missing; else "Review" | ◐ | Same test. Owner's change (14 Nov): a product whose flags all have saved decisions is Ready too (migration 019) |
| A level change on the Confidence tab moves products between the states | ✓ | test_app_settings `test_a_preview_writes_nothing_and_predicts_the_moves` |
| Row "Approve" publishes without opening; "Review" and a row click open the product | ✓ | test_app_review `test_approve_ready_from_list_publishes_and_undo_returns`; seen |
| "Approve all ready" approves exactly the filtered Ready products after a confirmation with the number | ✓ | Built in step 3; used by the owner (24 products on 14 Nov) |
| Filters, search and sort combine; the summary line matches the database | ✓ | Category replaces department (Build PRD 6). test_app_finish facet and category filter test |
| Approved tab: approval date and a working "View on website" | ✓ | Seen |
| Back link keeps filters and scroll position | ✓ | Filters kept since step 1; scroll position restored since step 5 |

## Detail page

| Check | | How |
| --- | --- | --- |
| Every applicable facet with value and confidence; flagged ones first | ✓ | Seen |
| A flagged facet can be approved or changed; only allowed values | ✓ | Allowed values enforced on save; an exclusive value ("all-season") can no longer be combined (bug found by the owner, fixed, test `test_an_exclusive_value_cannot_be_saved_with_another_value`) |
| "Approve and publish" disabled until every flag is decided and required values are present; the footer says what is left | ✓ | Step 3; refusals now also mark the facet row that blocks |
| "Save" keeps the product unpublished | ✓ | test_app_review |
| After approval: published in the catalog tables and the website's views, moved to Approved | ✓ | test_app_review (v_catalog_products); owner approved 104 products |
| Undo unpublishes and returns it | ✓ | test_app_review |
| Changing an approved product and saving changes the website | ◐ | Save on an approved product re-approves with the new rows (code path in `review.save`); no automated test |
| No product on the website unless approved here; `tagger publish` publishes nothing | ✓ | test_app_review `test_tagger_publish_no_longer_publishes` |
| `v_invalid_tags` empty after any save or approval | ✓ | Checked in the review and finish tests |

## Live tag

| Check | | How |
| --- | --- | --- |
| A new product link is scraped, categorised and tagged on the page in under 2 minutes | ✓ | Real: 220232969_14 in 45 s, $0.06 |
| Fills in step by step: photos and text, category, facets, with times and cost | ◐ | Steps, times and cost show as they finish; the product's photos and text appear once it is categorised, not straight after fetching |
| Result shows Ready / Needs review by the same rules, approvable on the page | ✓ | Same view; embedded product page |
| A category below the level pauses until decided | ✓ | test_app_finish `test_a_category_below_the_level_pauses_the_single_product_run` |
| Non-Ounass link, unreachable page, failed model call: message, nothing half-published | ✓ | test_app_live refusals before any request; test_app_finish failure at each step |
| A link already in the database shows the result with "Tag again" | ✓ | Seen |
| A live-tagged product appears in Products, and in Approved once approved | ✓ | Seen (owner) |

## Filters

| Check | | How |
| --- | --- | --- |
| Category filter at any level includes everything beneath | ✓ | test_app_finish filter test |
| Facet filter by value and by "needs review", on both tabs | ✓ | Same test (value); flagged filter uses the same list as the state view |
| Filters combine, show as chips, survive a reload | ✓ | Query string + chips (step 1) |
| "Approve all ready" acts only on the filtered products | ✓ | `approve_ready(Filters)` |

## Confidence tab

| Check | | How |
| --- | --- | --- |
| A row for every model facet and Category, figures match the database | ✓ | test_app_settings: agrees with the state view; a category filter matches figures worked out by hand |
| Editing shows the preview and changes nothing until Apply | ✓ | Preview writes nothing (test) |
| After Apply, Products, badges and the product page use the new level without a restart | ✓ | The state view reads the levels each time; the page refreshes the rail |
| Raising moves Ready to Needs review, lowering the reverse, except other-reason flags | ✓ | test_app_settings flag tests |
| Approved products unaffected | ✓ | test_app_settings |
| A product live-tagged after a change uses the new level | ✓ | The pipeline reads the level per product (test) |
| Undo restores the level and the counts | ◐ | Undo re-applies the previous values returned by Apply; ☐ owner to try (needs unapproved products) |
| The category filter changes figures, not levels | ✓ | By design (levels are per facet) |

## Facets tab

| Check | | How |
| --- | --- | --- |
| Every facet listed with name, definition, rules, values, categories, all matching the database | ✓ | Read straight from the database |
| A changed judging rule is saved and the next product tagged follows it | ✓ | test_app_facets: the next request carries the change |
| An added value is offered to the AI and in the value editor | ✓ | test_app_facets; a reviewer can save it and the rows pass the rules |
| A value in use cannot be deleted; inactive stops new use, keeps tags | ✓ | test_app_facets |
| Rubric read-only and matching the skill file; nothing writes the skill files | ◐ | Owner's decision (14 Nov): the skill file has no per-facet sections; each facet's rubric is its definition in the database, editable and versioned (v6 -> v6.1). Skill files are unchanged by the app and by the test run (hashes compared) |
| Saving changes no tag and no approved product | ✓ | test_app_facets `test_a_facet_save_changes_no_tag_and_no_approved_product` |
| Each save in History; Restore brings back the earlier version as a new change | ✓ | test_app_facets |
| "Test on a product" re-tags one product and shows before and after | ☐ | Built; a paid call (~5 cents), left for the owner |
| Invalid edits refused with a message on the field, nothing saved | ✓ | test_app_facets; field messages on the page |
| Read-only items cannot be edited | ◐ | ID, type, categories and rule-set facets are refused by the server; the rubric is editable by decision |

## Models tab

| Check | | How |
| --- | --- | --- |
| Exactly the profiles in the model list, with price and photo reading, the two in use marked | ✓ | test_app_settings |
| A new tagging model is used by the next Live tag, and "Done" names it | ◐ | The next run reads it (test); ☐ owner to try once (paid) |
| The two steps are set independently | ✓ | |
| A text-only model shows the warning | ◐ | Built on the card; no text-only profile exists to show it |
| Saving changes no tag and no approved product | ✓ | Writes `app_settings` only |
| Accuracy, cost and time match the scorecard | ◐ | Accuracy = the latest scored run's figure (`tagger evaluate`); cost and time are averaged over every run of that model, not one run |
| The rail footer shows both models and updates after a save | ✓ | |

## Review outcomes

| Check | | How |
| --- | --- | --- |
| Per facet and overall on the Confidence tab, matching the database | ✓ | Read from `v_review_outcome_rows` |
| Set to unknown counts as changed; a confident answer changed counts as "changed though confident" | ✓ | test_app_finish outcome tests |
| A later level change does not alter recorded outcomes | ✓ | test_app_settings |

## Live tag: single product or listing; listing pages

| Check | | How |
| --- | --- | --- |
| Detection picks the mode and shows or hides the number | ✓ | test_app_live (20 real links); seen |
| The other option overrides; a mismatch refuses to run | ✓ | test_app_live |
| First X products in page order, each scraped from its own page | ✓ | test_app_live (saved listing page) |
| Rows fill in one by one with Ready / Needs review and an action | ✓ | Real 3-product run |
| Already-known products shown as "Already tagged", not tagged again | ✓ | test_app_finish listing job |
| One failure does not stop the rest; it can be retried; "Stop" cancels the rest | ✓ | test_app_finish (failure and retry); Stop by code |
| Fewer than X: tags what is there and says how many | ✓ | |
| X above 25 refused | ✓ | test_app_live |
| No more than three at once; requests one at a time | ✓ | test_app_finish |

## Metrics tab

| Check | | How |
| --- | --- | --- |
| Strip, run line, filters, blocks 1 to 4, no Business block | ✓ | Seen |
| Same numbers as `tagger evaluate` and the scorecard | ✓ | test_app_metrics; run 1003 compared field by field |
| Tuned set and holdout side by side, never combined | ✓ | test_app_metrics |
| Small samples greyed; retired facets nowhere | ✓ | |
| Nothing to count shows a dash and a reason | ✓ | test_app_metrics |
| Calibration: a line per evidence type, diagonal, counts on hover, hollow small points | ✓ | Seen; lowest band is "below 0.7" (owner's decision) |
| Approved without change + changed = 100% | ✓ | |
| Empty review block before any review | ✓ | |
| Block 3 is tagging cost only | ✓ | |
| One run: single points and "Baseline run" | ✓ | |
| Filters change only their blocks; others say so | ✓ | |
| No model call, no data change | ✓ | Reads only |

## Design

| Check | | How |
| --- | --- | --- |
| All colours, sizes and spacing from tokens; no hex outside tokens.css | ✓ | grep of `web/src` |
| One primary button per page | ◐ | True on every page except Confidence, where each edited row has its own Apply (as the PRD's row flow describes) |
| Works at 1280 px with no sideways scroll | ✓ | Metrics and Confidence tables narrowed in step 5; seen |
| Loading, empty, error and saved states | ✓ | Grey placeholders on every page; "Saving…"; banners with Try again; "Photo unavailable"; "Not found in product text" |
| Whole review by keyboard with a focus ring | ◐ | Every control is a button or input with a focus ring; keyboard shortcuts skipped at the owner's request |
| No column name or status code on screen | ✓ | Facets wording fixed in step 5 |

## Checks by hand with real model calls (Build PRD 9)

| Check | | Note |
| --- | --- | --- |
| Live tag three new products from three departments, record time and cost | ◐ | Women (45 s, $0.06; and a listing item, $0.05) and Men ($0.06, owner's run); ☐ a third department |
| Approve one product and see it in the website's views | ✓ | Owner approved 104 |
| Walk all tabs at 1280 px against the Design PRD | ◐ | Done by screenshots; ☐ owner's own walk-through |
