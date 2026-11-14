# Ounass Tagger: Design PRD

Nov 13, 2026 · @Richard George

## 1. What this covers

This PRD sets how the tagger's screens look and behave, and specifies seven tabs: **Products** (every tagged product, each marked Approve or Review), **Approved** (what is live on the website), **Live tag** (paste an Ounass URL and watch it get categorised and tagged), **Metrics** (how well the tagger is doing, in five questions), **Confidence** (how confident the tagger is on each facet, and the level each must reach), **Facets** (each facet's rules and values, editable by a merchandiser), and **Models** (which AI model does the tagging). Nothing reaches the website until a person approves it.

| Document | Decides |
| --- | --- |
| Review Screen PRD | What data each screen reads and writes, keyboard shortcuts, saving rules for the golden set. |
| This PRD | Visual style, navigation, the three tabs, the detail page, and the approve-and-publish rules. |

This is the PRD to build from for the tagger's screens. Where it differs from the Review Screen PRD or the pipeline PRD, this one wins. Two things change in those documents: the Review Screen PRD's "queue mode" is replaced by the Products tab, and the pipeline's bulk `tagger publish` no longer puts products on the website (section 6). The golden set is already saved, so golden mode in the Review Screen PRD is not part of this build.

## 2. Design principles

1. **Evidence beside the answer.** A reviewer never leaves the page, or scrolls away from a facet, to see the photo or sentence behind it.
2. **Doubt is the loudest thing on the page.** Colour is reserved for what needs attention. Anything the AI was sure of is plain black on white.
3. **Confidence is always a number.** Every colour signal is paired with the figure and a word, so the screen reads correctly on a projector and for colour-blind viewers.
4. **One primary action per page.** On the detail page it is "Approve and publish". Everything else is visually quieter.
5. **Quiet, editorial look.** White space, black type and product photography carry the page, in keeping with a luxury retailer's back office. No gradients, no drop shadows on cards, no decorative icons.
6. **Readable from across a room.** The panel will watch this on a shared screen, so key values are at least 14 pixels and the layout holds at 1280 pixels wide.

## 3. Visual style

Define every value below once, as named CSS variables in a single file, and use only those names in components. No colour, size or spacing is written directly into a component.

**Colour**

| Token | Value | Used for |
| --- | --- | --- |
| `bg-page` | `#FAFAF8` | Page background. |
| `bg-surface` | `#FFFFFF` | Cards, tables, panels. |
| `border` | `#E6E4DF` | Card and row borders, dividers. |
| `text` | `#1A1A1A` | Body text and values. |
| `text-muted` | `#6B6B6B` | Labels, helper text, reasons. |
| `action` | `#111111` | Primary button fill, active nav item, links on hover. |
| `flag` / `flag-bg` | `#9A6700` / `#FFF4D6` | Below the confidence threshold; needs a decision. |
| `ok` / `ok-bg` | `#1F7A4D` / `#E8F5EE` | Approved, saved, published. |
| `error` / `error-bg` | `#B42318` / `#FDECEA` | Failed validation, missing required value, failed save. |
| `highlight` | `#FFE98A` | Highlighted evidence quote in product text. |
| `focus` | `#2563EB` | Keyboard focus ring, 2 pixels, on every interactive element. |

**Type**

| Token | Value | Used for |
| --- | --- | --- |
| Font | Inter, then the system sans-serif | Everything. Load weights 400, 500 and 600 only. |
| `text-xs` | 12px / 16px line height | Labels and table headers, in uppercase with 0.04em letter spacing. |
| `text-sm` | 13px / 20px | Reasons, helper text. |
| `text-base` | 14px / 22px | Body, facet values, table cells. |
| `text-lg` | 18px / 26px, weight 600 | Product title, page headings. |
| Numbers | Tabular figures | Confidence, counts and prices, so columns line up. |

**Spacing and shape**

| Token | Value |
| --- | --- |
| Spacing scale | 4, 8, 12, 16, 24, 32, 48 pixels. Nothing in between. |
| Corner radius | 6px on cards, inputs and buttons; 999px on badges and chips. |
| Card | White surface, 1px `border`, 16px padding, no shadow. |
| Shadow | Only on popovers and the enlarged photo. |
| Content width | Fluid, with 24px page margins. Minimum supported width 1280px. |
| Photos | 3:4 portrait, cropped to fill, on a `#F3F2EE` placeholder while loading. |

Light theme only.

## 4. App shell and navigation

Every page sits in one shell: a narrow left rail for moving between sections and a top bar that names the page.

| Area | Design |
| --- | --- |
| Left rail | 200px wide, white, full height. The word "Tagger" at the top in `text-lg`. Below it the seven tabs, each a 36px row. The active tab has a 2px `action` bar on its left edge and weight 600. |
| Tabs | **Products**, with a count badge of products waiting for approval. **Approved**, with its count. **Live tag**. **Metrics**. Then, under a thin divider and a "Settings" label in `text-xs`: **Confidence**, **Facets** and **Models**. |
| Top bar | 56px high. Page title on the left; page-level controls on the right (search and filters on the lists; previous and next on the detail page). |
| Rail footer | The current run ID, the default confidence level, and the two models in use, in `text-xs`, so the panel can always see which settings produced the screen. |

Both count badges update as soon as a product is approved, without a page reload.

## 5. Products and Approved tabs

