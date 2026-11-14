# Richie's — Customer Feature Engineering PRD

Nov 14, 2026 · @Richard George · V5

## 1. Scope and ground rules

Build the customer-facing store, Richie's, with two features that share one rules engine: the occasion assistant and Complete the Look. No trained recommendation model is involved; everything is filters and weighted scores over the tags the tagger already produces. Screens and behaviour are specified in the companion design PRD, which takes precedence on anything visual.

**Ground rules**

1. **Reuse what exists.** Use the same repository, stack, database and conventions as the tagger app. Inspect the current schema before adding to it; table and column names in this PRD are suggestions and should follow the existing naming.
2. **Read only approved products.** The store reads from the table of approved, tagged products that the tagger app writes to. It never reads the raw scraped table.
3. **Do not change the tagging pipeline.** No edits to the categorising or tagging skill files, the rubrics, the golden set, or the tagger's metrics.
4. **Rules live in the database.** Occasion profiles, slot templates, colour pairings and score weights are rows, not constants in code, so they can be edited without a deploy.
5. **Deterministic.** The same persona, session state and input must return the same result. No randomness in ranking.
6. **Never show a weak match.** If nothing passes the filters, return nothing for that slot or occasion and let the page hide it.
7. **Fast.** Shortlist and look requests should return in under 300 ms on the local machine. The catalogue is a few hundred products, so scoring in memory is fine.
8. **Runs locally with one command.** Add the store to the existing setup instructions, including the sample data load.

## 2. How it fits together

One engine module does all the choosing. The two features are two entry points into it, and the pages only render what it returns.

1. **Catalogue.** The approved products table, holding tagged products from the tagger and sample products from the loader.
2. **Rules.** Small database tables: occasion profiles, outfit roles, slot templates, colour pairings and score weights.
3. **Shopper state.** The active persona, their starting history, and the events of the current session. A taste profile is computed from these on each request.
4. **Engine.** A single module with three functions: `filter(candidates, rules)`, `score(candidates, context, profile)` and `diversify(ranked)`. It returns ranked products with the reasons each one scored.
5. **Features.** `occasion_shortlist(occasion, refinements, persona, session)` and `build_look(anchor_product, occasion, persona, session)` both call the engine.
6. **Pages.** The PLP and PDP call the feature endpoints and render the results. The behind-the-scenes panel calls a state endpoint.
7. **Event log.** Every shopper action is written to an events table. It feeds the taste profile now and is the instrumentation for the success measures later.

Keep the engine free of page concerns and of persona names. It takes a profile and a context and returns ranked products, so the same code would run on real customer data.

## 3. Data model

Add seven small tables and one column. Follow the existing naming conventions; the names below are suggestions.

**Change to the approved products table**

- Add `source`, with values `tagged` (came through the tagger and was approved) and `sample` (loaded directly). Default `tagged` for existing rows.
- Confirm the table has, or add: `in_stock` (boolean, default true), `price`, `brand`, `department`, and at least one image URL.

**New tables**

| Table | One row per | Key columns |
| --- | --- | --- |
| `occasion_profiles` | Occasion value | `occasion`, `display_name`, `formality_min`, `formality_max`, `seasons`, `hero_roles` (which outfit roles can be the hero piece), `active` |
| `outfit_roles` | Category node in the spine | `category_node_id`, `role` (one\_piece, top, bottom, outerwear, shoes, bag, jewellery, accessory, or none) |
| `look_templates` | Anchor role and slot | `anchor_role`, `slot_role`, `position`, `required` |
| `colour_pairings` | Pair of colour families | `colour_a`, `colour_b`, `score` from 0 to 1 |
| `score_weights` | Weight | `feature` (shortlist or look), `signal`, `weight` |
| `personas` | Persona | `id`, `name`, `description`, `department_default`, `starting_intent` (low or high) |
| `events` | Shopper action | `id`, `persona_id`, `session_id`, `type`, `product_id`, `context` (JSON), `is_seed`, `created_at` |

**How the rule tables are filled**

