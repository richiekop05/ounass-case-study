# Handoff: Richie's — Occasion Assistant & Complete the Look

## Overview
Richie's is a sample luxury store (mobile-first, also works at desktop width) built to demo one feature with two entry points:
- **Occasion assistant** on the product listing page (PLP): one tap on an occasion → an explained shortlist of up to 8 hero pieces.
- **Complete the Look** on the product page (PDP): one card per outfit slot, with an in-place **Swap**.

It also includes two demo tools: a **persona switcher** (4 simulated shoppers) and a **Behind-the-scenes** drawer that shows intent, taste profile and events. `PRD.md` in this folder is the full product spec and is the source of truth for rules; this README covers the visual design and how the prototype behaves.

## About the Design Files
The files in this bundle are **design references built in HTML**. They are working prototypes that show the intended look and behaviour; they are not production code to copy. Rebuild them in the target codebase's environment (React/Next.js, etc.) using its own patterns. If no codebase exists yet, Next.js + TypeScript is a sensible choice, with the catalogue and tag data served from the backend described in the engineering PRD.

- `Richies Store.dc.html` is the prototype. Open it in a browser and it needs `support.js` beside it. The markup is a template with inline styles, and the logic lives in `class Component` in the `<script data-dc-script>` block. **All ranking, slot and learning logic is in that class**, so read it before you implement.
- `PRD.md` is the customer feature design PRD, V3. It adds the "Looks for you: where and when" rules (V2) and the persona dropdown signals and one-lead-module rule (V3). Note that the swap control follows the redesigned card-bottom bar in this README, which replaces the PRD's "text control at top-right".

## Fidelity
**High fidelity** for layout, type, colour, copy and interaction. **Placeholder** for imagery: every product image is a striped block tinted to the product's colour tag. Use real product photography: large, uncropped, square corners, on its original background. Catalogue items and brand names are invented sample data.

## Design Tokens
Colours
- Background ivory `#FAF8F5`
- Text near-black `#1A1A1A`
- Secondary text, stone `#8A857D`; muted body `#5E5A54`; description body `#4A4741`
- Hairlines `#E5E0D8`; chip outline `#D6CFC4`; drawer row divider `#EFEBE5`; loading block `#F2EEE8`
- Accent (one only), muted bronze `#8C6A3F`, used for occasion chips, active refine and filter states, and "Styling for:"
- Demo chrome (persona bar and BTS drawer): bg `#141312`, text `#EDE8E0`, muted `#9C968C`, dim `#6E6962`, dividers `#2A2826`/`#23211F`/`#3A3733`, highlight `#D9B98A`
- Toast: bg `#1A1A1A`, text `#FAF8F5`
- No shadows and no gradients, except the image placeholder stripes, which are temporary.

Typography
- Display: **Cormorant Garamond** 400/500, used for the wordmark (500, clamp(32px,5vw,42px), lh 1), section headings such as "Where are you going?" (400, clamp(34px,5.5vw,52px), lh 1.05), the PDP product name (400, clamp(34px,4.5vw,46px)), "Complete the look" (32px), the grid heading (clamp(26px,3.5vw,34px)), look card titles (26px) and the filter drawer title (30px)
- UI and body: **Instrument Sans** 400/500. Card name 14px (15px on hero cards); price 13px `#5E5A54`; body 14px with lh 1.65
- Labels: 10.5–11px, uppercase, letter-spacing 0.16–0.18em (brand names, nav, slot names, buttons)
- Demo chrome: **IBM Plex Mono** 12–13px
- Image placeholder captions: IBM Plex Mono 10px uppercase, 0.08em

Shape and spacing
- Radius 0 everywhere, except chips (999px pills)
- Page container max-width 1280px, side padding clamp(16px,4vw,48px)
- Grid gaps: row clamp(28px,4vw,48px), column clamp(12px,2.5vw,32px)
- Image aspect: grid card 3:4, hero shortlist card 4:5, slot card 3:4
- Buttons: primary is a solid `#1A1A1A` block with ivory text, 54px high, 12px uppercase 0.18em, hovering to `#33312E`. Secondary is a 1px `#1A1A1A` outline with padding 15×24, filling black with ivory text on hover. Size buttons are 48×48 with a 1px `#E5E0D8` border; selected is solid black.

Motion
- Fades and colour transitions run 200–300ms ease. The swap cross-fade is 230–240ms on opacity. The BTS drawer uses opacity plus a 24px translate over 300ms. Nothing bounces, and nothing auto-advances.

## Screens / Views

