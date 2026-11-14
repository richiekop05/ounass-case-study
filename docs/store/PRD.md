# Richie's — Customer Feature Design PRD

Nov 14, 2026 · @Richard George

## 1. Purpose and scope

This PRD describes how the customer-facing sample store, Richie's, should look and behave so that Claude Code and Claude Design can build it. It covers one feature with two entry points: the occasion assistant on the product listing page and Complete the Look on the product page.

The store exists to demonstrate the feature for the Ounass case review. It is a prototype in the spirit of a Gulf luxury retailer, not a copy of the Ounass site. The panel knows their own app, so the design should be visibly its own.

**In scope**

- A product listing page (PLP) and a product page (PDP)
- The occasion assistant, including the shortlist and the reasons shown for each item
- Complete the Look, including swap
- A persona switcher and a behind-the-scenes panel for the demo
- Mobile-first layout, because the brief places the feature in the Ounass app; the same pages must also work at desktop width for screen sharing

**Design principles**

1. The customer never needs vocabulary. They choose an occasion or a product; the tags do the rest.
2. Every suggestion shows why it was chosen, in the words of the tags.
3. The feature recedes for a shopper who already knows what they want.
4. Nothing is shown unless it is a good match. An empty slot is hidden, not filled with a weak item.

The rules and data behind these screens are in the companion engineering PRD.

## 2. Brand: Richie's

Richie's should read as a quiet luxury store: generous space, large photography, restrained type, and no visual noise. If a choice is between adding an element and leaving space, leave space.

| Element | Direction |
| --- | --- |
| Wordmark | "Richie's" set in a high-contrast serif, centred in the header, sentence case with the apostrophe |
| Display type | A refined serif (Cormorant Garamond or similar) for the wordmark, page titles and look titles |
| Body and UI type | A clean sans-serif (Inter or similar); labels in small uppercase with wide letter-spacing |
| Background | Warm ivory, `#FAF8F5` |
| Text | Near-black, `#1A1A1A`; secondary text in stone, `#8A857D` |
| Lines | Hairline rules in `#E5E0D8`; no drop shadows |
| Accent | One accent only, muted bronze `#8C6A3F`, used for the active state and the occasion entry |
| Shapes | Square corners on images, cards and buttons; no pills except the tag chips |
| Buttons | Primary is solid near-black with ivory text; secondary is a hairline outline |
| Imagery | Product photography carries the page; images are large, uncropped and on their original background |
| Motion | Slow, subtle fades (200 to 300 ms); nothing bounces or slides in quickly |
| Prices | In AED, plain weight, no strikethroughs or discount badges |

**Product card.** Image first, then brand in small uppercase, product name in one line, and price. No ratings, no badges, no hover clutter.

**Tone of voice.** Short and composed. "Where are you going?" not "Find your perfect outfit!". No exclamation marks, no urgency language, no emoji.

**What to avoid.** Bright colours, gradients, rounded cards, carousels that auto-advance, pop-ups, and anything that looks like a marketplace.

## 3. Personas

The store is built for four simulated shoppers. Three are low intent and see the feature; one is high intent and proves the feature stays out of the way.

| Persona | Who they are | What we know at the start |
| --- | --- | --- |
| Layla, the Wanderer | Dubai. A regular with no mission tonight; likes neutrals and clean lines | A rich history of views, saves and purchases |
| Noor, the Occasion-haver | Abu Dhabi. Needs an outfit for an engagement in three weeks; does not know designers | One past order and a short session |
| James, the Newcomer | Visiting from the UK. Buying something for his wife; has never used the store | Nothing |
| Sara, the Hunter | Riyadh. Arrived from a search for one specific bag and has selected a size | A high-intent session |

**How each screen changes**

| Screen element | Layla | Noor | James | Sara |
| --- | --- | --- | --- | --- |
| Top of the PLP | "Looks for you" leads; the occasion picker is one collapsed line beneath it | Occasion picker first, with evening occasions suggested | Occasion picker with a two-tap opener: who it is for, then the occasion | No feature band; plain grid |
| Occasion shortlist | Ranked strongly by her taste | Ranked mainly by fit to the occasion | Ranked by fit to the occasion only, with the widest variety | Not shown |
| Complete the Look | Slots filled in her colours and aesthetic | Slots filled to suit the occasion and the hero piece | Slots filled with safe matches; adapts as he taps and swaps | One compact rail below delivery and returns |
| Reason chips | "Matches your minimalist picks" | "Right for an evening event" | "Goes with the dress you chose" | None |

The same occasion and the same product must give visibly different results for Layla and for James. That contrast is the personalisation demo.

James starts generic and changes within the session. After three or four taps or swaps, his shortlist and looks should have shifted enough to notice.