- `occasion_profiles`: draft one row per value of the occasion facet from the approved occasion rubric. Richard reviews before use.
- `outfit_roles`: derive from the category spine by rule (dresses, jumpsuits, abayas and kaftans are one\_piece; and so on), then list any node that did not map for review. Beauty, fragrance, home and kids' toys are `none`.
- `look_templates`: seed with the defaults in section 6.
- `colour_pairings`: seed from the colour vocabulary in the database. Neutrals (black, white, ivory, beige, grey, navy, brown, metallics) pair with everything at 1.0; a colour with itself is 0.8; listed complementary pairs are 0.7; everything else is 0.3. Store both orderings or look up symmetrically.
- `score_weights`: seed with the defaults in sections 5 and 6.

All facet values used in these tables must exist in the facet vocabulary already stored in the database. Validate on load and fail loudly on an unknown value.

## 4. Sample data loader

Some products and all persona histories are loaded directly, bypassing the tagging flow. The loader skips the tagger but not the taxonomy: every sample product must carry valid tags from the same vocabulary.

**Sample products**

- Source file: `sample_data/products.json`, one object per product, with the same fields the approved products table holds: brand, name, price, department, category node, image URLs, description, and a value for each facet that applies to its category.
- The loader writes straight into the approved products table with `source = 'sample'`. It does not write to the raw scraped table and does not call the categorising or tagging steps.
- Confidence fields are left null. Review status is set to approved, with the approver recorded as `sample-loader`.
- Validation before insert: the category node exists in the spine; every facet is applicable to that node; every value exists in the facet vocabulary; required fields and at least one image are present. Reject the whole file with a clear message on any failure.
- The loader is idempotent. Each sample product has a stable `sample_id`; re-running updates in place and never duplicates.
- A flag removes all sample rows: `--clear`.

**Keeping sample data out of the tagger's numbers**

- Every tagger metric, the review queue and the drift monitoring must exclude `source = 'sample'`. Add the condition wherever those queries read the approved products table, and add a test that a sample product changes none of the metrics.
- The tagger app's approved products tab may show sample rows, marked "Sample", but offers no review actions on them.

**Preferred way to create sample products**

Use real products with real photographs wherever possible: take a product already in the scraped table, write its tags by hand in the JSON, and load it as a sample. Invented products with placeholder images are a last resort, for a slot that nothing real can fill.

**Persona histories**

- Source file: `sample_data/personas.json`, holding the four personas and, for each, a list of starting events: views, saves and purchases referencing product ids.
- The loader writes these to `events` with `is_seed = true`. Layla has about 15, Noor about 3, James none, Sara 3 in one category plus a size selection in the current session.
- The products referenced must exist and be approved, so run this after products are loaded. Fail with the list of missing ids if not.

**Coverage report**

Add a command that prints, for each active occasion and department, how many approved products exist per outfit role. This shows where sample products are needed before anyone writes them.

## 5. Occasion assistant

Given an occasion, return up to eight hero pieces, ranked, each with the reasons it was chosen.

**Step 1: filter.** Keep a product only if all of these hold:

- It is approved and in stock
- Its department matches the persona's department, or the answer to "Who is it for?"
- Its outfit role is one of the occasion's `hero_roles`
- Its occasion tag includes the chosen occasion
- Its formality is within the occasion's `formality_min` to `formality_max`
- Its season is compatible with the occasion's seasons; `all-season` is compatible with everything
- It passes any refinement: price within the budget band, colour in the chosen family, or for "More covered", long sleeves, a midi or longer length, and not sheer

**Step 2: score.** Each signal is scaled 0 to 1, then weighted.

| Signal | Default weight | How it is measured |
| --- | --- | --- |
| Occasion fit | 0.40 | 1.0 if the chosen occasion is the product's only or first occasion value, 0.7 otherwise |
| Taste match | 0.35 | The persona's affinity for this product (section 7) |
| Formality centre | 0.15 | 1.0 at the midpoint of the occasion's range, falling linearly to 0.5 at its edges |
| Tag confidence | 0.10 | Mean confidence of the occasion and formality tags; 1.0 for sample products |

