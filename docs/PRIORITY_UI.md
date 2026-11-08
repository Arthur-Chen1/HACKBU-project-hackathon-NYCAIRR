# Priority and website cleanup

November 8, 2026.

## One priority scale

Previously, the map offered percentile-based “risk relative to tonight” and absolute priority
thresholds of 1.0 / 0.25 expected collisions. A relatively high-ranked building could therefore
have a medium or low badge. High regional migration also did not guarantee a building would cross
those absolute thresholds.

The website now uses **priority** everywhere. High/medium/low remain useful as the three labels of
this single scale. Map colors, nightly counts, detail badges, ranks, and exported priorities are
assigned by `src/recommendations/priority.py`; the browser no longer calculates a second scale.

For each night in the selected area:

1. Exclude missing, negative, and non-finite estimates from ranking.
2. Sort valid expected collision counts descending.
3. Let `share = number of scored buildings with a strictly greater estimate / number scored`.
4. For a positive estimate, assign high when `share < 0.20`, medium when `share < 0.50`, otherwise low.
5. Assign zero estimates low, and missing/invalid estimates unavailable.

Distinct positive estimates produce approximately 20% high, 30% medium, and 50% low. Ties use the
same rank and tier, so actual proportions can differ. The highest positive estimate is always
high, including a single-building selection. If every estimate is the same positive value, all
are tied at high; if all are zero, all are low. Identical outputs from a night-level fallback do
not establish building-specific differences.

This is an explicit **relative attention policy**, not a model recalibration or an absolute risk
threshold. A high-priority building on a quiet night can have a smaller count than a low-priority
building on a busy night. Changing the selected area can change a building's priority without
changing its estimate. The website explains this distinction and continues showing expected
collision counts and regional migration context.

The former no-model demo rules were also removed: a missing model cannot produce a building
priority from migration alone. The historical `risk_priority`, `risk_basis`, `risk_features`, and
`risk_reasons` field names are retained for API/export compatibility; they do not represent another
user-facing scale. `priority_rank`, `priority_scored_buildings`, and `priority_basis` describe the
new assignment. Model predictions and training artifacts are unchanged.

## Dimming decision

The existing off/dim/reduce actions select an operator-approved percentage that meets a configured
light-output budget. They do not use the AI to calculate the reduction's effect on collisions;
priority magnitude does not choose the percentage. Missing operator settings lead to “review.”

The current collision model has no learned response to building dimming settings. Therefore it
cannot justify an effective percentage or predicted collisions avoided. The user explicitly
approved **removing dimming from the website** after this limitation was explained.

Removed from the website: off/dim/reduce totals, the operator-review column, reduction percentages,
lighting schedules and budgets, operator-budget reasons, and the header's lighting recommendation
claim. The nightly table now shows high and medium priority counts. Building details contain only
model estimates, priority, ranking, and model/source explanations. The building API payload omits
lighting action fields. Existing command-line operator-budget tools and their tests remain.

No new dimming policy or percentage mapping was implemented. Reintroducing collision-reduction
recommendations requires suitable lighting-response evidence and a new user decision.

## Verification

`python -m unittest discover -s tests -v` checks the priority distribution, unchanged estimates,
ties, zeros, missing values, small selections, separate nights, matching map/summary outputs,
and removal of lighting fields/reasons from building details. Existing model and command-line
lighting tests remain in the suite. The dashboard script is also checked for JavaScript syntax.
