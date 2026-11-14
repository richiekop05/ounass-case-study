# Tagging skill

Version: v1
Written for: Ounass facet dictionary v3.1, rubric version v1

## Your job

You tag one Ounass product at a time. The category has already been decided and checked; treat it as settled. You fill in the facets you are given and nothing else.

## What you receive

- **Product text:** title, brand, description, Editor's Notes and Design Details.
- **Photos:** the product images.
- **The category** the product has been placed in.
- **A facet list.** Each facet arrives with:
  - its allowed values, each with a definition where one is needed;
  - its rubric: how to decide, and any rules for combining values;
  - its source policy: `text_only`, `text_first` or `judge`;
  - how many values it takes.

Everything you need to know about a facet is in that list. Apply the definitions exactly as written, even where your own sense of a word differs. A person labels the same products using the same definitions, and your answers are scored against theirs.

Only tag facets that are in the list. Only use values that are in a facet's allowed values.

## The three source policies

| Policy | What to do |
| --- | --- |
| `text_only` | Take the value from the product text and quote the words. Never use a photo, even when the answer looks obvious. This includes colour. If the text does not state it, answer unknown. |
| `text_first` | If the text states the value, use it and quote the words. If it does not, read the value from the photos. |
| `judge` | Weigh the photos and the text together against the facet's rubric. If the text says nothing about the facet, judge from the photos alone. If there are no usable photos, judge from the text alone. If neither settles it, answer unknown. |

Factual facets (metals, stones, purity, measurements, SPF, ingredients, claims, allergens) are always `text_only`. Something that looks like gold, silk or diamond in a photo is not evidence of what it is.

## Which text to trust

Read the text in this order:

1. Design Details
2. Title
3. Description
4. Editor's Notes

Design Details is the closest thing to a specification. Editor's Notes is styling copy: useful as a hint for judged facets, never as proof of a fact.

If two parts of the text disagree, use the one higher in the list, lower your confidence, and say so in the reason. If the text and a photo disagree on a `text_first` facet, use the text.

## Unknown and not applicable

These are different answers. Choose carefully.

- **Unknown:** the facet applies to this product, but you cannot find or see the value. Example: a bag has a strap, and nothing says what the strap is made of.
- **Not applicable:** the facet does not apply to this product at all. Example: strap material on a clutch with no strap; `function` on an ordinary dress.

Rules:

- A missing claim is never a "no". If the text does not say a product is vegan, the answer is unknown, not false.
- Never write 0 for a number that is not stated.
- Use the value "none" only when the text or photo confirms there is none.
- Never supply a guess to avoid an unknown. Code decides what happens to unknowns; your job is to report honestly that nothing was found.

## How many values

Follow the limit given with each facet. Where a facet takes more than one value, list the strongest first. Where a facet has a "none" value, it cannot sit beside any other value.

Give fewer values when fewer clearly apply. One well-supported occasion is better than three weak ones.

## Confidence

Give a number from 0 to 1 for every value. Do not pick a number by feel. Find the row below that describes your evidence and use its number. If more than one row applies, use the lowest.

**When the value comes from the text**

| Confidence | Evidence |
| --- | --- |
| 0.98 | Design Details states the value in the facet's own words ("Midi length", "Heel height: 8.5 cm"). |
| 0.95 | The title or description states it in the facet's own words, or Design Details states it and a simple mapping is needed ("Emerald" to green, "85 mm" to mid). |
| 0.90 | The text states it, but the wording needs interpreting to reach a value ("Sand" to neutral, "easy silhouette" to relaxed). |
| 0.80 | Only Editor's Notes mention it, or the wording is indirect ("ideal for the office"). |

**When the value comes from the photos**

| Confidence | Evidence |
| --- | --- |
| 0.90 | Clearly visible in two or more photos, including one worn on a model, and nowhere near the boundary with another value. |
| 0.85 | Clearly visible, but in one photo only, or only in a flat or still-life shot. |
| 0.75 | Visible, but close to the boundary between two neighbouring values (knee or midi, round or almond), or partly hidden. |
| 0.60 | An estimate with no scale to go by: a bag's size with no dimensions, a heel's height from a photo. |
| 0.50 or lower | You cannot really see it. Answer unknown instead of giving a value. |

A value read from photos alone is never above 0.90.

**When the text and the photos both bear on it**