## 4. Site frame

Every page shares three elements: the store header, the persona switcher, and a behind-the-scenes panel. The last two are demo tools and must look clearly separate from the store.

**Store header**

- The Richie's wordmark, centred
- Department links for the departments that exist in the catalogue, plus "Shop all"
- A bag icon with a count; adding to bag updates the count and shows a brief confirmation, with no bag page behind it
- No search box. Search is out of scope, and its absence should not look like a gap

**Persona switcher**

- A slim bar above the header reading "Shopping as: Layla", with a dropdown listing the four personas and the signals behind each (see "What each dropdown entry shows" below)
- Switching persona starts a fresh session for that persona and reloads the current page
- A "Reset session" link clears what the session has learned and returns the persona to their starting state
- The bar uses a dark background and a monospace label so nobody mistakes it for store navigation

**What each dropdown entry shows (added 14 Nov)**

Each persona in the dropdown shows why the store treats them as low or high intent, so the review panel can see the reasoning before a persona is chosen. No age is shown anywhere in the store or the panel.

An entry has four parts, top to bottom:

1. **Name and type**, for example "Layla · the Wanderer"
2. **Intent badge**: "Low intent" or "High intent"
3. **Signals**: three or four short lines, each one an observed behaviour
4. **What the store does**: one line naming the lead module

| Persona | Intent | Signals | What the store does |
| --- | --- | --- | --- |
| Layla · the Wanderer | Low | Returning customer with a long history · Opened the app directly · No search or filter · Opened two products and closed each within seconds | Leads with Looks for you |
| Noor · the Occasion-haver | Low | Arrived from an evening-edit notification · One past order · No search or filter · Browsing evening dresses without narrowing | Leads with the occasion picker, evening suggested |
| James · the Newcomer | Low | First visit, no history · Arrived from a general web search · One page viewed · No high-intent signal yet | Leads with the occasion picker and "Who is it for?" |
| Sara · the Hunter | High | Arrived on a product page from a search for a specific bag · Selected a size · Past orders in bags | Feature stays out of the way |

- The signals are the persona's **starting** session. A small caption under the list reads "Simulated starting sessions". What happens after that, live, is shown in the behind-the-scenes panel.
- The intent badge uses the bronze accent for low intent and a neutral outline for high intent.
- The collapsed bar shows the name and badge only: "Shopping as: Layla · Low intent".
- Signal lines are plain statements of behaviour, with no scores or technical terms.

**Behind-the-scenes panel**

A drawer that opens from the right edge, closed by default, opened with a small tab labelled "Behind the scenes". It shows:

- The intent state, low or high, and the rule that set it
- The persona's taste profile: the top values per tag with a simple bar each, for aesthetic, colour, formality and coverage
- The last five events and how each one moved the profile, for example "Swapped out gold earrings: statement aesthetic down"
- On a product page, the product's full set of tags
- A "Sample" marker on any product that was loaded as sample data and did not go through the tagger

The panel is how the review panel sees why two personas get different results. It should be readable at screen-share size.

## 5. Product listing page

The PLP is where a low-intent shopper meets the feature. From top to bottom it has a feature band, a filter bar and the product grid.

**Feature band (low intent only)**

- **Occasion picker.** A serif heading, "Where are you going?", with one chip per occasion. Only occasions with enough matching products are shown. This is the element in the bronze accent.
- **Looks for you.** Above the picker, one compact row of look cards, shown only once the shopper has a taste profile (see "Looks for you: where and when" below). Each card shows a hero piece with two or three matching items and a title built from the tags, such as "Minimalist evening". Tapping a card opens the hero piece's product page with its look.
- **Opener (James).** Before the occasion chips, one question: "Who is it for?" with three choices, for me, for her, for him. The answer sets the department.
- **Own words (optional).** Under the chips, a quiet text link, "Or describe it in your own words", which opens a single-line input. Build this last; the chips are the main path.

For Sara the band is absent and the page starts at the filter bar.

**One lead module (added 14 Nov)**

The top of the PLP shows one lead module, never Looks for you and the full occasion picker stacked together. The rule is: show when the store knows, ask when it does not.

| What the store knows | Lead module | Persona at the start |
| --- | --- | --- |
| Their taste, but no occasion | Looks for you | Layla |
| An occasion, but little taste | Occasion picker, with that occasion suggested first | Noor |
| Nothing | Occasion picker with the "Who is it for?" opener | James |
| That they know what they want | None | Sara |