If the persona has no profile yet, drop the taste signal and rescale the other weights to sum to 1.

**Step 3: variety.** Walk the ranked list from the top and take a product only if the shortlist so far has fewer than two items of its brand and fewer than two of its primary colour. Stop at eight.

**Step 4: reasons.** Return up to three reason codes per product, in this order: the occasion, the strongest other matching tag (formality or season), and a taste reason if the taste signal was above 0.6. The page turns codes into customer wording.

**Which occasions to offer.** An endpoint returns the active occasions that have at least four products passing step 1 for the persona's department. The page shows only those.

**Looks for you.** For a persona with a profile, pick the three occasions with the highest mean taste match, take the top hero piece from each, and build a look for it (section 6). Return the hero, its slot items and a title made from the hero's aesthetic and occasion values.

**Looks for you: eligibility and placements (added 14 Nov).** This replaces any behaviour tied to a named persona. Check how the current build decides who sees Looks for you, and change it to the rule below if it differs.

- **Eligibility.** A session is eligible when it is low intent and one of these holds: the shopper's seed and session events include at least three with a positive weight (view, save, add to bag, swap in, purchase); or the session has an occasion in play, from an `occasion_select` event or a seed entry occasion. Put the threshold of three in `score_weights` or an equivalent settings row so it can be changed.
- **Which looks.** With a taste profile, use the method above: the occasions with the highest mean taste match, top hero piece from each. With only an occasion in play, take the top hero pieces from that occasion's shortlist. Each look has a distinct hero piece, and no hero repeats across placements on one page.
- **Endpoint.** `GET /store/looks-for-you` accepts `count` (default 6) and `exclude_product_id`, and returns `eligible`, a reason code, and the looks. When not eligible it returns no looks and the reason.
- **Grid placement.** The PLP inserts one look after every eighth product, at most three per page, using looks from the endpoint in order. The top band uses the first looks returned; the grid uses the ones after them.
- **Product page placement (only if time allows).** A "More looks for you" row that calls the endpoint with `exclude_product_id` set to the product on the page.
- **Timing.** Eligibility is evaluated when a page loads, so a shopper who becomes eligible sees look cards on the next page, not the current one.
- **Events.** Add `look_card_view` and `look_card_tap`, each with the placement in the context: `top_band`, `grid` or `product_page`. Include them in the funnel script so placements can be compared.
- **State endpoint.** `GET /store/state` also returns Looks for you eligibility, the reason, and the count of positive events against the threshold.
- **Tests.** A session with no events is not eligible; the same session becomes eligible after three positive events; a high-intent session is never eligible; a session with one seed purchase and an occasion in play is eligible and gets occasion-led looks; no hero piece appears twice on one page.

**PLP lead module (added 14 Nov).** The top of the PLP renders exactly one lead module, decided on the server when the page loads. The current build shows Looks for you and the occasion picker together for Layla; change it to the rule below.

- **Decision, in this order.** First match wins:
  1. Session is high intent → `none`
  2. An occasion is in play, from an `occasion_select` event in the session or a seed entry occasion → `occasion_picker`, with that occasion returned as `suggested_occasion`
  3. The shopper has a taste profile, meaning at least three positive events, the same threshold as Looks for you eligibility → `looks`
  4. Otherwise → `occasion_picker`, with `show_opener` true when no department is known
- **Where it is returned.** Add `lead_module`, `suggested_occasion`, `show_opener` and a reason code to the PLP response or to `GET /store/state`, whichever fits the existing structure. The page must not decide this itself.
- **When the lead is `looks`.** The page shows the Looks for you row and a collapsed one-line occasion entry beneath it. Opening that line loads the occasion chips in place from `GET /store/occasions`; choosing one posts to the shortlist as usual and records `occasion_select`.
- **When the lead is `occasion_picker`.** The top Looks for you row is not rendered. Grid look cards still follow the Looks for you eligibility rule above.
- **Changes over a session.** Because the decision is made at page load, James moves from `occasion_picker` to `looks` on the first PLP load after his third positive event. A shopper who picks an occasion moves to `occasion_picker` with the shortlist shown.
- **Behind-the-scenes panel.** Show the current lead module and the reason code.
- **Tests.** Layla's seed state returns `looks`; Noor's returns `occasion_picker` with a suggested occasion; James's returns `occasion_picker` with the opener, then `looks` after three positive events; Sara's returns `none`; no response ever carries both a top looks row and a full picker.