### Site frame (every page)
1. **Persona bar** (V3): sticky at the top, dark, monospace.
   - Left: "Shopping as: {Name} · Low intent ▾". The intent text is `#D9B98A` for low and `#C8C2B8` for high. No age appears anywhere.
   - Right: "High intent from next page" (in `#D9B98A`, only while a change is pending) and a "Reset session" link.
   - The dropdown is min(440px,92vw) wide and scrolls if taller than the viewport. Each entry has four parts, top to bottom:
     - Name and type ("Layla · the Wanderer") with an intent badge on the right. The badge is 10.5px uppercase: low is a solid bronze `#8C6A3F` fill with ivory text; high is a `#6E6962` outline with `#C8C2B8` text.
     - 3–4 signal lines (11.5px `#C8C2B8`, each prefixed "–").
     - One "→ What the store does" line in `#D9B98A`.
   - The current persona's row has a `#24221F` background. A caption at the foot reads "Simulated starting sessions".
   - Signal copy comes from PRD §4, "What each dropdown entry shows".
   - Switching or resetting starts a fresh session and reloads the current page.
2. **Store header**: a 3-column grid with the "Richie's" wordmark centred (clicking it goes to the PLP) and a bag icon with count on the right (an 18px line icon). Below that, a centred nav of Women / Men / Shop all; the active item has a 1px underline. Bottom hairline. No search.
3. **Toast**: fixed, centred, top 132px, reading "Added to bag" or "Look added to bag". Fades in over 300ms and hides after 1.8s.
4. **Behind-the-scenes**: a fixed vertical tab on the right edge labelled "BEHIND THE SCENES". The drawer is min(440px,94vw) wide and dark. Its sections are:
   - Intent (large LOW/HIGH, the rule, and a pending note)
   - Taste profile: top 3 values per dimension (Aesthetic, Colour, Formality, Coverage) with a 6px bar and a %, or "No signal yet" when empty, followed by the line "Starting history is simulated"
   - Last five events, each with a delta line such as "statement aesthetic down · gold down"
   - On the PDP, product tags with confidence values, a "Sample" badge and no confidences for sample items