- **When Looks for you leads,** the occasion picker collapses to a single quiet text line under the row: "Shopping for an occasion?" Tapping it opens the occasion chips in place, and choosing one shows the shortlist as usual.
- **When the occasion picker leads,** there is no Looks for you row at the top. Look cards can still appear inside the grid for an eligible shopper.
- **The lead changes as the store learns.** Once James has tapped enough to have a taste profile, his next page load leads with Looks for you and collapses the picker, the same as Layla.
- **The first product row must be visible without much scrolling.** On mobile the lead module takes no more than about half the first screen.

This supersedes any earlier line in this document that shows the two modules together.

**Filter bar**

- One row of filter buttons opening a drawer: category, colour, occasion, formality, season, aesthetic, sleeve length and length
- Filter values come from the tags in the database; a value with no products is not listed
- Applied filters show as removable chips under the bar
- Using a filter is a high-intent signal, so the feature band collapses on the next page load

**Grid**

- Two columns on mobile, four on desktop, with generous gutters
- Product cards as described in the brand section
- A heart on each card saves the product; saving is recorded as a signal and there is no wishlist page
- For low-intent personas the default order is by fit to their taste, labelled "Selected for you"; for Sara and for James at the start it is newest first

**Looks for you: where and when**

Looks for you appears when the shopper has given enough signal to build a look for them, not for a fixed list of personas. It has three placements.

| Placement | What it looks like | Priority |
| --- | --- | --- |
| Inside the PLP grid | One look card after every eighth product, the width of two grid cells and the height of one row. It shows the hero piece large with two or three matching items small beside it, a serif title, and the label "A look for you". At most three per page. | Build first |
| Top of the PLP | One compact row above the occasion picker, kept short so the picker stays in view without scrolling | Already built; shorten |
| Bottom of the product page | A row titled "More looks for you", under Complete the Look and the description. Never includes the product on the page. | Only if time allows |

Tapping a look card anywhere opens the hero piece's product page with its look.

**When each persona sees it**

| Persona | Behaviour |
| --- | --- |
| Layla | From the first page, built from her taste |
| Noor | From the first page, built for the occasion she arrived with, because her history is thin |
| James | Not at first. After three or four taps or swaps, the first look card appears in his grid on the next page load |
| Sara | Never |

James's first look card is a demo moment: the store goes from knowing nothing about him to offering a look for him within a minute. The behind-the-scenes panel should show the point at which he became eligible and why.

A look card in the grid must not feel like an advert. It uses the same ivory background and type as the product cards, with a hairline border to set it apart.

## 6. Occasion assistant

The occasion assistant turns one tap into a short, explained selection of hero pieces. It runs in place on the PLP; the shopper never leaves the page until they choose a product.

**Flow**

1. The shopper taps an occasion chip (James answers "Who is it for?" first).
2. The feature band expands into a shortlist section. The grid stays below it.
3. The section title names the occasion in serif type, for example "For an evening event", with a "Change" link that returns to the chips.
4. Up to eight hero pieces appear as large cards, bigger than grid cards, two per row on mobile.
5. Each card carries up to three reason chips.
6. Tapping a card opens its product page with the occasion carried over.

**Hero piece card**

- Large image, brand, name and price
- Reason chips under the price, in small outlined pills. Each chip is a tag that earned the item its place, written for a customer: "Evening", "Formal", "Your kind of minimalist"
- A chip about the shopper's taste appears only when the persona has a profile; James sees none at first
- No more than three chips, with the occasion reason first

**Refine row**

A single row of optional chips under the title lets the shopper narrow without knowing filter vocabulary: a budget band, a colour family, and "More covered". Tapping one re-ranks the shortlist in place. These are refinements, so they do not count as high-intent filter use.

**Variety**

The shortlist should never look like eight versions of one item. No more than two pieces from the same brand or in the same colour.

**Carrying the occasion**

On the product page a slim line above the title reads "Styling for: evening event", with a link back to the shortlist. Complete the Look then builds for that occasion.

## 7. Product page and Complete the Look

The product page is where every demo ends up, including the product the review panel picks live. Complete the Look must work for any approved product with an outfit role.

**Product page layout**

- Image gallery, full width on mobile, swipeable
- Brand in small uppercase, product name in serif, price
- Size selector and an "Add to bag" button
- Description and details in collapsed sections
- "About this piece": the product's tags as chips. Tapping a chip opens the PLP filtered to that tag

**Complete the Look module**

- Title in serif: "Complete the look"
- The product on the page is the anchor and is not repeated in the module
- One card per slot. A dress gets shoes, a bag and jewellery; a top also gets a bottom. Three slots is the norm, four the maximum
- Each slot card shows the slot name in small uppercase ("Shoes"), the image, brand, name, price, one reason chip, and a "Swap" control
- Cards sit in a horizontal row that scrolls on mobile and fits on one line on desktop
- Under the row: the total price of the look and an "Add look to bag" button

**Swap**