The Products tab lists every tagged product that has not been approved yet, and tells the reviewer at a glance whether each can be approved as it stands or needs a look first. It is the tagger's home page.

**Approve or Review.** Each product gets one of two states, worked out from its current run:

| State | Rule | Button on the row |
| --- | --- | --- |
| Ready | The category and every facet are at or above the confidence threshold, and no required facet is missing. | **Approve** (primary). Approves and publishes in one click, from the list. |
| Needs review | Any facet is below the threshold or flagged for review, a required facet has no value, or the category is in review. | **Review** (secondary, with a `flag` outline). Opens the detail page. |

- Clicking anywhere else on a row opens the detail page, for both states.
- "The threshold" for a facet means its confidence level as set on the Confidence tab (section 8): the facet's own level if it has one, otherwise the default.
- A product the tagger has not processed yet is not listed. The summary line shows how many are tagged.

**Layout.** One table, one row per product, 72px rows.

| Column | Shows |
| --- | --- |
| Photo | 48 by 64px thumbnail of the primary image. |
| Product | Title in `text-base`, weight 500; brand beneath in `text-muted`. |
| Category | The last two levels of the category path. A `flag` badge "Category in review" when that applies. |
| Facets | For a Ready product: "14 of 14 confident" in `text-muted`. For Needs review: "3 of 14 need review", then the flagged facet names as chips in `flag-bg`, up to three and then "+2". |
| Lowest confidence | The lowest facet confidence on the product, as a confidence badge (section 12). |
| Action | The Approve or Review button, right-aligned. |

**Controls in the top bar**

- Summary line: "120 ready · 42 need review · 162 of 241 tagged". Use the real figures, for the current filters.
- State filter as three pills: All, Ready, Needs review.
- **Category filter.** A drop-down showing the category tree with search. Picking any level shows products in that category and everything beneath it.
- **Facet filter.** Pick a facet, then either one of its values ("occasion is wedding guest") or "needs review" ("occasion needs review"). Number and text facets offer "has a value", "unknown" and "needs review".
- Active filters appear as removable chips under the top bar, with "Clear all". Category and facet filters combine with each other, the state pills and search.
- Search by title, brand or product ID.
- Sort: Needs review first, lowest confidence at the top, by default. Also brand A to Z.
- "Approve all ready (120)": a secondary button that approves every Ready product in the current filter, after a confirmation that states the number.
- The same category and facet filters are on the Approved tab. Filters are kept in the page address, so a filtered view survives a reload and the back button.

**After approving from the list.** The row fades out, a confirmation appears at the bottom of the screen with "Undo" for eight seconds, and both count badges update.

**Approved tab.** The same table for approved products. The Facets column is replaced by the approval date, and the Action column by a "View on website" link. Rows open the detail page, so an approved product can still be corrected.

**Empty states.** Products tab with nothing waiting: "Everything tagged so far is approved", with a link to the Approved tab. Approved tab with nothing in it: "Nothing approved yet".

## 6. Product detail page

The detail page shows every facet for one product with its confidence. The reviewer approves or changes each facet that needs review, then approves the product, which publishes it to the website.

**Layout**

| Area | Design |
| --- | --- |
| Top bar | "← Products" back link, the product title, a status badge ("Ready", "Needs review" in `flag`, or "Approved · live" in `ok`), and previous and next buttons that follow the list's current filter and sort. |
| Left column, 42% | The evidence panel: photo gallery on top, labelled product text beneath (section 12). It scrolls on its own. |
| Right column, 58% | The category card, then two groups of facet rows: **Needs review** first, then **Confident**. Each group has a heading with its count. |
| Footer bar | Fixed to the bottom of the right column. Left: "3 facets still need a decision". Right: a secondary "Save" button and the primary "Approve and publish" button. |

**Facet rows**

- **Needs review** rows have a 3px `flag` bar on the left edge and are open by default, showing value, confidence, evidence and reason. Each has two actions: **Approve** (keep the value as shown) and **Change** (open the value editor).
- Once a flagged row is approved or changed, its bar turns `ok` and it shows "Approved" or "Changed". It stays in place; rows do not jump between groups while the reviewer works.
- **Confident** rows are compact, one line each: facet name, value, confidence. Clicking one expands it, and it can be changed in the same way.
- Facets written by rule show "Rule" in place of a confidence number and are read-only.
- If the category is in review, the category card is the first thing to decide. The facet rows are locked until it is approved or changed.

**Save and approve**

| Action | What it does |
| --- | --- |
| Approve or change a row | Held on the page. The footer shows "Unsaved changes". |
| Save | Stores the decisions made so far. The product stays in the Products tab and off the website. Use it to stop part-way. |
| Approve and publish | Stores everything and publishes the product. It then moves from the Products tab to the Approved tab. |

**Rules for approval**

- "Approve and publish" is enabled only when every flagged facet has been approved or changed, and no required facet is without a value. Until then the button is disabled and the footer says what is left; clicking the message scrolls to the first open row.
- A Ready product has nothing flagged, so the button is enabled as soon as the page opens.
- A facet with no value that is not required can be approved as "Unknown".
- Every changed value must be an allowed value for that facet and within its limits. The database rules in the tables PRD still apply.

**What approval writes.** Approval is the only way a product reaches the website.