**Own words (optional, build last).** One language-model call turns free text into an occasion value and refinements from the closed vocabulary, returned as structured output. If the model returns nothing valid, show the chips. The result goes through the same steps as a chip tap.

## 6. Complete the Look

Given any approved product, return a look: one chosen item per slot, plus up to four alternates per slot for swap.

**Step 1: role and template.** Read the anchor's outfit role from `outfit_roles`. If the role is `none`, return no look. Otherwise read its slots from `look_templates`. Default templates:

| Anchor role | Slots, in order |
| --- | --- |
| one\_piece | shoes, bag, jewellery |
| top | bottom, shoes, bag |
| bottom | top, shoes, bag |
| outerwear | one\_piece, shoes, bag |
| shoes | one\_piece, bag, jewellery |
| bag | one\_piece, shoes, jewellery |
| jewellery | one\_piece, shoes, bag |
| accessory | one\_piece, shoes, bag |

**Step 2: filter per slot.** Keep a candidate only if:

- It is approved, in stock, has the slot's role, and is not the anchor
- Its department matches the anchor's
- Its formality is within one step of the anchor's
- Its season is compatible with the anchor's; `all-season` is compatible with everything
- If an occasion was carried from the shortlist, its occasion tag includes that occasion; otherwise it shares at least one occasion value with the anchor

Where a facet does not apply to a candidate's category, skip that check and that score signal, and rescale the remaining weights.

**Step 3: score per slot.**

| Signal | Default weight | How it is measured |
| --- | --- | --- |
| Colour harmony | 0.30 | `colour_pairings` score between the anchor's and the candidate's primary colour |
| Formality closeness | 0.20 | 1.0 for the same level, 0.6 for one step away |
| Occasion overlap | 0.15 | Shared occasion values divided by the anchor's occasion values |
| Aesthetic match | 0.15 | 1.0 if they share an aesthetic value, 0.4 otherwise |
| Taste match | 0.15 | The persona's affinity for the candidate (section 7); dropped and rescaled if there is no profile |
| Price fit | 0.05 | 1.0 if the candidate costs no more than the anchor, falling to 0 at three times the anchor's price |

**Step 4: assemble.** Fill slots in template order, taking the top candidate for each. Apply one look-level rule: across the anchor and chosen items, allow at most two non-neutral colour families. If the top candidate breaks the rule, take the next one that does not.

**Step 5: alternates and swap.** Keep the next four candidates per slot, in score order, as that slot's alternates. The response carries all of them, so swap is instant in the browser and needs no new request. Each swap sends two events: `swap_out` for the item replaced and `swap_in` for the item now shown. Stepping back to an earlier option sends the same pair in reverse.

**Step 6: reasons.** Return one reason code per chosen item and alternate, taken from its strongest signal: colour, formality, occasion, aesthetic or taste.

**Thin results.** Return only the slots that have at least one candidate. Include a count of filled slots so the page can choose between "Complete the look", "Pairs well with" and showing nothing.

**High intent.** For a high-intent session, return the top item per slot only, with no alternates and no reasons.

## 7. Personas, taste profile and intent

A persona is a starting history plus whatever happens in the session. The taste profile and the intent state are both computed from events; nothing about a persona's taste is typed in by hand.

**Session**

- A session belongs to one persona and has an id held in a cookie. Switching persona or pressing "Reset session" starts a new session id.
- The profile for a request is computed from that persona's seed events plus the current session's events.

**Taste profile**

