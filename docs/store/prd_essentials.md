# Richie's PRDs: the parts the build follows (saved 14 Nov 2026)

From the Customer Feature Engineering PRD (rules) and the Customer Feature Design PRD (screens), both by Richard
George, 14 Nov 2026. Owner decisions that change them are in `PLAN.md` (database vocabulary; samples from Live tag).

## Engineering PRD 5: Occasion assistant
Given an occasion, return up to eight hero pieces, ranked, each with the reasons it was chosen.
- **Filter:** approved and in stock; department matches the persona's or the "Who is it for?" answer; outfit role
  in the occasion's `hero_roles`; occasion tag includes the occasion; formality within `formality_min..max`;
  season compatible (`all_season` with everything); refinements: price within the budget band, colour in the
  chosen family, "More covered" = long sleeves, midi or longer, not sheer.
- **Score** (0..1 each, weighted): occasion fit 0.40 (1.0 if it is the product's only or first occasion, else
  0.7); taste match 0.35 (section 7); formality centre 0.15 (1.0 at the midpoint, 0.5 at the edges); tag
  confidence 0.10 (mean of occasion and formality confidence; 1.0 for samples). No profile: drop taste, rescale.
- **Variety:** at most two per brand and two per primary colour; stop at eight.
- **Reasons:** up to three codes in order: the occasion; the strongest other matching tag (formality or season);
  a taste reason if taste > 0.6. The page turns codes into customer wording.
- **Occasions offered:** active occasions with at least four products passing the filter for the department.
- **Looks for you:** for a persona with a profile, the three occasions with the highest mean taste match, the top
  hero of each, a look for it; title from the hero's aesthetic and occasion.
- **Own words (optional, last):** one model call maps free text to an occasion and refinements from the closed
  vocabulary; nothing valid → show the chips.

## Engineering PRD 6: Complete the Look
Given any approved product: one chosen item per slot plus up to four alternates per slot.
1. Role from `store_outfit_roles`; `none` → no look. Slots from `store_look_templates`.
2. Filter per slot: approved, in stock, the slot's role, not the anchor; same department; formality within one
   step of the anchor; season compatible; occasion: the carried occasion if any, else shares an occasion with the
   anchor. A facet that does not apply to a candidate's category: skip that check and signal, rescale.