- The reviewer's final answers are stored, the product is marked approved, and it is published in the catalog tables that power the sample website.
- The tagger's own answers are kept unchanged alongside, so scoring against the golden set is not affected by corrections.
- The website's views already show only published products, so nothing else is needed for it to appear.
- `tagger publish` no longer publishes.
- The exact tables, columns and transactions are in the [Build PRD](https://claude.ai/artifact/7C6b7hT3F8Zar1Twprh9Ac), sections 3 and 5.

**After approval**

1. A confirmation appears at the bottom of the screen: "Approved and published", with "View on website" and "Undo" links, for eight seconds.
2. The page moves to the next product in the list.
3. Undo unpublishes the product and returns it to the Products tab.

**Opening an approved product.** The page looks the same, with the "Approved · live" badge and one primary button, "Save changes". Saving updates the website straight away. A secondary "Send back to review" action unpublishes it.

## 7. Live tag tab

The Live tag tab takes a product URL from the Ounass website, runs the whole pipeline on it, and shows the result on the same page with the same Approve or Review decision. It is the screen for products the panel picks.

**Input.** A single wide URL box with a primary button, centred at the top of the page. Above the box, a two-option toggle: "Single product" and "Listing page". "Single product" is selected when the page opens.

- **The toggle sets itself.** When a link is pasted or typed, the app works out whether it is a product page or a listing page and selects the matching option. A short note beneath confirms it: "Detected: product page" or "Detected: listing page".
- **The reviewer can override it** by clicking the other option. The app then treats the link as that type and does not switch back unless the link changes.
- **Single product:** no number field anywhere on the page. The button reads "Tag product". Helper text: "Paste a product link from the Ounass website".
- **Listing page:** the number field appears beside the box, and the button reads "Tag 5 products", following the number. Helper text: "Paste a category, brand or search page link".
- If the link's type and the selected option disagree after an override (a product link with "Listing page" selected, say), starting the run shows a message under the box that names the mismatch and offers to switch. It does not run.
- Pressing Enter starts the run.

**Progress.** While it runs, a four-step tracker replaces the helper text. Each step shows a tick and its time when done.

1. Fetching product: the existing scraper reads the page and saves the raw row and photos.
2. Categorising.
3. Tagging.
4. Done: total time and cost for this product, in `text-muted`.

**Result, on the same page.** Under the tracker, the page shows the same two columns as the detail page (section 6): evidence on the left, category card and facet rows on the right, with the same footer bar.

- The photos and text appear as soon as step 1 finishes, the category card after step 2, and the facet rows after step 3, so the page fills in as the tagger works.
- A status badge states the outcome using the rules in section 5: "Ready" or "Needs review".
- Ready: "Approve and publish" is enabled straight away.
- Needs review: the flagged rows are open for the reviewer to approve or change on this page, with no navigation.
- Once tagged, the product also appears in the Products tab, or the Approved tab after approval.

**Special cases**

| Case | Behaviour |
| --- | --- |
| Category below the threshold | The run pauses after step 2 with the category card flagged. The reviewer approves or changes the category, then tagging continues. |
| URL is not an Ounass product or listing page | Message under the box: "This is not an Ounass product or listing link". Nothing runs. |
| Product already in the database | The page shows the existing result at once, with a "Tag again" secondary button that starts a fresh run. |
| Page cannot be fetched | The tracker stops at step 1 in `error` with the reason and a "Try again" button. |
| A model call fails | The tracker stops at that step with the reason and "Try again", which resumes from that step. |

**Tagging several products from a listing page.** When the pasted URL is an Ounass listing page (a category, brand, search or sale page), the box changes to listing mode.

1. A number field appears beside the box: "First \[ 5 \] products", from 1 to 25, default 5. The button reads "Tag 5 products".
2. Before starting, a line beneath shows an estimate from past runs: "About 6 minutes and $0.40".
3. On start, the app reads the listing page, takes the first X products in the order the page shows them, then opens each product's own page and scrapes it in full, exactly as for a single product.
4. Each product is then categorised and tagged.

**Progress in listing mode.** In place of the single-product result, the page shows a table with one row per product, filled in as each finishes.

| Column | Shows |
| --- | --- |
| # | Its position on the listing page. |
| Product | Thumbnail, title and brand, as soon as its page has been scraped. Before that, a placeholder row. |
| Step | Waiting, Fetching, Categorising, Tagging, Done, or Failed with the reason. |
| Result | "Ready" or "Needs review" badge, with the count of flagged facets. "Already tagged" for a product that was in the database before. |
| Action | "Approve" for a Ready product, "Review" otherwise. Both behave as on the Products tab; Review opens the detail page. |

- Above the table: a progress line ("3 of 5 done · 2 min 10 s · $0.21") and a "Stop" button, which lets the product in progress finish and cancels the rest.
- When all are done, the line becomes a summary: "5 tagged: 3 ready, 2 need review", with "Approve all ready" and "Open in Products" (the Products tab filtered to these products).

**Rules in listing mode**

- A product already in the database is not scraped or tagged again. It is listed as "Already tagged" with its current state, and still counts towards X.
- A product whose category is below the confidence level does not pause the batch. It is listed as "Needs review" and the run moves on.
- One product failing does not stop the others. Its row shows "Failed" with the reason and a "Try again" link.
- Only the listing page as first loaded is read. If it shows fewer than X products, the app tags what is there and says so: "This page showed 18 products".
- Sponsored tiles, banners and anything that is not a product link are skipped.

**Build notes**

- Reuse the scraper and the pipeline's single-product path (`tagger run --product ID`); do not write a second tagging path.
- The run happens on the server and reports progress to the page as each step finishes.
- Target: under two minutes from paste to result.
- A "Tag another" link clears the page for the next URL. The last five URLs tagged in this session are listed beneath the box as links.

## 8. Confidence tab

The Confidence tab shows, for every facet, the confidence level a tag must reach to be trusted and how much of the catalog reaches it, and lets the reviewer change that level. Changing a level moves products between Ready and Needs review straight away.

**Layout.** One table, one row per facet, with a first row for Category.

| Column | Shows |
| --- | --- |
| Facet | The facet's display name; its definition on hover. A "Required" marker where it applies. |
| Confidence level | The level in force for this facet, as an editable number from 0.00 to 1.00 in steps of 0.05. A small "default" label when the facet uses the default level. |
| Confident | The percentage of products whose answer for this facet is at or above the level, as a number and a 120px bar. Beneath it in `text-muted`: "182 of 214 products". |
| Average confidence | The mean confidence of the tagger's answers for this facet, to two decimals. |
| Unknown | The percentage of products where the tagger gave no answer for this facet. |
| Needs review | The number of products currently flagged because of this facet. Clicking it opens the Products tab filtered to them. |
| Reviewed so far | For approved products: how many answers for this facet a reviewer had to decide, split as "18 approved as shown · 6 changed", with the change rate ("25% changed") beneath. A dash until something has been reviewed. |

- Rows are sorted with the lowest "Confident" percentage first, so the weakest facets are at the top.
- Facets written by rule have no confidence and are listed at the bottom, greyed, with "Rule" in the level column.
- The figures cover every tagged product, using the tagger's own answers, whether or not the product has been approved.

**Review outcomes.** Above the table, a summary line gives the overall figures for approved products: "96 answers reviewed · 71 approved as shown · 25 changed (26%) · 4 changed though confident".

- "Changed" includes answers the reviewer set to unknown.
- "Changed though confident" counts answers that were above the level, so not flagged, but that the reviewer changed anyway.
- The change rate is the guide for setting a level. A facet where almost everything is approved as shown is being reviewed more than it needs, so its level can come down. A facet with a high change rate, or any "changed though confident", needs its level raised.

**Top bar controls**

- Default level: one editable number that applies to every facet without its own level.
- Category filter: pick any level of the category tree to see the figures for that part of the catalog only. Levels are still set per facet, not per category.
- Search by facet name.
- "Reset all to default", as a quiet button with a confirmation.

**Changing a level**

1. The reviewer edits the number on a row. Nothing changes yet; the row shows a preview beneath it: "0.80 → 0.70: Confident rises from 74% to 85%. 12 products move from Needs review to Ready."
2. Two buttons appear on the row: "Apply" (primary) and "Cancel".
3. On Apply, the level is saved, the row's figures and the nav badges update, and a confirmation appears with "Undo" for eight seconds.

**Rules**

- A new level applies to products waiting for approval and to everything tagged from then on, including Live tag.
- Approved products stay approved and live, whatever the level becomes.
- Lowering a level never clears a flag raised for another reason, such as a missing required value or a quote that is not in the product text.
- Several rows can be edited before applying. A bar at the bottom then shows the combined effect with "Apply all" and "Discard".

**Empty state.** Before any product is tagged: "No tagged products yet", with the levels still editable.

## 9. Facets tab

The Facets tab shows every facet with its rules, rubric and allowed values, and lets a merchandiser edit and save the parts that live in the database: the rules and the values. The tagger reads those from the database, so a saved change shapes the very next product tagged. The rubric lives in the tagging skill file; it is shown here for reference and is not edited in the app.

**Layout.** Two panes.

| Area | Design |
| --- | --- |
| Left pane, 320px | The list of facets. Search box on top, then a category filter ("facets used in Dresses"). Facets are grouped under three headings: Tagged by AI, Set by rule, Retired. Each row shows the facet name, its number of values, and an "Edited" marker if it has been changed in the app. |
| Right pane | The selected facet, as the cards below, in this order. A fixed footer bar holds "Discard" and the primary "Save changes" button, both disabled until something is edited. |

**Cards in the right pane**

| Card | Shows | Editable |
| --- | --- | --- |
| Basics | Display name and one-line definition. The internal ID beneath in `text-muted`. | Name and definition. |
| How it is judged | Evidence source, as three options: "Product text only", "Product text first, then photos", "Judge from text and photos". Whether the facet is required. What happens when no answer is found: "Leave unknown" or "Send to review". The maximum number of values. | All four. |
| Values | A table: value label, definition where the database holds one, the number of products using it, and an Active switch. "Add value" at the bottom. | Label, definition, Active, and new values. |
| Rubric | The facet's guidance from the tagging skill file, as plain read-only text on a `bg-page` background, with the note "Managed in the skill file". Collapsed to six lines with "Show all". | No. |
| Applies to | The categories this facet is used in, as chips. | No. |
| Confidence level | The facet's current level, with a link to the Confidence tab. | On the Confidence tab only. |
| History | The last five changes: date, what changed, with "View" to see the earlier version and "Restore" to bring it back. | Restore only. |

**Rules**

- A value that products use cannot be deleted. Switching it to inactive stops the AI choosing it from then on and keeps existing tags as they are.
- A value's label can be changed; products already tagged with it show the new label.
- The facet's ID, its type (single value, multi value, number, text) and the categories it applies to are read-only.
- Facets set by rule show their rule in plain words and are read-only.
- Leaving the page or switching facet with unsaved edits asks for confirmation.
- Validation on save: a name and definition are present, the maximum number of values is at least 1, no two active values share a label, and at least one value stays active. Problems appear on the field concerned.

**After saving**

1. A confirmation: "Saved. This applies to products tagged from now on."
2. A banner on the facet: "214 products were tagged with the earlier version", with a "Test on a product" button.
3. The change is added to History and the facet gets the "Edited" marker.

Saving never re-tags products by itself and never changes approved products.

**Test on a product.** This shows the effect of an edit in under a minute, and is the demo moment for this tab.

1. The reviewer picks a product from a search box, or pastes an Ounass URL.
2. The app re-tags that one product with the saved rules.
3. A panel shows this facet's answer before and after, side by side: value, confidence, evidence and reason, with the product's photo beside them.
4. The re-tagged product appears in the Products tab as Ready or Needs review, like any other.

**Empty and loading states.** With no facet selected: "Select a facet to see its rules". The left list shows placeholder rows while loading.

## 10. Models tab

The Models tab is where the reviewer chooses which AI model the tagger uses. There are two choices, because the tagger works in two steps: one model to pick the category, and one to fill in the facets.

**Layout.** Two sections, one above the other: "Categorising model" and "Tagging model". Each shows the available models as a row of cards, with one selected.

| On each model card | Detail |
| --- | --- |
| Name | The model's name in `text-lg`, with a "Current" badge in `ok` on the selected one. |
| Price | Cost per million tokens in and out, in `text-muted`. |
| Reads photos | "Reads photos" or "Text only". A text-only model carries a `flag` note: "Facets judged from photos will go to review". |
| Results so far | From past runs that used this model for this step, where there are any: accuracy against the golden set, average cost per product, and average time per product. Otherwise "Not run yet". |

- The selected card has a 2px `action` border. Clicking another card selects it but does not save.
- The models offered are the ones already set up in the tagger's model list. The app does not add or remove models.

**Saving.** A footer bar shows "Discard" and the primary "Save changes" button once a different model is picked. On save, a confirmation: "Saved. Products tagged from now on use this model."

**Rules**

- The choice applies to everything tagged from then on: Live tag, "Test on a product", a category change that re-tags, and command-line runs that do not name a model.
- Saving never re-tags existing products and never changes approved products.
- The rail footer shows the two models in use, beside the run ID and default confidence level.
- On the Live tag tab, the "Done" step states which two models produced the result.

## 11. Metrics tab

The Metrics tab answers five questions about the tagger, in a fixed order, on one scrolling page. It only reads existing data: it calls no model and changes no tag, confidence level or rule. Its content comes from the "Tagger app: Metrics tab requirements" document; how each number is calculated is in the Build PRD, section 11.

**Page structure, top to bottom**

| Order | Block | Heading shown on the page |
| --- | --- | --- |
| 0 | Top strip | No heading: five tiles. |
|  | Run line | One line of `text-xs` small print. |
|  | Filter bar | Run, Category, Facet. |
| 1 | Quality | "Is it right?" |
| 2 | Review | "How much human work does it need?" |
| 3 | Cost | "What does tagging cost?" |
| 4 | Health | "Is it still healthy?" |
| 5 | Business | "What does the business get?" Not yet specified; see the note at the end of this section. |

- Each block is one white card with its question as the heading in `text-lg`, 32px apart.
- A small set of in-page links under the filter bar jumps to each block.

**Top strip.** Five tiles in one row, equal width. Each tile: a label in `text-xs` uppercase, one number at 32px weight 600 in tabular figures, and one supporting line in `text-sm` `text-muted`.

| Tile | Large number | Supporting line |
| --- | --- | --- |
| Tag precision | Precision on the holdout products | Precision on the tuned set, and the number of answers scored |
| Auto-accepted | Share of tags accepted without a person | Number of tags accepted out of the total |
| Review rate | Share of tags waiting for a person | Number of products with at least one tag in review |
| Category accuracy | Share of accepted categories that are correct | Number of categories flagged and held |
| Tagging cost per product | Cost of the tag step divided by products tagged | Projection for 3 million products at standard pricing |

- Until the holdout products are labelled, the precision tile shows the tuned-set number as the large number, with a "Tuned set" badge beside the label.
- Tiles are not coloured by good or bad. They are plain black on white.

**Run line.** Directly under the strip: tag run number, models used, definitions version, golden set size, and the date of the run, separated by middle dots.

**Filter bar**

| Filter | Default | Applies to |
| --- | --- | --- |
| Run | The latest tag run, with a picker for earlier runs | The whole page |
| Category | All; any level of the category tree | Blocks 1, 2 and 5 |
| Facet | All; one facet | The facet scorecard, calibration and review outcomes |

A block a filter does not apply to shows a quiet note, "Not affected by the category filter", while that filter is on.

**Block 1: Is it right?**

| Element | Design |
| --- | --- |
| Precision, Recall, Coverage | Three stat pairs in a row. Each shows the tuned-set and holdout numbers side by side, labelled, with the count of answers beneath. The two sets are never combined into one figure. |
| Unknown agreement, Invented quote rate | Two single numbers on a second row. Invented quote rate carries the note "lower is better". |
| Facet scorecard | Sortable table: facet, answers, average confidence, precision, recall, coverage, review rate. Facets with fewer than 20 golden answers are greyed and carry a "Small sample" badge. Default sort: lowest precision first, small samples last. |
| Calibration chart | Stated confidence on the horizontal axis in four bands (below 0.7, 0.7 to 0.8, 0.8 to 0.9, 0.9 to 1.0), actual accuracy on the vertical axis from 0 to 100%. One line per evidence type (text, photo, both) and a thin grey diagonal for perfect calibration. Each point shows its count on hover, and points built on fewer than 20 answers are drawn hollow. |

A definition line under the block: "Tuned set: the golden products the rules were adjusted on. Holdout: golden products labelled without seeing the tagger's answers." with the size of each.

**Block 2: How much human work does it need?**

| Element | Design |
| --- | --- |
| Review rate, Products needing review | Two numbers in a row, each with its count. |
| Product progress | One horizontal bar split into ready, needs review and approved, with the three counts beneath. |
| Approved without change, Changed on review | One split bar for all reviewed tags, the two shares labelled on it, with the count of reviewed tags beside it. They always total 100%. |
| Accepted tags corrected, Category overrides | Two numbers, each with its count and a one-line gloss: "errors that got past the threshold" and "whether the category gate is in the right place". |
| Review outcomes per facet | A table with one split bar per facet (approved without change, changed) and the count of reviewed tags. Each row links to that facet on the Confidence tab. |
| Review time per product | Median time, in minutes and seconds, with the number of products it is based on. |

Before any review has been done in the app, the block shows the review rate, products needing review and product progress, and one empty-state line for the rest: "No reviews yet. These fill in as products are approved."

**Block 3: What does tagging cost?** Every label in this block says "tagging cost", because the categorising step is not included.

| Element | Design |
| --- | --- |
| Tagging cost per product | One number, in cents. |
| Tagging cost per run | One number, with products tagged and the model used beneath. |
| Time per product | One number, in seconds. |
| Projection for the full catalog | Two numbers side by side: standard pricing and batch pricing. The catalog size used is stated beneath ("for 3,000,000 products"). |
| Model comparison | A small table, one row per model: tagging cost per product and precision on the same products. Shown only when a comparison run exists. |

**Block 4: Is it still healthy?**

| Element | Design |
| --- | --- |
| Facets that moved most | Table: facet, unknown rate at baseline and now with the change in points, confidence shift, and a status badge: Stable (plain), Watch (`flag`), Alert (`error`). Sorted by status, then by size of change. |
| Review rate and coverage over time | One line chart across tag runs with two lines. With one run, two single points and the note "Baseline run". |
| Open alerts | A list: facet, what moved, by how much, when. Empty state: "No open alerts". |

**Charts.** Draw the charts as simple inline graphics using the tokens in section 3, plus three series colours defined once as tokens: `series-1` `#1A1A1A`, `series-2` `#2563EB`, `series-3` `#9A6700`. Each series also has its own line style (solid, dashed, dotted) and a direct label at the end of the line, so no chart depends on colour alone. Axes and gridlines are `border`; axis labels are `text-xs` `text-muted`. No legends in boxes, no 3D, no animation.

**Rules for every number**

- Retired facets appear nowhere on the page.
- A metric with nothing to count shows a dash and a short reason in `text-muted`, never 0%.
- Every percentage has its count beside or beneath it.
- Percentages are whole numbers; costs show two significant figures.
- The page shows the same numbers as the command line for the same run.

**Block 5: What does the business get?** The requirements document lists this block and its source ("counts on the tagged catalog") but does not yet define its metrics. Leave it out of the build, with no placeholder card on the page, until they are specified.

## 12. Shared components

Build each of these once and reuse it on every page, including the later live tag screen.

| Component | Design |
| --- | --- |
| Confidence badge | A pill with the number to two decimals and a 40px bar filled in proportion. Below the threshold: `flag` text on `flag-bg`, with the word "Low" before the number. At or above it: `text` on white with a `border` outline. Rule-written facets: "Rule" in `text-muted`. |
| Facet row, open | Line 1: facet name in `text-xs` uppercase, a "Required" marker where it applies, and the confidence badge on the right. Line 2: the value or values as chips in `text-base`. Line 3: the evidence label (Text, Image or Both), the quote in italics inside quotation marks, and the reason in `text-muted`. Actions on the right, visible on hover or focus: Approve, Change, Unknown. |
| Facet row, compact | One 40px line: facet name, value, confidence badge. |
| Value editor | Opens in place under the row, never as a pop-up window. Single-value facets: chips, one selectable. Multi-value: chips that toggle, with "2 of 3 selected" beneath. Number and text facets: an input. Buttons: Done and Cancel. |
| Category card | The full path as breadcrumbs, with the last level in weight 600. Confidence badge on the right. The runner-up beneath as a quiet "Use instead: …" link. "Change" opens a search box over all categories. |
| Evidence panel | Gallery: one large 3:4 photo with numbered thumbnails beneath; click to enlarge. Text: one block per field with its label in `text-xs` uppercase. A clicked quote is highlighted in `highlight` and scrolled into view. A link opens the product on the Ounass website. |
| Buttons | Primary: `action` fill, white text, 36px high. Secondary: white with a `border` outline. Quiet: text only. One primary button per page. Disabled buttons are 40% opacity with a tooltip saying why. |
| Status badge | Pill in `text-xs`: "In review" (`flag`), "Approved · live" (`ok`), "Edited" (`text-muted` outline). |
| Chips | 24px high, 999px radius. Value chips are white with a `border`; flagged facet chips on the list use `flag-bg`. |

**Wording.** Use plain labels: "Approve and publish", "Needs review", "Unknown". Show the facet's display name, with its definition on hover; never show database column names or status codes on screen.

## 13. States, feedback and accessibility

Every screen has a designed state for loading, empty, error and success, so nothing looks broken during the live demo.

| State | Design |
| --- | --- |
| Loading | Grey placeholder blocks in the shape of the content. No spinners on whole pages. |
| Saving | The button shows "Saving…" and is disabled. The page stays usable to read. |
| Saved | A confirmation at the bottom of the screen for four seconds: "Saved". |
| Save failed | A banner in `error-bg` at the top of the right column with the reason and a "Try again" button. Edits stay on the page. |
| Validation problem | The row gets an `error` left bar and a one-line message beneath the value. |
| Unsaved changes | Leaving the page asks for confirmation. |
| Photo failed to load | The placeholder colour with the text "Photo unavailable". |
| Quote not found | A small `flag` note under the quote: "Not found in product text". |

**Motion.** Fades and expansions take 150 milliseconds. Nothing slides, bounces or animates on page load.

**Accessibility**

- Text and its background meet a contrast ratio of at least 4.5 to 1. The tokens in section 3 already do.
- No meaning is carried by colour alone: every flag has a word or icon as well.
- Every action is reachable by keyboard, with a visible `focus` ring. The shortcuts in the Review Screen PRD apply on the detail page.
- Clickable targets are at least 32 pixels high.
- Photos have the product title as alternative text.

## 14. Acceptance checks

**Products and Approved tabs**

- [ ] The Products tab lists every tagged product that is not approved, and no approved product.
- [ ] A product shows "Approve" only when its category and every facet are at or above the threshold and no required facet is missing. Otherwise it shows "Review".
- [ ] Changing a facet's level on the Confidence tab moves products between the two states accordingly.
- [ ] "Approve" on a row publishes that product without opening it; "Review" and a click on the row both open the detail page.
- [ ] "Approve all ready" approves exactly the Ready products in the current filter, after a confirmation showing the number.
- [ ] The state filter, search, department filter, facet filter and sort work together, and the summary line matches the database.
- [ ] An approved product appears in the Approved tab with its approval date and a working "View on website" link.
- [ ] The back link returns to the list with filters and scroll position kept.

**Detail page**

- [ ] Every facet that applies to the product's category is shown with its value and confidence. Flagged facets are grouped first.
- [ ] Each flagged facet can be approved as shown or changed; only allowed values can be chosen.
- [ ] "Approve and publish" stays disabled until every flagged facet is approved or changed and every required facet has a value, and the footer says what is left.
- [ ] "Save" stores decisions, keeps the product in the Products tab, and does not publish it.
- [ ] After approval, the product is published in the catalog tables, appears on the website with the values shown on screen, and has moved to the Approved tab.
- [ ] Undo unpublishes the product and returns it to the Products tab.
- [ ] Changing an approved product and saving changes what the website shows.
- [ ] No product is on the website unless it was approved in this app. `tagger publish` alone publishes nothing.
- [ ] `v_invalid_tags` returns no rows after any save or approval.

**Live tag tab**

- [ ] Pasting an Ounass product URL that is not in the database scrapes, categorises and tags it, and shows the result on the same page in under two minutes.
- [ ] The page fills in step by step: photos and text, then category, then facets, with a time per step and a total cost.
- [ ] The result shows "Ready" or "Needs review" by the same rules as the Products tab, and can be approved or corrected without leaving the page.
- [ ] A category below the threshold pauses the run until the reviewer decides it, then tagging continues.
- [ ] A non-Ounass URL, an unreachable page and a failed model call each show the message in section 7 and leave no half-saved product on the website.
- [ ] A URL already in the database shows the existing result, with "Tag again".
- [ ] A live-tagged product appears in the Products tab, and in the Approved tab once approved.

**Filters**

- [ ] The category filter at any level of the tree returns that category's products and those of every category beneath it.
- [ ] The facet filter returns the right products for a chosen value and for "needs review", on both the Products and Approved tabs.
- [ ] Category, facet, state and search filters combine, show as removable chips, and survive a page reload.
- [ ] "Approve all ready" acts only on the filtered products.

**Confidence tab**

- [ ] Every model-tagged facet and Category has a row with its level, percentage confident, average confidence, unknown percentage and needs-review count, and the figures match the database.
- [ ] Editing a level shows a preview of the new percentage and the number of products that would change state, and changes nothing until Apply.
- [ ] After Apply, the Products tab, the nav badges and the detail page all use the new level without a restart.
- [ ] Raising a level moves affected Ready products to Needs review; lowering it does the reverse, except for facets flagged for another reason.
- [ ] Approved products are unaffected by any level change.
- [ ] A product tagged through Live tag after a change is judged by the new level.
- [ ] Undo restores the previous level and the previous counts.
- [ ] The category filter on this tab changes the figures and not the levels.

**Facets tab**

- [ ] Every facet is listed with its name, definition, judging rules, values and the categories it applies to, and all of it matches the database.
- [ ] Changing a judging rule (for example, making a facet required, or switching it to "Product text only") and saving stores it in the database; the next product tagged, in a batch or through Live tag, follows the new rule.
- [ ] Adding a value makes it available to the AI and to the value editor on the detail page.
- [ ] A value in use cannot be deleted; switching it to inactive stops new use and leaves existing tags unchanged.
- [ ] The rubric is shown read-only and matches the skill file. Nothing in the app writes to the skill files.
- [ ] Saving changes no existing tag and no approved product.
- [ ] Each save appears in History; "Restore" brings back the earlier version as a new change.
- [ ] "Test on a product" re-tags one product and shows the facet's answer before and after.
- [ ] Invalid edits are refused with a message on the field, and nothing is saved.
- [ ] Read-only items (ID, type, categories, rubric, rule-set facets) cannot be edited.

**Models tab**

- [ ] The tab lists exactly the models set up in the tagger's model list, with price and whether each reads photos, and marks the two currently in use.
- [ ] Choosing a different tagging model and saving makes the next Live tag use it, and the "Done" step names it.
- [ ] The categorising model and the tagging model can be set independently.
- [ ] A text-only model shows the warning before it is saved.
- [ ] Saving changes no existing tag and no approved product.
- [ ] Where past runs exist for a model, its accuracy, cost and time per product match the scorecard's figures.
- [ ] The rail footer shows the two models in use and updates after a save.

**Review outcomes**

- [ ] After approving products, the Confidence tab shows per facet how many reviewed answers were approved as shown and how many were changed, and the overall summary line; both match the database.
- [ ] An answer set to unknown counts as changed. A confident answer the reviewer changed is counted under "changed though confident".
- [ ] Changing a confidence level afterwards does not alter the outcomes already recorded.

**Live tag: single product or listing**

- [ ] Pasting a product page link selects "Single product", and no number field is shown.
- [ ] Pasting a category, brand or search page link selects "Listing page", and the number field appears.
- [ ] Clicking the other option overrides the detection, and the number field shows or hides to match.
- [ ] Replacing the link with one of the other type re-runs detection and updates the toggle.
- [ ] Starting a run where the link and the selected option disagree shows the mismatch message and does not run.

**Live tag: listing pages**

- [ ] Pasting an Ounass listing URL switches the box to listing mode with the number field and an estimate.
- [ ] With X set to 5, the app tags the first five products in the order the listing page shows them, each scraped from its own product page with the same fields as a single-product scrape.
- [ ] The table fills in row by row, and each finished row shows Ready or Needs review with a working Approve or Review action.
- [ ] A product already in the database is shown as "Already tagged" and is not scraped or tagged again.
- [ ] One failing product does not stop the rest; "Stop" cancels the products not yet started.
- [ ] A listing page with fewer than X products tags what is there and says how many it found.
- [ ] X above 25 is refused.

**Metrics tab**

- [ ] The page shows the top strip, run line, filter bar and blocks 1 to 4 in that order, and no Business block.
- [ ] Every number matches `tagger evaluate` and the facet scorecard for the same run.
- [ ] Tuned-set and holdout figures are shown side by side and never combined. Without holdout labels, the precision tile shows the tuned-set number with its badge.
- [ ] Facets with fewer than 20 golden answers are greyed and marked "Small sample". Retired facets do not appear anywhere.
- [ ] A metric with nothing to count shows a dash and a reason, never 0%.
- [ ] The calibration chart has one line per evidence type, a diagonal, counts on hover, and hollow points for small counts.
- [ ] Approved without change and changed on review total 100% of reviewed tags, overall and per facet.
- [ ] Before any review is done, block 2 shows its three always-available items and the empty-state line.
- [ ] Every label in block 3 says "tagging cost", and no figure there includes the categorising step.
- [ ] With one run, the over-time chart shows single points and "Baseline run".
- [ ] The run, category and facet filters change only the blocks listed for them, and the others say they are not affected.
- [ ] Loading the page makes no model call and changes no data.

**Design**

- [ ] All colours, type sizes and spacing come from the tokens in section 3; a search of the component code finds no hard-coded hex values.
- [ ] Each page has one primary button. On the Products tab, row-level Approve buttons are the primary style and "Approve all ready" is secondary.
- [ ] Every page works at 1280 pixels wide with no sideways scrolling.
- [ ] Loading, empty, error and saved states match section 13.
- [ ] The whole review of one product can be completed by keyboard, with a visible focus ring throughout.
- [ ] No database column name or status code appears on screen.

## 15. Open questions

1. **"Approve all ready".** Added so that a hundred or more Ready products do not need a click each. Remove it if every product should be approved one at a time.
2. **Levels per facet, not per category.** The Confidence tab sets one level per facet across the whole catalog. A different level for the same facet in different categories (stricter for occasion on dresses than on bags, say) is possible later but not in this build.
3. **Database changes.** Recording approvals and storing confidence levels need the schema changes listed in the [Build PRD](https://claude.ai/artifact/7C6b7hT3F8Zar1Twprh9Ac), section 3, which need a go-ahead.
