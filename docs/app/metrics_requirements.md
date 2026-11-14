# Tagger app: Metrics tab requirements

Nov 14, 2026 · @Richard George

## Purpose and layout

The Metrics tab answers five questions about the tagger, in this order, on one scrolling page. It is the source for a Design PRD (what the page shows) and a Dev PRD (how each number is calculated).

| Order | Block | Question it answers | Where the data comes from |
| --- | --- | --- | --- |
| 0 | Top strip | How is it doing, in five numbers? | The blocks below |
| 1 | Quality | Is it right? | Golden set evaluation |
| 2 | Review | How much human work does it need? | Reviews done in the app |
| 3 | Cost | What does tagging cost? | Stored run costs, tag step only |
| 4 | Health | Is it still healthy? | Monitoring tables |
| 5 | Business | What does the business get? | Counts on the tagged catalog |

The page reads existing data. It does not call a model and does not change any tag, threshold or rule. Changing thresholds stays on the Confidence tab.

Under the top strip, one line of small print states the run the page describes: tag run number, models used, definitions version, golden set size and the date of the run.

## Top strip

Five tiles, each with one large number, a short label and one supporting line.

| Tile | Large number | Supporting line |
| --- | --- | --- |
| Tag precision | Precision on the holdout products | Precision on the tuned set, and the number of answers scored |
| Auto-accepted | Share of tags accepted without a person | Number of tags accepted out of the total |
| Review rate | Share of tags waiting for a person | Number of products with at least one tag in review |
| Category accuracy | Share of accepted categories that are correct | Number of categories flagged and held |
| Tagging cost per product | Cost of the tag step divided by products tagged | Projection for 3 million products at standard pricing |

Until the holdout products are labelled, the precision tile shows the tuned-set number as the large number, marked "tuned set".

## 1. Is it right?

Every number in this block compares the tagger's answers with the golden set. Only products whose category matched the golden category are scored for tags.

| Metric | How it is calculated | How to show it |
| --- | --- | --- |
| Precision | Accepted tags that match the golden answer ÷ all accepted tags | One number, tuned set and holdout side by side |
| Recall | Accepted tags that match the golden answer ÷ all golden answers that have a value | Same |
| Coverage | Accepted tags ÷ all tag answers | Same |
| Facet scorecard | Per facet: answers, average confidence, precision, recall, coverage, review rate | Sortable table; facets with fewer than 20 golden answers marked "small sample" and greyed |
| Calibration | For each confidence band (below 0.7, 0.7 to 0.8, 0.8 to 0.9, 0.9 to 1.0) and each evidence type (text, photo, both): share of answers that match the golden answer, with the count | Chart of stated confidence against actual accuracy, one line per evidence type, with a diagonal showing perfect calibration |
| Unknown agreement | Tagger answers of "unknown" where the golden answer is also unknown ÷ all tagger "unknown" answers | One number |
| Invented quote rate | Text answers whose evidence quote is not found in the product text ÷ all text answers | One number; lower is better |

Tuned set means the 76 golden products the rules were adjusted on. Holdout means the 19 golden products labelled without seeing the tagger's answers. The two are never added together.

## 2. How much human work?

This block uses what reviewers do in the app. A reviewed tag is a tag that was waiting for review and that a person has approved or changed. The tagger's original answer is always kept, so the two can be compared.

| Metric | How it is calculated | What it is for |
| --- | --- | --- |
| Review rate | Tags waiting for or sent to review ÷ all tag answers | The size of the workload |
| Products needing review | Products with at least one tag or the category in review ÷ products tagged | The workload counted in products |
| Approved without change | Reviewed tags a person approved as the tagger gave them ÷ reviewed tags | A high share means the threshold is too strict and people are checking correct answers |
| Changed on review | Reviewed tags a person changed to a different value, or cleared ÷ reviewed tags | The tagger's real error rate in the uncertain band |
| Accepted tags corrected | Tags the tagger accepted that a person later changed ÷ accepted tags on reviewed products | Errors that got past the threshold |
| Review outcomes per facet | Approved without change and changed, split by facet, with counts | Shows which thresholds to move and in which direction |
| Category overrides | Flagged or held categories a person changed ÷ flagged or held categories reviewed | Whether the category gate is in the right place |
| Product progress | Counts of products: ready, needs review, approved | Progress through the catalog |
| Review time per product | Median time from opening a product to approving it | Converts review rate into hours |

Approved without change and changed on review always add up to 100% of reviewed tags. Show them as one split bar per facet, with the count of reviewed tags beside it.

Before any review has been done in the app, this block shows the review rate and product counts, and an empty state for the outcome metrics.

## 3. What does tagging cost?

This block shows the cost of the tag step only. The categorise step is stored separately and is not included in any number here. Every label says "tagging cost".

| Metric | How it is calculated | How to show it |
| --- | --- | --- |
| Tagging cost per product | Cost of the tag run ÷ products tagged in that run | One number, in cents |
| Tagging cost per run | Stored cost of the tag run | One number, with products tagged and the model used |
| Time per product | Tag run duration ÷ products tagged | One number, in seconds |
| Projection for the full catalog | Tagging cost per product × 3,000,000 | Two numbers: standard pricing, and batch pricing at half the standard rate |
| Model comparison | Tagging cost per product and precision for each model run on the same products | Small table, one row per model; shown only when a comparison run exists |

The catalog size used for the projection is a setting, with 3,000,000 as the default.

## 4. Is it still healthy?

This block shows the monitoring already built for the pipeline (tagging pipeline PRD, section 9). It needs no golden answers, so it works on every new product.

| Metric | How it is calculated | How to show it |
| --- | --- | --- |
| Unknown rate per facet | "Unknown" answers ÷ all answers for the facet, in the latest run, against the stored baseline | Table of the facets that moved most, with baseline, current and the change in points |
| Confidence shift per facet | The population stability index between the baseline and current confidence distributions | Same table, with a status: stable, watch or alert, using the limits set in monitoring |
| Review rate over time | Review rate for each tag run | Line across runs |
| Coverage over time | Coverage for each tag run | Same chart, second line |
| Open alerts | Monitoring alerts not yet closed | List: facet, what moved, by how much, when |

When only one run exists, the two lines show a single point and the table shows "baseline run".

## 

## Rules for every number

- **Retired facets.** Retired facets, such as `shoe_fit_width`, appear nowhere on the page.
- **No answer yet.** A metric with nothing to count shows a dash and a short reason, never 0%.
- **Same definitions as the command line.** The page must give the same numbers as `tagger evaluate` and the facet scorecard for the same run.

## Filters

- **Run:** the latest tag run by default, with a picker for earlier runs.
- **Category:** any level of the category tree, applied to blocks 1, 2 and 5.
- **Facet:** one facet, applied to the scorecard, calibration and review outcomes.

## 