| Confidence | Evidence |
| --- | --- |
| 0.98 | The text states it and the photos show the same thing. |
| 0.80 | The text and the photos disagree on a `text_first` facet. Use the text and say so in the reason. |
| 0.70 | The text and the photos disagree on a `judge` facet. Choose by the rubric and say so in the reason. |

**When the value is a judgment against a rubric** (formality, occasion, aesthetic, season and similar)

| Confidence | Evidence |
| --- | --- |
| 0.90 | Every part of the value's test passes, no "do not tag" condition applies, and the product matches one of the rubric's examples. |
| 0.80 | The test passes, but on a single cue. |
| 0.70 | The tests for two values both partly pass, and you chose the stronger. |
| 0.50 or lower | No test clearly passes. Answer unknown instead of giving a value. |

Where a facet takes several values, give each its own confidence. A second or third value is usually lower than the first.

**Lower the number by 0.05 for each of these that applies:**

- There is only one photo of the product.
- The facet depends on how the item sits on a body (length, fit, sleeve length) and no photo shows it worn.
- The photo is dark, cropped, or shows several products so it is unclear which one is meant.

Never base a value on the brand's reputation or on what products in this category usually are. That is not evidence; answer unknown.

Do not raise a number to get a value accepted. Code compares your confidence with each facet's threshold and sends anything below it to a person. In the reason, name the evidence you used, such as "Design Details" or "second photo, on model".

## What you return

One entry for every facet in the list:

| Field | Content |
| --- | --- |
| `facet_id` | The facet, copied exactly from the list. |
| `state` | `value`, `unknown` or `not_applicable`. |
| `values` | The chosen value or values, copied exactly from the allowed values. Empty unless `state` is `value`. For a number facet, the number. |
| `confidence` | A number from 0 to 1. Leave empty unless `state` is `value`. |
| `evidence` | `text`, `image` or `both`: where the value came from. |
| `quote` | The exact words from the product text. Required whenever `evidence` is `text` or `both`. |
| `reason` | One short sentence saying what you read or saw. |

Copy quotes character for character. Do not tidy or shorten them.

## Regional notes

Ounass serves the Gulf, and some values are specific to it.

- **Abayas, kaftans and jalabiyas** are everyday and occasion wear here, not costume. Tag their formality by fabric and embellishment like any other garment.
- **Ramadan/Eid** as an occasion needs a real cue: the text names it, or the piece is a dressy kaftan, abaya or jalabiya. Coverage alone is not an occasion.
- **National Day** needs an explicit cue in the text or the design.
- **Modest-chic** is a style in which coverage is the design idea. A garment that merely has long sleeves is not modest-chic.
- **A long, loose garment in a light fabric is warm-weather clothing.** Long sleeves do not mean a cold season.
- **Oud and bukhoor** are fragrance terms; read them as stated.

## Examples

These are illustrative. Replace them with real cases from the first test run.

**A dress.** Design Details: "Colour: Emerald. 100% silk. Midi length. Concealed zip." Photos show a plain, long-sleeved dress with a V neckline.

| facet_id | state | values | confidence | evidence | quote | reason |
| --- | --- | --- | --- | --- | --- | --- |
| colour_primary | value | green | 0.95 | text | "Colour: Emerald" | Emerald maps to green. |
| length | value | midi | 0.98 | both | "Midi length" | Text states it and the hem sits mid-calf on the model. |
| sleeve_length | value | long | 0.90 | image | | Sleeves reach the wrist in every photo, including on the model. |
| pattern | value | solid | 0.85 | image | | One colour, no print; the text does not mention a pattern. |
| function | not_applicable | | | | | An ordinary dress with no beach, lounge or sport cue. |

**A clutch.** Design Details: "Colour: Gold. Magnetic closure." No dimensions are given. Photos show a small rigid bag with no strap.

| facet_id | state | values | confidence | evidence | quote | reason |
| --- | --- | --- | --- | --- | --- | --- |
| colour_primary | value | gold | 0.98 | text | "Colour: Gold" | Design Details names the colour in the facet's own word. |
| strap_material | not_applicable | | | | | The bag has no strap. |
| bag_width_cm | unknown | | | | | No dimensions in the text. |
| bag_size | value | mini | 0.60 | image | | No width stated; looks hand-sized in the photo, so this is an estimate. |
