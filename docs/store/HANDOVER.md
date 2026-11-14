# Richie's store: hand-over note

14 Nov 2026 · branch `store` (not merged into `main`)

## Start it

```bash
uv run richies setup                      # migrations, rule tables, persona histories (safe to rerun)
cd store/web && npm install && npm run build && cd ../..
uv run richies serve --with-tagger        # store http://127.0.0.1:8780 · tagger app http://127.0.0.1:8765
```

## What was built (engineering PRD build order)

1. Schema (migrations 023, 024): `source` and `in_stock` on approved products, store-sample marker on runs,
   seven `store_*` tables. Store samples are invisible to every tagger list, figure, queue and monitor.
2. Store samples: Live tag's third mode "Store sample (listing)" scrapes, tags and publishes listing products for
   the store only, without review. Persona histories from `store/sample_data/personas.json`. Coverage report.
3. Engine (`store/richies/engine.py`): filter, score, diversify and the shared signals; deterministic.
4. Store shell: persona bar, header, product grid with filters, product page, sessions, event log.
5. Complete the Look with swap, back, total and "Add look to bag"; one rail for high intent.
6. Taste profile from events (session events count double), the behind-the-scenes panel with bars and effects.
7. Occasion assistant: occasion chips (at least four eligible pieces), shortlist of up to eight with reasons,
   refinements (budget, neutrals, more covered), the "Who is it for?" opener.
8. Intent rule (search referral, size, PLP filter; refinements do not count), applied from the next page.
9. Looks for you (shoppers with a real history).
10. Own words: NOT built. It needs a paid model call per use; the chips are the main path.

### Design PRD V3 and engineering PRD V5 (14 Nov, later the same day)

- **One lead module** at the top of the PLP, decided by the server at page load (`store/richies/lead.py`): high intent
  → none; an occasion in play (chosen, or arrived with) → occasion picker with it suggested first; a taste profile
  (3 positive events, the `looks_threshold` setting in `score_weights.json`) → Looks for you, with the picker
  collapsed to "Shopping for an occasion? ↓"; otherwise the picker, with "Who is it for?" when no department is known.
- **Looks for you eligibility** replaces the old "five events" rule: low intent and (3 positive events or an
  occasion in play). Occasion in play → looks built for that occasion (Noor: evening); otherwise taste-led.
  `GET /store/looks-for-you?count=6&exclude_product_id=` returns `eligible`, the reason and distinct heroes:
  top row 1–3, grid cards (after every 8th product) the rest, "More looks for you" on the product page.
- **The moment James becomes eligible** is logged (`looks_eligible` event) and shown in behind the scenes, with
  the state (ELIGIBLE / NOT YET / NEVER), the count against the threshold, a pending note and the lead module.
- **Persona switcher**: name · archetype, intent badge, 3–4 starting signals, "→ what the store does". No ages.
  The badge and the line are computed by running the intent and lead rules over each persona's starting events;
  each signal names a check (`store/tests/test_lead.py`) so the text cannot drift from the data.
  Each persona's arrival is an `entry` event in the simulated starting session; Sara's history is now 3 bag purchases.
- **Swap** is the quiet row from README V3 (hairline above, stone "←" and "Swap ⇄", no box).
- **Events** `look_card_view` (logged when a card is at least half on screen) and `look_card_tap`, with the
  placement; `richies funnel` shows them per placement. Card views are kept out of the panel's last five events.
- Migration 025 (persona fields, event types). Docs: `PRD.md` (V3), `design_handoff_README.md` (V3),
  `ENGINEERING_PRD.md` (V5) and the V3 prototype `Richies Store.dc.html`.

Commands (rerun `richies setup` after pulling: migration 025 and the new persona fields): `richies setup | seed-rules | draft-personas | load-personas | coverage | funnel | reset-events | serve`.
Tests: `uv run pytest store/tests` (store) and `uv run pytest` (everything).

## Decisions made along the way (owner)

Git on `main` + branch `store`; samples from Live tag (unsure categories and empty required facets accepted);
database vocabulary (Noor's engagement = special occasion / wedding guest); persona histories from a file only;
proceed without approvals and list the owner's to-dos at the end.

Choices made in the build, easy to change: photos shown whole in 3:4 frames (they are 2:3); suggested occasion
chips are the occasion in play first, then the most recent order's occasion-specific values; the shortlist's URL
key is `?shortlist=` (because `?occasion=` is the occasion filter); look card views count when half the card is on
screen; an answered "Who is it for?" counts as a known department for the lead rule.

## Known gaps

- Men's catalogue is thin: only Everyday and Work have four or more men's hero pieces, so men see two occasion
  chips and almost no evening looks. Women's evening looks have few jewellery pieces.
- No sample products yet, so the "Sample" marker in behind the scenes has not been seen on screen.
- There is no stock data: everything is in stock, so the out-of-stock rule is untested on real data.
- Own words (step 10) not built.

## Design README V7 and engineering PRD V7 (14 Nov)

- **Product-page occasion row.** `GET /store/products/{id}/look` also returns `occasionInPlay` and `occasionOptions`: the product's own occasion tags that are offered on its department's listing page and fill at least one slot, with the session's occasion first, at most four. There are no options for high intent or for a product with no outfit role. Choosing one records `occasion_select` with `surface: product_page` and rebuilds the look in place. The old standalone "Styling for" line above the product name is gone.
- **A chosen occasion carries on.** The lead now says where the occasion came from (`occasionSource`: chosen or entry). A chosen one reopens its shortlist on the next listing page, once per load; an arrival one (Noor) is only suggested.
- **Layla's band.** When Looks for you leads for a shopper whose taste comes from their history, the row stands alone (`occasionToggle: false`). James, whose taste was built in the session, keeps the "Shopping for an occasion? ↓" line.
- **Swap** is the 28px overlay on the photo's top-right corner from README V7.
- **Funnel** adds `occasion_select` by surface (listing page against product page).