Swap is the signature interaction and should feel considered.

- "Swap" sits at the top-right of each slot card as a text control with a small icon
- Tapping it cross-fades the card to the next best match for that slot; the rest of the look stays still
- A quiet counter under the card shows the position, "2 of 5", with a back arrow to return to the previous option
- The look total updates with each swap
- At the last option the control reads "No more options" and is disabled
- Each swap teaches the store about the shopper's taste. That learning is shown in the behind-the-scenes panel, not on the page

**Placement by intent**

|  | Low intent | High intent (Sara) |
| --- | --- | --- |
| Position | Directly under "Add to bag", above the description | Below delivery and returns |
| Size | Full module with reason chips, swap and look total | One compact rail of small cards titled "Pairs well with" |
| "About this piece" chips | Shown and tappable | Hidden |

## 8. States

The catalogue is small, so thin results are the most likely thing to go wrong in the demo. Each case below has a designed answer.

| Situation | What the shopper sees |
| --- | --- |
| An occasion has fewer than four matching products | Its chip is not shown |
| A shortlist has fewer than eight items | Show what there is; no padding with weaker matches |
| A slot has no good match | The slot is hidden |
| Only one slot can be filled | The module title becomes "Pairs well with" and the look total is hidden |
| No slot can be filled, or the product has no outfit role (fragrance, homeware) | The module is not shown |
| A slot has a single option | The swap control is hidden for that slot |
| The last swap option is reached | The control reads "No more options" and is disabled |
| A product is out of stock | It is never suggested in a shortlist or a look |
| Content is loading | Ivory placeholder blocks in the final layout; no spinners |
| A refinement leaves no results | A single line, "Nothing matches that yet", with a link to clear the refinement |
| A newly approved product arrives from the tagger | It appears on the PLP on the next load and has a look on its product page |
| The shopper becomes high intent mid-session | The current page does not change; the next page loads in the high-intent layout |
| The persona is switched | The session resets and the current page reloads for the new persona |

## 9. Sample products and data

Some products and all customer histories will be loaded as sample data without going through the tagger. To a shopper they look identical to tagged products; to the review panel they must be clearly marked.

- **In the store:** a sample product has the same card, the same product page and the same behaviour as any other product. It needs at least one good image, a brand, a name and a price.
- **In the behind-the-scenes panel:** a sample product carries a "Sample" marker next to its tags, and its tags show no confidence figures.
- **Preferred source:** real products with real photographs, tagged by hand, so the store never shows an invented item with a placeholder image. Invented products are a last resort for a slot that nothing real can fill.
- **Persona histories:** the views, saves and purchases behind each persona are sample data. The panel states this in one line under the taste profile: "Starting history is simulated".

How sample data is loaded and kept out of the tagger's accuracy figures is in the engineering PRD.

## 10. Out of scope and acceptance

**Out of scope**

- Home page, search, bag page, checkout, account and wishlist page
- A chat-style shopping assistant
- The graded intent score with a middle state; the build uses low and high only
- Arabic and right-to-left layout
- Gifting by recipient

**Acceptance checklist**

- [ ] The store is named Richie's and follows the brand section: ivory background, serif titles, square corners, one accent
- [ ] The persona switcher lists Layla, Noor, James and Sara, and switching reloads the page for that persona
- [ ] Layla sees "Looks for you" as the lead module with the picker collapsed to one line; Noor sees the picker first; James sees the opener; Sara sees neither
- [ ] Choosing an occasion shows up to eight hero pieces, each with reason chips, without leaving the PLP
- [ ] The same occasion gives a visibly different shortlist for Layla and for James
- [ ] A product page shows Complete the Look with one card per slot, a look total and "Add look to bag"
- [ ] Swap changes one slot in place, shows its position, can step back, and updates the total
- [ ] After a few taps and swaps as James, his results change, and the behind-the-scenes panel shows why
- [ ] Using a filter or selecting a size moves the next page to the high-intent layout
- [ ] Empty and thin states behave as in section 8
- [ ] Sample products are indistinguishable in the store and marked in the behind-the-scenes panel
- [ ] Every screen works at mobile width and at desktop width

* [ ] Looks for you appears as a card after every eighth product in the grid for an eligible low-intent shopper, and never for Sara
* [ ] James sees no look cards at first, and sees his first one after three or four taps or swaps
* [ ] The behind-the-scenes panel shows whether the shopper is eligible for Looks for you and why

- [ ] The top of the PLP never shows Looks for you and the full occasion picker together; Layla's picker is one collapsed line that opens in place
- [ ] After James builds a taste profile, his next PLP load leads with Looks for you

* [ ] Each persona in the dropdown shows its intent badge, its starting signals and what the store does; no age appears anywhere
