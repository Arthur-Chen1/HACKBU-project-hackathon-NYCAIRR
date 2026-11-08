# DarkSky collision model

## What was trained

The saved **historical observed model** is [observed.json](data/processed/models/observed.json). It contains four fitted Poisson regressions (profiles):

| Profile | Rows | Sites | What it adds |
| --- | ---: | ---: | --- |
| `base` | 5,422 | 27 | Season, migration, and LaGuardia weather. Covers 560 monitored nights from March 31, 2017, through November 14, 2021. |
| `natural_light` | 1,190 | 20 | Night length and potential moonlit hours, fall 2021 only (76 nights). |
| `footprint` | 5,202 | 21 | Log roof height and log footprint area of the matched building, with facade length as exposure. This is the per-building fit. |
| `natural_light+footprint` | 1,030 | 17 | Both enrichments, fall 2021 only. |

The artifact is local because Git ignores `data/processed/`. Each teammate rebuilds it with the commands in [Reproduce or run it](#reproduce-or-run-it). The artifact uses feature version 3. The scorer rejects older files, so retrain after pulling this change.

Rebuilding from the commands below reproduces the original base and natural-light coefficients and evaluation exactly, because the Dryad CSV is checksum-pinned.

**Building-level fit.** The Dryad data identifies buildings only by letter codes. [configs/study_building_bins.csv](configs/study_building_bins.csv) maps 21 of the 27 study sites to NYC BINs. The addresses come from Table S2 of the study supplement. Each was geocoded with NYC GeoSearch and checked against the footprint's height and name. [configs/README.md](configs/README.md) lists the six excluded sites and the reasons. The study sites differ a lot: site `y` averages 3.16 collisions per surveyed night, site `a` averages 0.05, and the top five sites account for 42% of all collisions. With `--sites`, the footprint profiles learn how roof height and footprint area relate to those differences. Results are under [Evaluation in the saved artifact](#evaluation-in-the-saved-artifact).

**Facade exposure.** A survey counts birds along the facades the volunteers walked, and Table S2 records that distance for each site, from 32 m (site `u`) to 1,044 m (`aa`). The footprint profiles therefore model collisions per 100 m of facade:

```text
expected collisions = (facade length / 100 m) × exp(intercept + Σ coefficient × standardized feature)
```

Training uses the surveyed distance (`surveyed_facade_m`, from the crosswalk's `survey_distance_m`). Scoring uses the candidate building's footprint perimeter (`footprint_perimeter_m`). A building without a perimeter falls back to a profile without building traits. Before this term, a small building received about the same baseline as the study towers, which had long surveyed facades. For example, the median 25–35 m building in the September 10, 2021 Lower Manhattan replay has an 87 m perimeter, and its estimate fell from 1.15 to 0.81 on that heavy night. On ordinary nights such buildings score about 0.1–0.2. The exposure term also improved every holdout metric below.

**There is no trained pre-sunset forecast artifact in this workspace.** The forecast training and scoring code is implemented. With suitable daily inputs, it can currently produce a demo priority while the numeric forecast remains blank.

## The unit of data

- **Training key** is `study_building_id` and `night_date`. `night_date` is the NYC date on which the night begins. A survey the next morning supplies `observed_collision_count` for that night. A label of `0` is a completed survey with zero birds found. A blank label is skipped.
- **Scoring key** is `bin` and `night_date`. BIN is the NYC Building Identification Number. BBL identifies the tax lot and can be shared by buildings. A verified crosswalk is needed to attach a study site's outcomes to a real BIN for footprint-based training.
- The ingestion step collapses repeated surveys with matching counts to one row and excludes conflicting counts from the labeled export. The baseline excluded 21 conflicting building-nights. The trainer rejects duplicate keys within an input and conflicting values when it joins augment files. The fall replay adds columns to existing study rows.
- The label describes **birds found by a comparable morning survey**. The predictor list excludes the count, `survey_records`, study ID, BIN, BBL, and post-survey metadata. Missing numeric features stay missing, and valid zero values are preserved.

## Where the inputs come from

| Data | Project input | How it is used |
| --- | --- | --- |
| NYC collision surveys, observed KOKX migration traffic, and observed LaGuardia weather | `data/processed/baseline.csv`, from Dryad version `272593`, file `collisions_migration_weather_spring_fall_dataset.csv` | Supplies the training label, season, migration, visibility, cloud ceiling, both wind components, and temperature for the observed base fit. [Dryad study dataset](https://doi.org/10.5061/dryad.41ns1rnmw) |
| Sun and Moon | `data/processed/replays/fall-2021/training.csv` adds cached USNO data to the training rows. The scoring pipeline requests the same astronomy data for each NYC night. | Supplies sunset, sunrise, civil dusk, civil dawn, Moon illumination fraction, and minutes above the horizon between sunset and sunrise. The implementation samples at a shared NYC reference point and aligns events to the local night. [USNO API](https://aa.usno.navy.mil/data/api) |
| Building geometry and land use | NYC Building Footprints and PLUTO, joined by BIN and BBL respectively | Supplies map geometry, representative coordinates, roof height, footprint area, and lighting-program eligibility. Height and area can enter a model profile with matched training data. Land use controls program eligibility. [NYC Footprints](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue/about_data), [PLUTO](https://data.cityofnewyork.us/City-Government/Primary-Land-Use-Tax-Lot-Output-PLUTO-/64uk-42ks/about_data) |
| Neighborhood artificial light | NASA VIIRS Black Marble VNP46A2 Collection 2 for observed training. Forecast mode takes prior radiance through `--daily-inputs`. | The ingestion code samples the nearest pixel and retains nonnegative radiance with mandatory quality flag `0`. The roughly 500 m pixel measures neighborhood brightness. Operator light output is a separate measurement. [NASA Black Marble product](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2/) |
| Weather forecast | NOAA HRRR, sampled at each candidate building's representative point | Uses hourly values within sunset to sunrise. Visibility and ceiling are overnight minima. Wind components, cloud cover, and precipitation rate are hourly means. The forecast records the run time and coverage. [NOAA HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/) |
| Issued migration forecast | User-supplied `--daily-inputs` CSV with traffic in birds crossing one kilometer per hour, issue time, and region | Supplies one forecast shared by candidate buildings for each night. A regional forecast product such as BirdCast is a possible source if the supplied estimate has the required units and region. The project does not fetch it automatically. [BirdCast forecast products](https://birdcast.org/science-center/for-meteorologists/) |
| Building lighting settings | User-supplied `--operator-settings` CSV | Supplies approved cuts, eligible zones, scheduled and fixed output, and an operator cap in a consistent unit. These fields go to the recommendation layer. |

Raw responses are cached under `data/raw/`. The retrospective dashboard also shows separate NEXRAD radar summaries and neighborhood cells as map context. The observed regression uses the Dryad migration field, which matches its training source. The daily forecast path uses issued inputs and skips observed radar.

## How the regression works

1. Read the baseline and any `--augment` CSVs and join them by study site and local night. Build only profiles with complete inputs on at least two nights and two study sites. This is the code's minimum to fit and split data. The evaluation report shows the actual coverage.
2. Build `season_fall` from `season`, with fall as `1` and spring as `0`. If the season field is blank, the code uses the Dryad convention of labeling nights by the following morning's month. January through June maps to spring and July through December maps to fall. The observed training rows cover monitored migration nights.
3. Compute `night_hours = (sunrise_utc - sunset_utc)` in hours and `potential_moonlit_hours = moon_illum_fraction × moon_above_horizon_minutes / 60`. The second quantity is a proxy for potential lunar exposure before cloud effects. Civil dusk and dawn set the dimming window.
4. Apply `log1p` to migration traffic and radiance when those features are used. Standardize each transformed feature with the training mean and standard deviation for that profile. A constant feature gets a scale of `1`. Each evaluation split fits its preprocessing on the training partition only.
5. Fit an intercept and coefficients with SciPy L-BFGS-B. The objective is mean Poisson negative log-likelihood, omitting the term constant in the parameters, plus `0.05 × sum(coefficient²) / 2`. The intercept is unpenalized. For standardized features `z`, the expected count is `exp(intercept + sum(coefficient × z))`.
6. Fit the saved profiles on all eligible rows after evaluation. At scoring time the code selects the fitted profile with the most usable features for the row's season. Missing optional fields can fall back to a simpler profile. Missing required fields leave the estimate blank. The row records the features used and the three largest absolute log-rate contributions with nonzero effects, relative to the fitted feature means.

### Feature contract

| Profile | Required model features | Optional enrichments |
| --- | --- | --- |
| Observed | `season_fall`, `observed_migration_birds_per_km_h`, `observed_visibility_m`, `observed_cloud_ceiling_m`, `observed_wind_u_m_s`, `observed_wind_v_m_s`, `observed_temperature_c` | `night_hours` and `potential_moonlit_hours`, matched `roof_height_m` and `footprint_area_m2`, quality-checked `observed_neighborhood_radiance` |
| Forecast | `season_fall`, `forecast_migration_birds_per_km_h`, `forecast_visibility_m`, `forecast_wind_u_m_s`, `forecast_wind_v_m_s`, `forecast_cloud_cover_pct`, `forecast_precip_rate_mm_h`, `night_hours`, `potential_moonlit_hours` | `forecast_cloud_ceiling_m`, matched `roof_height_m` and `footprint_area_m2`, quality-checked `prior_neighborhood_radiance` available by the cutoff |

The observed artifact has spring-and-fall fits (`base`, `footprint`) and fall-only fits (`natural_light`, `natural_light+footprint`). A candidate building with a known roof height, footprint area, and perimeter is scored by a footprint profile, so buildings on the same night get different estimates. In the Lower Manhattan replay of September 10, 2021, the 1,427 buildings received 1,366 distinct estimates, from below 0.1 for small sheds to about 8 for 2 World Financial Center, a tall tower with a long facade. NYC footprints record an unknown roof height as `0`. The scorer treats that as missing, so those buildings fall back to the night-level fit. The replay does not load same-night satellite radiance into the scorer.

### Evaluation in the saved artifact

The chronological split holds out the newest 20% of eligible nights, rounding up and keeping all rows for each night together. The separate site split holds out the last 20% of sorted study IDs, rounding up. The base site's holdout IDs are `u`, `v`, `w`, `x`, `y`, and `z`. The benchmark predicts the training mean for the row's season, with the overall training mean as a fallback.

| Profile and holdout | Test rows | MAE, seasonal mean | MAE, base Poisson on the same rows | MAE, fitted profile | Mean Poisson deviance, fitted profile |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base, future nights | 1,587 | 0.861 | — | 0.837 | 3.923 |
| Base, unseen buildings | 385 | 1.499 | — | 1.433 | 7.127 |
| Natural light, future nights | 263 | 1.001 | 0.391 | 0.422 | 1.359 |
| Natural light, unseen buildings | 259 | 1.935 | 1.865 | 1.853 | 7.901 |
| Footprint, future nights | 1,397 | 0.921 | 0.895 | 0.861 | 3.96 (base 4.32) |
| Footprint, unseen buildings | 297 | 1.746 | 1.679 | 1.615 | 8.48 (base 8.75) |
| Natural light + footprint, future nights | 227 | 1.111 | 0.385 | 0.408 | 1.27 (base 1.44) |
| Natural light + footprint, unseen buildings | 261 | 2.044 | 1.939 | 1.821 | 7.68 (base 7.87) |

MAE is the average absolute count error per survey. On the same fall rows, adding astronomy improved Poisson deviance on both splits and increased MAE on the future-night split. The footprint profile's unseen-building holdout is the last five matched sites in sorted order: `t`, `u`, `v`, `y`, and `z`. That set includes `y` (3 World Trade Center), the site with the most collisions per night.

The report also measures the fraction of observed collisions captured by alerting the top `ceil(0.10 × buildings on that night)` rows. This metric tests whether the model can tell which buildings will be hit. Profiles without building traits give every building the same estimate on a night, so their ranking comes from the tie-break by study ID.

| Profile and holdout | Collisions captured, seasonal mean | Collisions captured, fitted profile |
| --- | ---: | ---: |
| Base, future nights | 0.21 | 0.21 |
| Base, unseen buildings | 0.04 | 0.04 |
| Footprint, future nights | 0.22 | **0.27** |
| Footprint, unseen buildings | 0.02 | **0.39** |
| Natural light + footprint, future nights | 0.37 | **0.39** |
| Natural light + footprint, unseen buildings | 0.04 | **0.39** |

On buildings it never saw, the footprint profile catches 39% of collisions in its top 10%, compared with 2% for a ranking without building traits. With facade length handled by the exposure term, roof height carries the building effect. Its coefficient on standardized log height is `+0.29` in the spring-and-fall fit and `+0.46` in the fall fit. The footprint-area coefficients are near zero (`+0.02` and `−0.18`), because a larger footprint mostly means a longer facade, which the exposure term already counts. Per meter of facade, taller buildings have more collisions.

### Computational cost

Measured on an Apple laptop with the cached data and the saved artifact:

| Step | Cost |
| --- | --- |
| Load and join the training CSVs (5,422 rows) | 0.04 s |
| Train all profiles, including both holdout evaluations | 0.45 s, 6 MB peak memory |
| Saved artifact | 31 KB, 40 learned numbers |
| Score one building | About 9 µs, roughly 115,000 buildings per second. All of NYC's roughly 1.08 million buildings would take about 9 s. |
| One-night replay of Lower Manhattan (1,427 buildings) through `main.py --cli` | 1.1 s end to end |

The model is not the bottleneck. A new replay night spends about a minute downloading NEXRAD radar, and the first run for a borough downloads that borough's commercial footprints.

### Known limits and next steps

- **Only 21 matched training buildings,** mostly tall towers in Lower Manhattan. Estimates for short or outer-borough buildings extrapolate beyond the training data, and no collision surveys exist to test them.
- **No uncertainty intervals.** The model outputs a mean count without a confidence range.
- **Hand-set regularization.** The L2 penalty of `0.05` was not tuned by cross-validation.
- **Simpler than the study's model.** Chen et al. (2024) fit negative binomial mixed models with migration × weather interactions, day-of-year polynomials, year effects, and random intercepts for building and volunteer. Their strongest fall effects were interactions: low cloud ceiling and southerly or westerly winds mattered mostly on heavy-migration nights. Adding those interactions, day-of-year terms, and a negative binomial likelihood to `src/models/count.py` is the most direct accuracy improvement. The study's random-effect standard deviation for building was `1.06` in fall (Table S6), which confirms that buildings differ a lot.
- **No artificial-light coefficients.** No training row has a quality-checked satellite radiance sample yet.

### A concrete score from the saved data

Study site `b` on October 1, 2021, has observed migration of `297.6325` birds/km/h, visibility of `16071.11765` m, ceiling of `22000` m, wind U/V of `1.71462` and `0.04564` m/s, and temperature of `17.22353` °C. Sunset and sunrise yield `12.2667` night hours. A Moon illumination fraction of `0.27` and `280` minutes above the horizon give `1.26` potential moonlit hours. The saved natural-light fit gives **`0.5930867` expected collisions per survey**, which maps to `medium`. The actual survey count was `0`. This example is from the fitted dataset and illustrates the calculation. The holdouts above measure performance.

## Daily scoring and output

The forecast decision time is the earlier of the run time and one hour before sunset. The code requires the migration forecast to have been issued by then and the HRRR run to have been initialized at least two hours earlier. The two-hour allowance is a project publication assumption. `hrrr_required_field_hours` must equal positive `hrrr_expected_hours`. Ceiling coverage is tracked separately because a clear grid cell can lack a ceiling. A prior radiance sample must have quality `0` and an availability timestamp at or before the decision time. Same-night observations belong to historical replay.

The daily CSV uses a blank BIN for the migration row and a seven-digit BIN for each optional prior radiance row. This example shows the two row types with illustrative values.

```csv
night_date,bin,forecast_migration_birds_per_km_h,forecast_migration_issue_utc,forecast_migration_region,prior_neighborhood_radiance,prior_radiance_quality,prior_radiance_available_at_utc,prior_radiance_granule
2026-10-01,,800,2026-10-01T16:00:00Z,NYC,,,,
2026-10-01,1000001,,,,12.5,0,2026-09-30T14:00:00Z,example-granule
```

The saved October 1, 2021, HRRR replay has eight labeled rows for one night and lacks an issued migration forecast. It yields zero eligible forecast training rows. To assemble forecast training data, save each daily export, join completed survey counts by verified site and night, and preserve the weather and migration issue timestamps. The forecast trainer expects `study_building_id`, `night_date`, `observed_collision_count`, and the raw fields and timestamps needed for the forecast features above.

When a forecast artifact is absent, the demo uses regional traffic to set priority. Below `500` birds/km/h is low, `500` through under `2000` is medium, and `2000` or more is high. Visibility below `8000` m or ceiling below `1000` m moves medium to high. Valid Sun and Moon coverage is required, and the explanation includes potential moonlit hours and usable prior radiance as context. This path leaves `expected_collision_count` blank.

### Files and fields

| Output | What to expect |
| --- | --- |
| `data/processed/models/observed.json` or `forecast.json` | Training artifact with model mode, artifact and feature versions, feature order, eligible seasons, means, scales, coefficients, intercepts, input paths and checksums, coverage, split summaries, and evaluation metrics. |
| `data/processed/buildings/<night>_<area>[_forecast].csv` and `.geojson` | One candidate row or map feature per BIN and night. Includes building IDs and attributes, source conditions, available timestamps and coverage, `expected_collision_count`, `risk_priority`, `risk_basis`, `risk_features`, and `risk_reasons`. GeoJSON adds footprint geometry. The suffix `_forecast` is used only in forecast mode. |
| `data/processed/judgements/<night>_<area>[_forecast].csv` and `.geojson` | One decision per candidate building and night. Includes the estimate and priority, `recommendation_status`, `tier`, `action`, `reduction_pct`, eligible zones, UTC and NYC-local times, scheduled, target and projected light output, approved scenarios, and reasons. Missing numeric values are blank in CSV and null in GeoJSON. |
| Dashboard | Nightly counts and forecast coverage, a building map colored by priority, and per-building explanations and dimming details. Historical replay includes the radar timeline when available. |

`expected_collision_count` is a decimal mean, so `0.593` means about 0.593 birds found per comparable survey on average. The project does not output a calibrated probability of at least one collision. Model priority is `high` for counts at least `1`, `medium` for counts at least `0.25` and below `1`, `low` below `0.25`, and `unavailable` when the required model inputs fail. `risk_basis` distinguishes an observed model, forecast model, or forecast demo rules.

## How the light recommendation is calculated

The recommendation applies to buildings in the lighting program with an available priority. The program uses NYC building feature code `2100` and PLUTO land-use codes `4` or `5`. Operator settings can be a per-BIN default or a dated override. The operator supplies the light cap and the approved reductions. Priority magnitude does not set the cap or select the cut.

For each approved percentage, the code computes `projected output = fixed output + scheduled eligible output × (1 - reduction_pct / 100)`. It selects the smallest approved cut that meets the cap. If a positive cut is selected, the output schedules the eligible zones from civil dusk to civil dawn. Fixed output includes required lighting. A cap that no approved cut can meet, missing settings, missing priority, or missing twilight routes the case to review where applicable.

For example, with `80 lm` of scheduled eligible output, `20 lm` fixed, a `60 lm` cap, and approved cuts of `0|25|50|100`, the selected cut is **50% of eligible output**. The projected total is `60 lm`. This is a light-output budget calculation. Estimating how the cut changes collisions or flight behavior requires measured lighting and survey outcomes under approved settings.

This operator CSV row encodes that example as the default for the BIN. Fill in `night_date` to override it for one night.

```csv
bin,night_date,eligible_zones,approved_reductions_pct,scheduled_eligible_light_output,fixed_light_output,target_artificial_light_output,light_output_unit
1000001,,facade|decorative,0|25|50|100,80,20,60,lm
```

The recommendation status is `scheduled` for a timed cut or `cap_met` for an approved 0% cut. Review cases carry `cap_unmet`, `missing_risk`, `missing_operator_settings`, or `missing_twilight`. Buildings outside the program carry `not_in_program`.

## Reproduce or run it

Run these commands from the project root with the Python environment active and the requirements installed. [Project setup](README.md)

```sh
# Build the training inputs (Dryad baseline, then fall 2021 Sun and Moon features).
python -m src.ingest coverage
python -m src.ingest --output data/processed/replays/fall-2021 pull --start 2021-08-31 --end 2021-11-14 --skip-satellite

# Write each matched study site's building traits from the verified crosswalk
# (configs/study_building_bins.csv, built from the study's Table S2; see configs/README.md).
python -m src.ingest sites --crosswalk configs/study_building_bins.csv

# Rebuild the saved observed model and evaluation. --sites fits the footprint profiles
# (log roof height, log footprint area) so buildings on the same night differ.
python -m src.models train \
  --input data/processed/baseline.csv \
  --augment data/processed/replays/fall-2021/training.csv \
  --sites data/processed/site_features.csv \
  --output data/processed/models/observed.json

# Run a historical replay with the observed model.
python main.py --cli --start 2021-10-01 --area pilot

# Train the separate forecast model once eligible labeled inputs are available.
python -m src.models train --mode forecast \
  --input labeled-forecast-nights.csv \
  --output data/processed/models/forecast.json

# Run the current NYC night with issued inputs and an operator light budget.
python main.py --cli --forecast --area pilot \
  --daily-inputs daily-inputs.csv \
  --operator-settings operator-settings.csv
```

The implementation is in [the trainer](src/models/count.py), [the feature and availability checks](src/features/model_inputs.py), [the forecast input join](src/features/daily.py), [the building scorer](building_analysis.py), and [the recommendation layer](src/recommendations/dimming.py).