The profile is a set of weights per facet value, for these facets: aesthetic, primary colour, formality, sleeve length, length, sheerness and brand.

1. For each event, take the product's value for each facet and add the event's weight to that value's tally.
2. Clamp each tally at zero, so a value can be neutral but not negative.
3. Within each facet, divide by the facet's total so the values sum to 1.

| Event | Weight |
| --- | --- |
| purchase (seed only) | +3 |
| add\_to\_bag | +3 |
| save | +2 |
| swap\_in | +1.5 |
| view (opened a product page, or tapped a shortlist card) | +1 |
| swap\_out | -1.5 |

Session events count double, so that a shopper with no history sees results move after three or four actions.

**Taste match for a product.** For each profile facet that applies to the product, look up the profile weight of the product's value, and divide it by the largest weight in that facet so the best-liked value scores 1. The taste match is the mean across facets. A persona with no events has no profile, and the engine drops the taste signal.

**Intent rule**

A session is high intent if any of these has happened in it; otherwise it is low intent:

- It started on a product page from a search referral (set on Sara's seed session)
- A size was selected
- A PLP filter was applied (refinement chips in the occasion shortlist do not count)

The state is evaluated when a page loads, so a change takes effect on the next page, not the current one. Return the state and the rule that set it, for the behind-the-scenes panel.

**What the full version would be.** The 0 to 100 intent score with a middle state is not built. Keep the rule in one function with a clear interface (`intent(session_events) -> state, reason`) so a scored version could replace it.

**Persona signals in the switcher (added 14 Nov).** The persona dropdown shows the signals behind each persona's intent. The wording and layout are in section 4 of the design PRD.

- **Remove age.** Delete any age field from the `personas` table, from `sample_data/personas.json`, and from every place a persona is displayed.
- **New persona fields.** Add `archetype` (for example "the Wanderer"), `entry_source` (direct, notification, web\_search or product\_search), `entry_occasion` (optional; set for Noor), and `starting_signals`, an ordered list of three or four short display strings. Fill them from the table in the design PRD.
- **Entry as seed events.** Record each persona's entry as a seed event in their starting session, so the intent rule and the lead-module rule read it from events like everything else. Sara's starting session also carries her `size_select` event.
- **Computed, not typed.** The intent badge and the "what the store does" line are computed by running the intent rule and the lead-module rule over the persona's seed events. Only the signal strings are stored text.
- **Endpoint.** Add `GET /store/personas`, returning for each persona: name, archetype, starting signals, computed starting intent, and computed starting lead module.
- **Consistency test.** For each persona, the computed starting intent must match the one in the design PRD table: Layla, Noor and James low, Sara high. A persona whose stored signals describe a behaviour that has no matching seed event fails the test, so the displayed signals can never drift from the data.

## 8. Endpoints and event log

Paths are suggestions; follow the existing API style. Every endpoint reads the persona and session from the cookie.

| Endpoint | Purpose | Returns |
| --- | --- | --- |
| `GET /store/products` | PLP grid, with optional facet filters and department | Products, ordered by taste match for a low-intent persona with a profile, otherwise newest first |
| `GET /store/products/{id}` | Product page | Product fields and its tags |
| `GET /store/facets` | Filter drawer | Each filterable facet with the values that have products |
| `GET /store/occasions` | Occasion chips | Active occasions with at least four eligible products |
| `POST /store/shortlist` | Occasion assistant | Up to eight hero pieces with reason codes; accepts occasion and refinements |
| `GET /store/looks-for-you` | Layla's row | Up to three looks with titles |
| `GET /store/products/{id}/look` | Complete the Look | Slots, each with a chosen item, alternates and reason codes; accepts an optional occasion |
| `POST /store/events` | Record a shopper action | Nothing |
| `GET /store/state` | Behind-the-scenes panel | Intent state and reason, taste profile, last five events with their effect |
| `POST /store/session` | Switch persona or reset | New session id |
| `POST /store/parse-occasion` | Own words (optional) | An occasion and refinements, or nothing |

**Event types**

`view`, `save`, `add_to_bag`, `add_look_to_bag`, `size_select`, `filter_apply`, `occasion_select`, `refine_apply`, `shortlist_view`, `look_view`, `swap_out`, `swap_in`.

Each event stores the product where there is one, and a JSON context: the occasion in play, the slot for look events, the position of the item in its list, and whether the surface was the shortlist, the look or the grid.

**Why log all of these.** The profile uses only six of them. The rest make the success measures computable from the prototype: how many sessions see a shortlist, tap a hero piece, view a look, swap, and add a look to bag. Add a small query or script that prints that funnel per persona from the events table.

## 9. Build order, tests and open items

**Build order**

Complete the Look comes before the occasion assistant, because the product page is where a product the review panel picks will land.

1. Schema changes, rule tables with seeded defaults, and the `source` column with the tagger metric exclusions
2. Sample data loader and the coverage report
3. Engine module, with unit tests
4. Store shell: header, PLP grid, product page, persona switcher, sessions and event logging
5. Complete the Look with swap
6. Taste profile, session learning and the behind-the-scenes panel
7. Occasion assistant: occasions endpoint, shortlist, refinements
8. Intent rule and the high-intent layouts
9. Looks for you
10. Own words input, only if everything above is done

**Tests**

- Engine: each filter rule excludes what it should; scores are deterministic; weights rescale correctly when a signal is dropped
- Look: every anchor role returns the right slots; the anchor never appears in its own look; the colour rule holds; a product with role `none` returns no look
- Profile: a persona with no events has no profile; a `swap_out` lowers a value and never takes it below zero; Layla and James get different shortlists for the same occasion
- Intent: a size selection or a PLP filter makes the next request high intent; a refinement chip does not
- Sample data: an invalid facet value is rejected; reloading does not duplicate; tagger metrics are unchanged by sample rows
- End to end: a product approved in the tagger app appears on the PLP and returns a look, with no restart

**Open items for Richard**

- [ ] Review the drafted `occasion_profiles` rows
- [ ] Review the `outfit_roles` mapping and any category nodes that did not map
- [ ] Review the complementary pairs in `colour_pairings`
- [ ] Run the coverage report after the catalogue is tagged, then decide which sample products are needed
- [ ] Choose the starting history for Layla, Noor and Sara from approved products
- [ ] Confirm the weights in sections 5 to 7 after trying the four personas; they are starting assumptions

## 10. Where to build and how to start

The store is built in the same repository as the tagger app, in its own new folder, on its own branch. Read this section before anything else.

**Where the code goes**

- Work from the repository's top-level folder, so the tagger's code, database schema, facet vocabulary and spine are all visible and reusable.
- Create one new folder for the store and put all store code in it: the pages, the rules engine, the sample data loader and the `sample_data` files. Propose the folder name and structure to match the rest of the project.
- Create a new git branch for this work before changing any file.
- The store and the tagger app share one database. Do not create a second database or copy tables.

**What may and may not be edited outside the new folder**

- Do not change the tagging pipeline, the categorising and tagging skill files, the rubrics, or the golden set.
- The only tagger files that may be edited are the metric, review-queue and drift-monitoring queries, and only to exclude `source = 'sample'` rows.
- Schema changes are limited to those in section 3: the new tables and the `source` column on the approved products table.
- The existing setup instructions are extended to cover the store and the sample data load, so the whole project still runs with one command.

**Before writing any code**

1. Read this PRD and the design PRD in full.
2. Inspect the existing codebase and database schema. Report how the tagger app is structured, which stack it uses, and which tables the store will read.
3. Propose the store's folder name and structure, and a build plan that follows the build order in section 9.
4. List anything in either PRD that conflicts with what already exists, such as table names or fields that are missing.
5. Wait for Richard's go-ahead on the plan before building.

**Why one repository.** A product approved in the tagger app has to appear in the store straight away during the live review, the sample marker has to be respected by the tagger's metrics, and the case asks for a single folder or repository with setup instructions.
