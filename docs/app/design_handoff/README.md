# Handoff: Ounass Tagger review app

## Overview
An internal back-office app for the Ounass product tagger. An AI pipeline categorises each product and fills in its facets (colour, material, occasion…); this app is where people review those answers, approve products so they publish to the website, tag new products live from an Ounass URL, monitor quality, and tune the settings (confidence levels, facet rules, models). **Nothing reaches the website until a person approves it.**

Seven tabs: **Products**, **Approved**, **Live tag**, **Metrics**, then under "Settings": **Confidence**, **Facets**, **Models**. Plus a product **detail page**.

## About the design files
The files in `prototype/` are **design references built in HTML**: a clickable prototype that shows the intended look and behaviour. They are not production code to copy. The task is to **recreate the design in the target codebase** (React/Next, Vue, etc.) using its established patterns, connected to the real tagger database and pipeline. If there is no app codebase yet, pick a suitable stack (for example React + TypeScript with a small server reusing the existing Python pipeline) and build it there.

Open `prototype/Tagger.dc.html` in a browser to click through it (keep `support.js` next to it). All data in the prototype is simulated in-browser; see "Simulated in the prototype" below.

## Source of truth
**`Ounass Tagger Design PRD v7.md` is the specification.** It defines every screen, rule, state, wording and acceptance check. Build to the PRD. Where the prototype and PRD differ, the PRD wins. This README summarises the PRD and records the prototype's decisions on points the PRD leaves open.

Related documents referenced by the PRD (not included; ask the product owner): Review Screen PRD (keyboard shortcuts, data read/written), Build PRD (schema changes §3, approval transactions §5, metric calculations §11), "Tagger app: Metrics tab requirements".

## Fidelity
**High fidelity.** Colours, type, spacing, radii and copy are final (PRD §3). Recreate pixel-accurately, but put every value in a single tokens file as CSS variables; the PRD requires no hard-coded hex values in components. (The prototype inlines values only because of how it was built.)

## Design tokens (PRD §3)
**Colour**
| Token | Value | Use |
|---|---|---|
| bg-page | #FAFAF8 | Page background |
| bg-surface | #FFFFFF | Cards, tables, panels |
| border | #E6E4DF | Borders, dividers |
| text | #1A1A1A | Body text, values |
| text-muted | #6B6B6B | Labels, helper text, reasons |
| action | #111111 | Primary button, active nav, link hover |
| flag / flag-bg | #9A6700 / #FFF4D6 | Below threshold, needs a decision |
| ok / ok-bg | #1F7A4D / #E8F5EE | Approved, saved, published |
| error / error-bg | #B42318 / #FDECEA | Validation, missing required, failed save |
| highlight | #FFE98A | Highlighted evidence quote |
| focus | #2563EB | 2px focus ring on every interactive element |
| series-1/2/3 | #1A1A1A / #2563EB / #9A6700 | Chart lines (solid / dashed / dotted) |
| photo placeholder | #F3F2EE | Photo loading / unavailable |

Prototype-only helper shades (add as tokens if kept): `#ECEAE4` (placeholder stripe), `#EBD9A6` (track of the low-confidence bar), `#C9C6BE` (chart diagonal, underline colour), `#D6D3CC` (switch off state).

**Type:** Inter, then system sans-serif; weights 400/500/600 only; tabular figures for numbers.
xs 12/16 uppercase, letter-spacing 0.04em (labels, table headers) · sm 13/20 (reasons, helper) · base 14/22 (body, values, cells) · lg 18/26 weight 600 (titles, headings). Metrics tile numbers 32/40 weight 600; stat numbers 24/32 weight 600.

**Spacing:** 4, 8, 12, 16, 24, 32, 48 only. **Radius:** 6px on cards, inputs and buttons; 999px on badges and chips. **Cards:** white, 1px border, 16px padding, no shadow. **Shadows:** popovers, confirm dialog, toast and enlarged photo only. **Motion:** 150ms fades and expansions only. **Minimum width:** 1280px, 24px page margins. Light theme only.