3. Score: colour harmony 0.30 (`store_colour_pairings`, primary colours); formality closeness 0.20 (1.0 same,
   0.6 one step); occasion overlap 0.15 (shared / anchor's); aesthetic 0.15 (1.0 shared, 0.4 otherwise); taste
   0.15 (dropped without profile); price fit 0.05 (1.0 up to the anchor's price, 0 at three times).
4. Assemble in template order, top candidate each; at most two non-neutral colour families across anchor and
   chosen items, else take the next candidate that keeps the rule.
5. Alternates: the next four per slot, all in the response (swap needs no request). Each swap sends `swap_out`
   (item replaced) and `swap_in` (item shown); stepping back sends the pair in reverse.
6. Reasons: one code per item from its strongest signal (colour, formality, occasion, aesthetic, taste).
- Thin results: only slots with at least one candidate; a count of filled slots ("Complete the look", "Pairs
  well with", or nothing). High intent: the top item per slot only, no alternates, no reasons.

## Engineering PRD 7: Personas, taste profile, intent
- A session belongs to one persona, id in a cookie; switching persona or "Reset session" starts a new one. The
  profile = the persona's seed events + the current session's events.
- **Taste profile:** per facet value for aesthetic, primary colour, formality, sleeve length, length, sheerness,
  brand: add each event's weight to the product's value for each facet; clamp at zero; divide by the facet's total.
  Weights: purchase (seed) +3, add_to_bag +3, save +2, swap_in +1.5, view +1 (product page or shortlist card),
  swap_out -1.5. Session events count double.
- **Taste match:** for each profile facet that applies to the product, the profile weight of its value divided by
  the facet's largest weight; mean across facets. No events → no profile → taste signal dropped.
- **Intent:** high if, in this session, it started on a product page from a search referral (Sara's seed
  session), a size was selected, or a PLP filter was applied (refinement chips do not count); else low. Evaluated
  when a page loads (takes effect on the next page). `intent(session_events) -> state, reason`.

## Engineering PRD 8: Endpoints and events
| Endpoint | Returns |
| --- | --- |
| `GET /store/products` | PLP grid with optional facet filters and department; ordered by taste for a low-intent persona with a profile, else newest first |
| `GET /store/products/{id}` | Product fields and tags |
| `GET /store/facets` | Each filterable facet with the values that have products |
| `GET /store/occasions` | Active occasions with at least four eligible products |
| `POST /store/shortlist` | Up to eight heroes with reason codes; occasion and refinements |
| `GET /store/looks-for-you` | Up to three looks with titles |
| `GET /store/products/{id}/look` | Slots with chosen item, alternates, reason codes; optional occasion |
| `POST /store/events` | Records a shopper action |
| `GET /store/state` | Intent state and reason, taste profile, last five events with their effect |
| `POST /store/session` | Switch persona or reset; new session id |
| `POST /store/parse-occasion` | Optional own-words parse |

Event types: view, save, add_to_bag, add_look_to_bag, size_select, filter_apply, occasion_select, refine_apply,
shortlist_view, look_view, swap_out, swap_in (+ seed: purchase, search_referral). Context JSON: occasion, slot,
position, surface (shortlist | look | grid). A script prints the funnel per persona (sessions that see a
shortlist, tap a hero, view a look, swap, add a look to bag).

## Design PRD 3: Personas
| | Layla, the Wanderer | Noor, the Occasion-haver | James, the Newcomer | Sara, the Hunter |
| --- | --- | --- | --- | --- |
| Who | 34, Dubai; regular, no mission; neutrals, clean lines | 29, Abu Dhabi; engagement in three weeks | 41, from the UK; buying for his wife; first visit | 37, Riyadh; searching for one bag; size selected |
| Top of PLP | "Looks for you", then the occasion picker | Occasion picker first, her occasions suggested | Opener "Who is it for?", then the picker | No feature band; plain grid |
| Shortlist | Ranked strongly by her taste | Mainly by occasion fit | Occasion fit only, widest variety | Not shown |
| Complete the Look | Her colours and aesthetic | Suits the occasion and hero | Safe matches; adapts as he taps and swaps | One compact rail below delivery and returns |
| Reason chips | "Matches your minimalist picks" | "Right for an evening event" | "Goes with the dress you chose" | None |
Same occasion and product must give visibly different results for Layla and James. James shifts after three or
four taps or swaps.

## Design PRD 8: States
| Situation | Shopper sees |
| --- | --- |
| Occasion with fewer than four matching products | Chip not shown |
| Shortlist under eight | What there is, no padding |
| Slot with no good match | Slot hidden |
| Only one slot | Title "Pairs well with", no total |
| No slot, or no outfit role (fragrance, homeware) | Module not shown |
| Slot with a single option | No swap control |
| Last swap option | "No more options", disabled |
| Out of stock | Never suggested |
| Loading | Ivory placeholder blocks in the final layout, no spinners |
| Refinement leaves nothing | "Nothing matches that yet" + link to clear |
| Newly approved product from the tagger | On the PLP on the next load, with a look |
| Becomes high intent mid-session | Current page unchanged; next page high intent |
| Persona switched | Session resets, page reloads |

## Design PRD 2 and 10: brand and acceptance (summary)
Quiet luxury: ivory, serif titles, square corners, one bronze accent, no shadows or gradients, prices in AED plain,
tone short and composed (no exclamation marks, urgency or emoji). Out of scope: home page, search, bag page,
checkout, account, wishlist page, chat assistant, graded intent score, Arabic/RTL, gifting by recipient.
Acceptance: brand; persona switcher with the four personas; Layla sees Looks for you + picker, Noor the picker
first, James the opener, Sara neither; shortlist of up to eight with chips without leaving the PLP; Layla vs James
visibly different; PDP look with total and "Add look to bag"; swap in place with position, back and total; James
changes after a few taps and the panel shows why; a filter or size moves the next page to high intent; section 8
states; samples indistinguishable in the store and marked in the panel; mobile and desktop widths.
