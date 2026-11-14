# How quality is measured

## The golden set

The golden set is 95 products whose category and facets a person decided. Claude Opus 5.5 drafted answers to speed up the labelling, and a person reviewed them. The answer key is `golden/golden_master.xlsx`.

The set is split in two:

- **Tuned set, 76 products.** These were used while writing and adjusting the skill files, rubrics and confidence levels. Figures on them are flattering by design, because the instructions were shaped to get them right.
- **Holdout, 19 products.** These were set aside and never looked at while tuning. Figures on them are the fairer estimate of how the tagger does on new products. 19 is a small number, so a single product can move a figure by several points.

`tagger evaluate --run ID` scores a tag run, and the categorise run before it, against both sets and prints them separately. The Metrics tab in the tagger app shows the same figures next to live ones.

## What each figure means

**Category accuracy.** The share of products placed in exactly the golden category. "Match or runner-up" also counts the tagger's second choice.

**Tag figures** count one answer per product and facet. They cover only products whose category was right, because a wrong category brings the wrong facets. An answer can be a value, "unknown" (the page does not say) or "not applicable".

| Figure | Question it answers | Counted as |
| --- | --- | --- |
| **Precision** | When the tagger gives a value, is it right? | Correct values ÷ values the tagger gave |
| **Recall** | Of the values the golden set has, how many did the tagger find? | Correct values ÷ values in the golden set |
| **Coverage** | How many golden values got an answer the tagger was confident in? | Golden values with an accepted answer ÷ values in the golden set |
| **Correctly left unknown** | When the page does not say, does the tagger say "unknown" instead of guessing? | Agreed unknowns ÷ golden unknowns |
| **Review rate** | How much goes to a person? | Answers in review ÷ all answers |
| **Auto-accepted** | How much needs no person at all? | Answers at or above their facet's confidence level ÷ all answers (Metrics tab) |

An answer is **accepted** when its confidence is at or above its facet's confidence level (0.80 unless the facet has its own). Below that it goes to **review**, and a person approves or corrects it in the tagger app.

Precision matters most for a store: a wrong tag puts a product in front of the wrong shopper. That is why answers the tagger is unsure of go to a person rather than being guessed.

## Results

Tag run 1003 (Claude Sonnet 5.5) and categorise run 1002 (Claude Haiku 5.5):

| | Tuned set | Holdout |
| --- | --- | --- |
| Category accuracy, exact | 88.2% (76 products) | 78.9% (19 products) |
| Category, match or runner-up | 97.4% | 94.7% |
| Tag precision | 95.8% | 91.3% |
| Tag recall | 84.3% | 80.6% |
| Tag coverage | 87.3% | 88.3% |
| Correctly left unknown | 91.3% | 76.6% |
| Review rate | 9.3% | 9.4% |
| Auto-accepted (Metrics tab) | 84.4% | 76.7% |
| Products scored for tags | 65 | 15 |
| Cost per product | $0.043 tag + $0.001 categorise | |

## Where the scorecards are

- `reports/facet_scorecard_run1003.xlsx`: every facet's figures for run 1003.
- `reports/facet_scorecard_run843.xlsx`: the same for run 843, an earlier run on the tuned set, for comparison.
- `golden/golden_master.xlsx`: the answer key.
- `golden/holdout_19.xlsx`: the holdout products as reviewed.

## Calibration

A confidence score is only useful if it means what it says. `tagger evaluate` also prints how often answers in each confidence band were right. In run 1003, answers scored 0.9 to 1.0 were right almost every time. Answers scored 0.7 to 0.8 were right about four times in five. The default confidence level is 0.80. The Metrics tab plots the same bands.