## App shell (PRD §4)
- **Left rail**, 200px, white, right border. "Tagger" in lg at the top. Tabs are 36px rows; the active tab has a 2px `action` left bar and weight 600. Products and Approved show count badges (pill, #F3F2EE, 12px) that update immediately on approve. A divider and a "SETTINGS" xs label sit above Confidence, Facets and Models.
- **Rail footer** (xs, muted): run ID, default confidence level, categorising model, tagging model.
- **Top bar**, 56px, white, bottom border: page title left, page controls right.
- **Toast**: bottom centre of the content area, #1A1A1A background, white text, underlined action links ("Undo", "View on website"). 8s for approvals and undoable actions, 4s for "Saved".
- **Confirm dialog**: 420px, white, 6px radius, title in lg, secondary Cancel and primary OK.

## Screens
Each is summarised below; the full rules are in the PRD section given.

### Products / Approved (§5)
One table, 72px rows. Columns: photo thumbnail (48×64) · product (title weight 500, brand and ID muted) · category (last two levels, plus a "Category in review" flag pill) · facets ("14 of 14 confident" muted, or "3 of 14 need review" plus up to three flag-bg chips and "+N") · lowest confidence (confidence badge) · action (**Approve** primary or **Review** with a flag outline, right-aligned). Clicking a row opens the detail page.
- The filter row under the top bar has state pills (All / Ready / Needs review), a category tree select, a facet select with a condition select (value / has a value / unknown / needs review), and Sort (Needs review first, which is the default and puts lowest confidence first; Brand A to Z).
- Active filters show as removable chips with "Clear all".
- The top bar has a summary line ("120 ready · 42 need review · 162 of 241 tagged"), search, and "Approve all ready (N)" as a secondary button with a confirmation.
- **Filters live in the URL** (the prototype uses the hash; use query params in the real app) so they survive reload and the back button.
- Approving from the list fades the row (150ms), then shows the toast with Undo for 8s.
- The Approved tab replaces the Facets column with the approval date and the Action column with a "View on website ↗" link.
- Empty states: "Everything tagged so far is approved" (with a link to Approved) and "Nothing approved yet". With filters on, "No products match these filters" and "Clear all filters".

### Detail page (§6)
- **Top bar:** "← Products" (or ← Approved / ← Live tag, depending on where the user came from), title, brand, a status pill (Ready / Needs review / Approved · live), "n of N", and prev/next buttons that follow the list's filter and sort.
- **Layout:** two columns, 42% / 58%, each scrolling on its own.
- **Left (evidence panel):** a 3:4 main photo (click to enlarge in a lightbox), numbered 48×64 thumbnails, and product text blocks (Description, Details, Composition, Size & fit) with xs labels. Clicking a facet's quote highlights it in `highlight` and scrolls it into view. Link to the product on Ounass.
- **Right:** the category card (breadcrumbs with the last level weight 600, confidence badge, "Use instead: …" runner-up link, Change which opens search over all categories). Below it, the **Needs review** group (open rows with a 3px flag bar) and then the **Confident** group (compact 40px rows that expand on click). Rule-written facets show "Rule" and are read-only. If the category is in review, the facet rows are locked (40% opacity) until it is decided.
- **Open row:**
  - Line 1: name (xs uppercase, definition on hover), Required marker, confidence badge.
  - Line 2: value chips.
  - Line 3: evidence label (Text/Image/Both), the quote in italics, the reason in muted, and "⚠ Not found in product text" where it applies.
  - Actions: Approve, Change, Unknown (Unknown only for optional facets).
  - Once decided, the bar turns `ok` and the row shows "✓ Approved" or "✓ Changed". Rows don't move between groups.
- **Value editor**, opening in place: single-value facets are selectable chips; multi-value facets are toggling chips with "2 of 3 selected" and a max limit; number facets are an input showing the allowed range. Done / Cancel; validation errors show inline.
- **Footer**, fixed at the bottom of the right column, 64px:
  - The left side shows what is left ("3 facets still need a decision"); clicking it scrolls to the first open row. It shows "Unsaved changes" when there are edits.
  - Buttons: Save (secondary) and Approve and publish (primary, disabled at 40% with a tooltip until every flag is decided and required facets have values).
  - On an approved product: "Send back to review" (secondary) and "Save changes" (primary).
- After approving, the toast shows "Approved and published" with View on website and Undo, and the page moves to the next product in the list.
- Leaving with unsaved decisions asks for confirmation.

### Live tag (§7)
- **Toggle above the box:** "Single product" / "Listing page" (pill segmented control). It auto-selects from the pasted URL and shows "Detected: product page" or "Detected: listing page" (with a time and cost estimate for listings). The user can override it; changing the URL runs detection again. If the link and the selected mode disagree when the run starts, a red message names the mismatch and offers a "Switch to …" button, and nothing runs.
- **Input:** a 48px URL box and a primary button ("Tag product", or "Tag N products" in listing mode). Listing mode adds a "First [5] products" number field (1–25).
- **Single product:** a four-step tracker (Fetching product → Categorising → Tagging → Done, with the time per step; Done shows total time, cost and the two models). The result renders progressively in the same two-column layout as the detail page.
  - If the category is low, the run pauses after step 2 until the reviewer decides the category.
  - A failed fetch or model call stops the tracker at that step in `error` with "Try again"; a retry after a model failure resumes from the failed step.
  - A product already in the database shows "Already tagged" with "Tag again".
- **Listing:** a table with # · Product (placeholder until scraped) · Step (Waiting, Fetching, Categorising, Tagging, Done, Failed + reason + Try again, Cancelled) · Result (Ready / Needs review + "N flagged" / Already tagged) · Action (Approve / Review).
  - A progress line above it ("3 of 5 done · 2 min 10 s · $0.21") with a Stop button, which lets the current product finish and cancels the rest.
  - When finished, the line becomes "5 tagged: 3 ready, 2 need review", with "Approve all ready (N)" and "Open in Products", which opens Products filtered to these products as a chip.
  - Products already in the database are not re-run. A low category does not pause the batch. One failure doesn't stop the rest. If the page lists fewer products than requested, it says "This page showed 18 products".
- "Tag another" in the top bar clears the page. The last five URLs appear as links under the box.

### Metrics (§11)
Read-only; it makes no model calls.
- **Top strip:** five equal tiles (Tag precision, Auto-accepted, Review rate, Category accuracy, Tagging cost per product). Each has an xs label, a 32px number and a muted line. A "Tuned set" badge shows when there are no holdout labels.
- **Run line** (xs muted) and a **filter bar** (Run, Category, Facet) with jump links.
- Four white cards, 32px apart:
  1. **Is it right?** Precision/Recall/Coverage as tuned-set and holdout pairs, Unknown agreement, Invented quote rate, a sortable facet scorecard ("Small sample" greyed if under 20 answers), a calibration chart (3 series and a grey diagonal; hollow points under 20; counts on hover) and a definition line.
  2. **How much human work does it need?** Review rate, products needing review, a product progress bar (ready / needs review / approved), the reviewed-tags split bar (always 100%), accepted tags corrected, category overrides, review time, and review outcomes per facet (each row links to the Confidence tab). Empty state: "No reviews yet. These fill in as products are approved."
  3. **What does tagging cost?** Every label says "tagging cost". Shows per product, per run, time, a projection for 3,000,000 products at standard and batch pricing, and a model comparison table when a comparison run exists.
  4. **Is it still healthy?** Facets that moved most (Stable plain / Watch flag / Alert error), a review rate and coverage over-time chart ("Baseline run" with a single run), and open alerts.
- No Business block. Blocks a filter doesn't apply to say "Not affected by the … filter".
- Charts: simple inline SVG, gridlines in `border`, axis labels xs muted, a direct label at the end of each line, no boxed legends.
- Rules: a dash plus a reason instead of 0%; every % has its count; whole-number %; costs to two significant figures.

### Confidence (§8)
- A review outcomes summary line above the table.
- One row per facet with Category first. Columns: Facet (Required marker, definition on hover), Confidence level (number input 0–1 in steps of 0.05, "default" label), Confident (% + a 120px bar + "182 of 214 products"), Average, Unknown, Needs review (a link that opens Products filtered), Reviewed so far ("18 approved as shown · 6 changed", "25% changed", "⚠ N changed though confident").
- Sorted weakest first; rule facets greyed at the bottom.
- Editing a level shows a preview under the row ("0.80 → 0.70: Confident rises from 74% to 85%. 12 products move…") with Apply / Cancel. Editing several rows shows a sticky bar with the combined effect and Apply all / Discard. Apply shows the toast with Undo.
- Top bar: Default level (with Apply), category filter, search, and a quiet "Reset all to default" with a confirmation.

### Facets (§9)
- **Left pane, 320px:** search, a category filter, and groups Tagged by AI / Set by rule / Retired. Each row shows the name, a value count and an "Edited" marker.
- **Right pane:** cards in this order: Basics (name, definition, ID + type muted) · How it is judged (evidence source as three chips, Required switch, no-answer behaviour, maximum values) · Values table (label, definition, products using it, Active switch, remove; values in use can't be removed; "+ Add value") · Rubric (read-only on bg-page, 6 lines + "Show all", "Managed in the skill file") · Applies to (chips) · Confidence level (link to the Confidence tab) · History (last five changes; View shows the earlier version; Restore applies it as a new change).
- A fixed footer holds Discard and Save changes. Validation errors show on the field (error border or left bar plus a message).
- After saving: toast "Saved. This applies to products tagged from now on.", a banner "N products were tagged with the earlier version" with a **Test on a product** panel (search or paste a URL → before/after columns showing value, confidence, evidence and reason, next to a photo → "Now in Products as Ready / Needs review").
- Rule-set and retired facets are read-only. Unsaved edits ask for confirmation when leaving the page or switching facet.

### Models (§10)
- Two sections (Categorising model, Tagging model), each a 4-column row of model cards.
- Card: name in lg, "Current" ok badge, price per million tokens (muted), "Reads photos" / "Text only" (the text-only note in flag: "Facets judged from photos will go to review"), and results so far (accuracy against the golden set, cost and time per product) or "Not run yet".
- The selected card has a 2px `action` border. A footer with Discard / Save changes appears once the selection differs from what is saved. Saved toast: "Saved. Products tagged from now on use this model."

## Shared components (PRD §12)
- **Confidence badge:** a pill showing the number to 2 decimals and a 40px bar. Below the threshold: "Low 0.62" in flag on flag-bg. At or above it: text on white with a border. Rule facets show "Rule" in muted.
- **Buttons:** primary uses the action fill with white text, 36px. Secondary is white with a border. Quiet is text only. Disabled buttons are 40% opacity with a tooltip saying why. One primary button per page.
- **Status pill:** xs uppercase.
- **Chips:** 24px high, 999px radius.

All of these are built once and reused across screens.

## Key business rules
- **Ready** means the category and every model facet are at or above their level, no required facet is empty, no quote is missing from the product text, and no facet is flagged for review. Anything else is **Needs review**.
- A facet's level is its own level if set, otherwise the default. Changes apply immediately to products waiting for approval and to future tagging; approved products are never affected.
- Approval is the only path to the website. The tagger's original answers are kept alongside the reviewer's. `tagger publish` no longer publishes.
- **Review outcomes** are recorded at approval time: for each decided answer, whether it was changed (setting it to unknown counts as changed) and whether it was above the level ("changed though confident"). Later level changes don't alter recorded outcomes.

## State the real app needs (server-backed)
Products with their tagger answers (value, confidence, evidence type, quote, reason, quote-not-found), category and runner-up, reviewer decisions (draft and saved), approval date and outcomes · confidence levels (per facet and default) · facet configs (name, definition, evidence source, required, no-answer behaviour, max values, values with label, definition and active flag) and their history · selected models · tag runs and their metrics · live-tag and batch job progress, streamed from the server (SSE or websockets).

Client-only state: list filters (in the URL), detail-page drafts, open editors, toasts and confirms.

## Simulated in the prototype
Sample products (14 starting, plus pool and listing products), models and prices, golden-set metrics, earlier runs, health trends, run timings and costs, and re-tag results are all invented. Products generated from listings reuse a sample product's text and facets. Persistence covers this browser session only: the URL hash plus localStorage for navigation. Replace all of it with real data from the tagger database and pipeline (`tagger run --product ID`, the scraper, `tagger evaluate`).

## Assets
No images or icons. Product photos are striped placeholders in the prototype; use the real product images (3:4, cropped to fill, #F3F2EE while loading, "Photo unavailable" on error, product title as alt text). Glyphs used: ←, →, ›, ×, ✓, ⚠, ↑, ↓, ↗. Font: Inter from Google Fonts (weights 400, 500, 600).

## Files
- `Ounass Tagger Design PRD v7.md`: the full specification and acceptance checks (§14).
- `prototype/Tagger.dc.html`: the clickable prototype. The markup at the top shows the exact layout and styles per screen; the logic class at the bottom holds the sample data and business rules (readiness, filters, approval, outcomes, live and batch runs, facet editing, metrics).
- `prototype/support.js`: the runtime the prototype needs to open in a browser. It is not part of the design.