### PLP
Top to bottom:
1. **Feature band**: only for low-intent personas, never for Sara. It shows **one lead module** (V3): "show when the store knows, ask when it does not".
   - Looks for you leads when the shopper is eligible and has a taste profile: Layla from the start, and James once he becomes eligible.
   - Otherwise the occasion picker leads: Noor gets it with evening suggested, and James at the start gets the "Who is it for?" opener.
   - When Looks for you leads, the picker collapses to one centred bronze text button, "Shopping for an occasion? ↓" (13px, underline on hover). Tapping it opens the opener (if James hasn't answered it) or the chips in place, below the looks row. Choosing an occasion replaces the band with the shortlist.
   - The picker state resets on the next page load, but "Back to shortlist" reopens it.
   - The full picker and the looks row are never shown stacked by default, and on mobile the lead module takes at most about half the first screen. Padding clamp(40px,7vw,72px) above and clamp(40px,6vw,64px) below, then a bottom hairline.
   - **Looks for you, top row** (only when it is the lead module, before an occasion is chosen): a short row that keeps the occasion picker in view. It has an 11px stone label, then up to 3 compact cards in a flex row (flex 1 1 250px with min-width 250px, 16px gap). It fits 3 across on desktop, scrolls on narrow screens, and hides the scrollbar. Each card has a 1px `#E5E0D8` border (darkening to `#8A857D` on hover) and 10px padding. Inside, an 84px 3:4 hero image sits beside a serif title (22px), an 11px stone one-line subline, and a row of three 30px 3:4 thumbnails. Every image has its own 1px `#E5E0D8` hairline. The title is built from the tags (`{Aesthetic} {occasion}`, e.g. "Minimalist evening"), and tapping a card opens the hero's PDP with that occasion carried over.
   - **Opener** (James, before he answers): "Who is it for?" with bronze pill choices For me / For her / For him. The answer sets the department (me and him → men, her → women).
   - **Occasion picker**: "Where are you going?" over bronze outline pills (padding 11×24, 13px). Hover fills them bronze with ivory text. Only occasions with 4 or more in-stock hero pieces in the department are shown. For Noor, Engagement and Evening come first and are filled, with the note "Evening occasions first, from your recent order". James sees "Shopping for her · Change". Below the chips is a stone link, "Or describe it in your own words", which opens a centred single-line input with a bottom-border; Enter keyword-matches it to an occasion, and on a miss it shows "Nothing matches that yet. Try one of the occasions above."
   - **Shortlist** (after an occasion is chosen):
     - Title "For {an evening event}" with a "Change" link, and a subline with the count and why the list was chosen (copy varies by persona).
     - A refine row of outline pills: "Under AED 3,000", "Neutrals", "More covered". Active ones turn bronze.
     - A grid of max 3 columns on desktop and 2 on mobile.
     - Hero card: 4:5 image with a heart at top-right, brand, name, price, then up to 3 reason pills (11px, `#D6CFC4` outline).
     - Empty state: "Nothing matches that yet." plus a bronze "Clear the refinement" link.
2. **Filter bar**: a horizontally scrolling row of square outline buttons for Category, Colour, Occasion, Formality, Season, Aesthetic, Sleeve length and Length. A button with active values shows " · n" and turns bronze. Applied filters appear below as black-outline pills with ×, followed by "Clear all".
3. **Grid heading**: "Selected for you" (taste-ranked) or "New in" (newest first), with "{n} pieces" on the right.
4. **Grid**: max 4 columns on desktop, 2 on mobile, using `repeat(auto-fill, minmax(max(150px, calc((100% - 96px)/4)), 1fr))`. Card: 3:4 image with a heart (♡/♥) at top-right, brand at 10.5px uppercase, name on one line with ellipsis, then price. No badges or ratings.
5. **Look cards in the grid** (V2, build first): after every 8th product, for at most 3 per page, insert a look card that spans 2 grid columns and one row (`grid-column: span 2`). It's a button with a 1px `#E5E0D8` border and padding clamp(10px,1.5vw,16px), using the same ivory background and type as the product cards, so it doesn't read as an advert. Inside are two equal columns:
   - Left: the hero image at 3:4, with `width:100%; align-self:start` so its width drives the aspect ratio and it never overlaps the text column.
   - Right: the label "A look for you" (10.5px uppercase stone), a serif title (clamp(22px,2.4vw,30px)), a 12px subline, a row of 3 thumbnails at 3:4 pinned to the bottom, and an underlined "View the look" label.

   When the top row is showing, the grid shows looks 4–6 so they don't repeat. Eligibility rules are under Interactions.
6. **Filter drawer**: an ivory panel on the right, min(400px,100vw) wide, with a 22% black scrim. It has a serif title and "Close". Each row has a 14px square checkbox, the label and a count. The footer is a primary button, "Show {n} pieces".

### PDP
- "← {Department}" link at the top.
- Two columns that wrap on mobile: a gallery (flex 1 1 420px, max 680) and info (flex 1 1 360px, max 560), with a gap of clamp(28px,5vw,72px).
- **Gallery**: a horizontal scroll-snap strip of 3 full-width 3:4 images, with the caption "Swipe for 3 views".
- **Info**, in order:
  - The "Styling for: {occasion}" line, only if an occasion was carried over. It sits between hairlines, with "Styling for:" in bronze and a "Back to shortlist" link.
  - Brand label, serif name, price.
  - Size buttons, if the product is sized.
  - "Add to bag".
  - **Complete the look**, for low intent only:
    - Serif title, with the subline "Built around this piece[ for an evening event]".
    - A row of slot cards (flex 1 0 148px, max 240, scrolling on mobile). Cards are 20px apart. Each card shows the slot label (uppercase) with the position ("2 of 5", stone, 11px) at top-right, then a 3:4 image, brand, name, price and one reason pill.
    - The swap control is deliberately quiet so it doesn't pull every click. It's a 40px row at the bottom of each card, pinned with `margin-top:auto` so rows align, with a 1px `#E5E0D8` rule above it. On the left, a "←" back button (13px, stone `#8A857D`, 35% opacity and disabled on the first option). On the right, the text button "Swap ⇄" (11px, 0.06em, stone). Both darken to `#1A1A1A` on hover. There is no border box and no fill.
    - On the last option the text reads "No more options" in `#B5AFA6`, disabled. A slot with a single option has no swap row and no counter.
    - Below the row: the label "The look · n pieces", the total, and a secondary "Add look to bag" button.
    - With only one slot, the title becomes "Pairs well with" and there is no total.
  - **About this piece**, for low intent only: tag pills. Tapping one opens the PLP filtered to that tag.
  - Accordions: Description, Details, Delivery and returns.
  - **More looks for you** (V2, optional): for eligible low-intent shoppers only, placed below the accordions. A 28px serif title over up to 3 stacked compact look cards (72px hero, title, subline, thumbnails). It never includes the product on the page, either as a hero or as a look item.
  - **High intent (Sara)**: no module after Add to bag. Instead a compact "Pairs well with" rail of 104px cards (image, brand, price) sits below the accordions. About this piece is hidden.

## Interactions & Behaviour
- **Personas** (see PRD §3):
  - Layla: strong taste weight (1.5), with "Matches your minimalist picks" chips.
  - Noor: occasion-led (taste weight 0.4); chips read "Right for an evening event".
  - James: taste weight grows as 0.3 × learned events, capped at 1.2. He starts with a strong variety penalty that fades as he interacts. His Complete the Look chips read "Goes with the {gown} you chose".
  - Sara: high intent, with no feature band.
- **Intent**: using a filter, tapping a tag chip or selecting a size sets `intentNext = high`. The current page doesn't change; the next navigation applies it. Refine chips do NOT change intent.
- **Shortlist ranking**:
  - Score = occasion fit (1.0 if it's the product's primary occasion, otherwise 0.7) + 0.3 if formality matches the occasion + taste weight × taste.
  - Greedy pick of up to 8, with at most 2 per brand and 2 per colour, and a penalty for repeating an aesthetic.
  - Out-of-stock items are never suggested.
- **Reason chips**: occasion first, then formality or "Covered", then the taste chip if the profile value is ≥ 0.45. Maximum of 3.
- **Complete the Look slots**:
  - A dress gets shoes, bag and jewellery. A top or outerwear gets bottom, shoes, bag and jewellery. A bottom gets a top, shoes and bag. An accessory gets a dress or top plus the other two accessory slots. Fragrance and anything else without an outfit role gets no module.
  - Candidates must pass a compatibility threshold of 0.9 (formality distance, aesthetic match or pair, colour harmony, occasion). They are then ranked with taste, giving at most 5 per slot.
  - A slot with no candidates is hidden.
  - The candidate list is frozen when the PDP opens, so swaps step through a stable list.
- **Swap**: the card fades out (opacity 0, about 230ms), the slot index changes, and it fades back in. The rest of the look stays still and the total updates. Swapping forward records an event that lowers the outgoing item's aesthetic and colour by 0.15. Back doesn't teach anything.
- **Looks for you eligibility** (V2):
  - Layla is eligible from the first page; her looks are built from her taste.
  - Noor is eligible from the first page, but every look is built for the engagement occasion because her history is thin. She gets grid cards only, with no top row, so her picker comes first.
  - James is not eligible at first. Once his session has 3 or more learned signals (views, saves, swaps, occasion taps, opener answer, add to bag), he becomes eligible on the next page load. An event is logged, "Became eligible for Looks for you", and the first card appears in his grid.
  - Sara is never eligible.
  - The BTS drawer has a "Looks for you" section showing ELIGIBLE, NOT YET or NEVER, the reason (e.g. "2 of 3 signals this session"), a pending note when the threshold has been reached but the page hasn't reloaded yet, and when the shopper became eligible.
- **Learning**: profile values are clamped to 0–1.
  - View: +0.12 on aesthetic, colour, formality and coverage
  - Save: +0.2 on aesthetic and colour
  - Add to bag: +0.2
  - Occasion tap: +0.1 on the occasion's formality
  - "More covered": +0.15 on covered
  - Every event is pushed to the event log.
- **Loading**: after a navigation or persona switch, the grid shows ivory placeholder blocks in the final layout for about 380ms. No spinners.
- **Bag**: the count increments and a toast appears. There is no bag page.
- **Responsive**: everything is fluid. Grids use auto-fill with min/max tracks, and the PDP columns wrap below about 820px.

## State (from the prototype's `fresh()`)
`pickerOpen, looksEligible, eligSince, persona, page ('plp'|'pdp'), pdpId, dept ('women'|'men'|'all'), occasion, carriedOcc, opener, refine{budget,neutral,covered}, filters{dim:[values]}, drawer, intent, intentNext, intentRule, profile{aes,col,form,cov}, events[], learn, saved{}, bag, size, ctl[{slot,items[]}], slotIdx{}, fading{}, acc{}, btsOpen, loading, ownOpen, ownText`

In production, the catalogue, tags, persona histories and ranking come from the backend (see the engineering PRD). The prototype's `RAW` catalogue is a pipe-delimited stand-in: `id|dept|cat|brand|name|price|colour|aesthetic|formality|occasions|coverage|season|sleeve|length|flags`, where flag `S` means sample and `X` means out of stock.

## Assets
- No image assets; product photography is still to be supplied.
- Fonts come from Google Fonts: Cormorant Garamond, Instrument Sans and IBM Plex Mono.
- The only icon is the bag, an 18px stroke icon: a rectangle with an arc handle. The heart uses the text glyphs ♡ and ♥, and Swap uses the glyph ⇄. Swap these for the codebase's icon set if it has one.

## Files
- `Richies Store.dc.html` is the full prototype (template, inline styles, and the logic class with the catalogue, personas and algorithms).
- `support.js` is the runtime needed to open the prototype in a browser.
- `PRD.md` is the customer feature design PRD.
