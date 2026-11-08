# DarkSky collision model

## What was trained

The saved **historical observed model** is [observed.json](data/processed/models/observed.json). It contains two fitted Poisson regressions. The base fit uses 5,422 survey rows from 27 study sites over 560 monitored NYC nights from March 31, 2017, through November 14, 2021. The natural-light fit uses the 1,190 of those rows with usable Sun and Moon features, covering 20 sites and 76 nights in fall 2021.

I reran training in a temporary directory with the current code. Both CSV checksums matched the saved artifact, and retraining reproduced its coefficients and evaluation report exactly. The artifact is local because Git ignores `data/processed/`.

The saved training data has zero rows with verified study-to-BIN footprint joins or usable satellite radiance, so neither fit has building geometry or artificial-light coefficients. **There is no trained pre-sunset forecast artifact in this workspace.** The forecast training and scoring code is implemented. With suitable daily inputs, it can currently produce a demo priority while the numeric forecast remains blank.

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

The observed artifact has a spring and fall base fit and a fall-only natural-light fit. On the same night, candidate buildings can get identical estimates when they share all fitted inputs. The current replay provides Dryad and astronomy inputs, with geometry for the map. It does not load same-night satellite radiance into the scorer.

### Evaluation in the saved artifact

The chronological split holds out the newest 20% of eligible nights, rounding up and keeping all rows for each night together. The separate site split holds out the last 20% of sorted study IDs, rounding up. The base site's holdout IDs are `u`, `v`, `w`, `x`, `y`, and `z`. The benchmark predicts the training mean for the row's season, with the overall training mean as a fallback.

| Profile and holdout | Test rows | MAE, seasonal mean | MAE, base Poisson on the same rows | MAE, fitted profile | Mean Poisson deviance, fitted profile |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base, future nights | 1,587 | 0.861 | — | 0.837 | 3.923 |
| Base, unseen buildings | 385 | 1.499 | — | 1.433 | 7.127 |
| Natural light, future nights | 263 | 1.001 | 0.391 | 0.422 | 1.359 |
| Natural light, unseen buildings | 259 | 1.935 | 1.865 | 1.853 | 7.901 |

MAE is the average absolute count error per survey. On the same fall rows, adding astronomy improved Poisson deviance on both splits and increased MAE on the future-night split. The report also measures the fraction of observed collisions captured by alerting the top `ceil(0.10 × buildings on that night)` rows. Ties are resolved by study ID. Both saved profiles use shared nightly predictors, so this metric can reflect the tie-break rather than useful within-night building ranking.

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
# Rebuild the saved observed model and evaluation.
python -m src.models train \
  --input data/processed/baseline.csv \
  --augment data/processed/replays/fall-2021/training.csv \
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
