# Collision-driven dimming planning estimates

Updated November 8, 2026. The user approved an AI-driven planning estimate explicitly labeled
as an unvalidated rule of thumb. This supersedes the earlier decision to omit website dimming.

## Calculation

The existing collision AI remains unchanged. The new layer, `src/recommendations/dimming_estimate.py`,
uses its expected collision count `E` for the building-night:

```text
suggested_dimming_pct = 100 × E / (E + 0.25)
```

The denominator's `0.25` is an explicit **product policy anchor**, not a fitted coefficient or a
biologically safe collision threshold. It makes 0.25 predicted collisions correspond to a 50%
suggested cut. `HALF_DIMMING_EXPECTED_COUNT` controls it; changes should increment `POLICY_VERSION`
and update the policy examples and tests. This version is `collision-planning-v1`.

| AI expected collisions | Suggested cut in nonessential light output |
| --- | --- |
| 0 | 0% |
| 0.01 | 3.8% |
| 0.10 | 28.6% |
| 0.25 | 50% |
| 0.50 | 66.7% |
| 1.00 | 80% |

The percentage increases continuously with the AI estimate and stays between 0 and 100. It does
not depend on other buildings in the selected area, so changing area selection changes only rank
and color. Missing, negative, or non-finite estimates produce no suggested percentage. A zero
estimate maps to zero under this policy; it does not establish that lighting has no wildlife impact.

This percentage refers to a proposed reduction in **nonessential light output**, not a hardware
dimmer's control setting and not total building lighting. Required and emergency lighting are
excluded. No lighting controls are operated. Historical replay produces a hypothetical planning
suggestion from that replay's collision estimate, not an issued forecast for that past night.

## Map distribution

Color ranks the full-precision suggestions within the selected area/night. Because the percentage
is a monotonic transform of predicted collisions, the shared ranking utility can use the original
collision estimates without changing their order:

- Red: up to `ceil(5% × number_scored)` buildings with the largest positive estimates.
- Amber: the next group, up to `ceil(25% × number_scored)` red and amber combined.
- Pale: the remainder and all zero estimates.
- Unavailable: missing estimates, excluded from ranking.

Tied estimates stay together. A tied group that would cross a boundary goes entirely into the
lower band instead of expanding the red population. A uniform positive set larger than one has
no red group; a single positive building is red. Small selections use rounded-up slots, so one
red building out of ten is possible. With 100 distinct positive estimates the distribution is
exactly 5 red, 20 amber, and 75 pale. Ties can reduce the red share. Display rounding to one decimal
does not affect rank.

These are relative colors, not absolute dimming thresholds. A quiet night's top-ranked building
may have a small suggested percentage. The number in building details is the planning estimate;
the color highlights which suggestions are largest within that selection.

## Why natural and artificial light are not added together

The forecast AI uses migration, weather, seasonal/date features, roof height, and facade exposure.
It has no learned response to changes in artificial lighting. The available sources do not
provide interchangeable lighting quantities:

| Input | Current meaning | Missing for a physical dimming calculation |
| --- | --- | --- |
| USNO astronomy | Moon illumination fraction, horizon time, dusk/dawn; derived potential moonlit hours | Measured or validated facade/flight-path illuminance in compatible units |
| Optional VIIRS samples | Quality-checked neighborhood radiance, not an individual building's controllable output | Per-building contribution, fixture geometry/spectrum, output-to-exposure relationship |
| Optional operator budget | Scheduled and fixed output plus a chosen output cap | A validated bird-specific target and measured response to dimming |
| Collision forecast | Expected survey collisions for a building-night | Counterfactual estimates under particular lighting settings |

NASA describes VNP46A2 as a moonlight- and atmosphere-corrected nighttime-lights product on a roughly
500 m grid. Its radiance is in nW/cm²/sr, not building-level lux or lumens. It cannot simply be added
to moon illumination fraction or subtracted from a lumen budget.
[NASA product description](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2)
and [collection 2 field units](https://ladsweb.modaps.eosdis.nasa.gov/filespec/VIIRS/2/VNP46A2_v2.0.fs).

The reviewed guidance recommends minimizing unnecessary nighttime lighting, including dimming and
limiting spill. It does not supply a numerical ideal light target for this application.
[U.S. Fish & Wildlife Service](https://www.fws.gov/story/threats-birds-collisions-nighttime-lighting).
The Chicago collision study supports a relationship with lighted window area at the studied site;
it does not train this NYC model to predict the effect of a specific dimming percentage.
[Van Doren et al., 2021](https://pubmed.ncbi.nlm.nih.gov/34099553/).

Consequently, this implementation does **not** invent a bird-optimal brightness, blend incompatible
light units, or claim a percentage of collisions prevented. Natural/artificial-light fields remain
available to the data pipeline and can support future work once compatible measurements and
lighting-response evidence exist. The user-facing estimate is explicitly a collision-driven
planning rule of thumb.

## Website and data contract

The website shows suggested dimming in place of the previous collision-priority badges, counts,
and map legend. Details still show the underlying AI collision estimate and its main factors.
The summary shows average and largest suggested cuts plus the number in the top color band.
The orange model/coverage explanation above the map was removed; actual missing-data notices
remain in neutral text. A collapsed explanation in building details identifies the planning rule.

New row, decision, and building API fields:

- `suggested_dimming_pct`: full-precision planning percentage or null.
- `dimming_band`, `dimming_rank`, `dimming_scored_buildings`: shared area/night ranking.
- `dimming_basis`, `dimming_policy_version`: provenance for the planning rule.

The old `risk_priority` and `priority_*` fields remain for compatibility and share the same sparse
ranking. They are not a separate website scale. The CLI's `reduction_pct` and operator-budget
calculator remain separate: an operator-approved budget action is not interchangeable with
`suggested_dimming_pct`.

## Verification

Run `python -m unittest discover -s tests -v`. Tests cover policy anchors and bounds, monotonicity,
area-independent percentages, missing inputs, incompatible light proxies, 5/20/75 colors,
ties that cannot inflate the red group, and matching building/map/summary values. Existing
forecast model tests verify the AI continues to respond to migration and building traits.
Dashboard rendering checks verify dimming values, unavailable states, and removal of the
model explanation banner.
