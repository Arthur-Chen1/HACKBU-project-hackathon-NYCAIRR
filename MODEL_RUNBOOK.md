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

The saved **pre-sunset forecast model** is [forecast.joblib](data/processed/models/forecast.joblib), with its evaluation in [forecast.report.json](data/processed/models/forecast.report.json). It is a machine-learning model (gradient-boosted trees) and replaces the forecast-mode Poisson regression. It returns the same outputs: an expected count, priority, features used, and three main factors for each building-night. [How the forecast AI model works](#how-the-forecast-ai-model-works)

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

1. Read the baseline and any `--augment` CSVs and join them by study site and local night. With `--sites`, attach each matched site's static traits (BIN, roof height, footprint area, perimeter, and `surveyed_facade_m`) to every night of that site. The verified match replaces the `nyc_reference_point` placeholder that crosswalk-free replays assign. Any other conflicting value stops training. Build only profiles with complete inputs on at least two nights and two study sites. This is the code's minimum to fit and split data. The evaluation report shows the actual coverage.
2. Build `season_fall` from `season`, with fall as `1` and spring as `0`. If the season field is blank, the code uses the Dryad convention of labeling nights by the following morning's month. January through June maps to spring and July through December maps to fall. The observed training rows cover monitored migration nights.
3. Compute `night_hours = (sunrise_utc - sunset_utc)` in hours and `potential_moonlit_hours = moon_illum_fraction × moon_above_horizon_minutes / 60`. The second quantity is a proxy for potential lunar exposure before cloud effects. Civil dusk and dawn set the dimming window.
4. Apply `log1p` to migration traffic, radiance, roof height, and footprint area when those features are used. A roof height or footprint area of `0` is NYC's code for unknown and is treated as missing. Standardize each transformed feature with the training mean and standard deviation for that profile. A constant feature gets a scale of `1`. Each evaluation split fits its preprocessing on the training partition only.
5. Fit an intercept and coefficients with SciPy L-BFGS-B. The objective is mean Poisson negative log-likelihood, omitting the term constant in the parameters, plus `0.05 × sum(coefficient²) / 2`. The intercept is unpenalized. For standardized features `z`, the expected count is `exp(intercept + sum(coefficient × z))`. Profiles that use building traits (`footprint` and `natural_light+footprint`) add a fixed exposure offset, which models collisions per 100 m of facade: `expected count = (facade_m / 100) × exp(intercept + sum(coefficient × z))`. Training uses `surveyed_facade_m`, the Table S2 survey distance. Scoring uses the candidate's `footprint_perimeter_m`. The offset has no coefficient.
6. Fit the saved profiles on all eligible rows after evaluation. At scoring time the code selects the fitted profile with the most usable features for the row's season. A profile with an exposure offset also requires a facade length, from `surveyed_facade_m` or `footprint_perimeter_m`. Missing optional fields can fall back to a simpler profile. Missing required fields leave the estimate blank. The row records the features used and the three largest absolute log-rate contributions with nonzero effects, relative to the fitted feature means. For exposure profiles, the facade term appears as `facade_length_per_100m`, the log of the facade length in 100 m units.

### Feature contract

| Profile | Required model features | Optional enrichments |
| --- | --- | --- |
| Observed | `season_fall`, `observed_migration_birds_per_km_h`, `observed_visibility_m`, `observed_cloud_ceiling_m`, `observed_wind_u_m_s`, `observed_wind_v_m_s`, `observed_temperature_c` | `night_hours` and `potential_moonlit_hours`; matched `roof_height_m` and `footprint_area_m2`, with facade exposure (`surveyed_facade_m` in training, `footprint_perimeter_m` in scoring); quality-checked `observed_neighborhood_radiance` |
| Forecast AI model | `season_fall`, `day_of_year`, `forecast_migration_birds_per_km_h`, `forecast_visibility_m`, `forecast_cloud_ceiling_m` (blank means clear, scored as `22000`), `forecast_wind_u_m_s`, `forecast_wind_v_m_s`, and decision-time lineage | matched `roof_height_m`, with facade exposure (`surveyed_facade_m` in training, `footprint_perimeter_m` in scoring) |

The observed artifact has spring-and-fall fits (`base`, `footprint`) and fall-only fits (`natural_light`, `natural_light+footprint`). A candidate building with a known roof height, footprint area, and perimeter is scored by a footprint profile, so buildings on the same night get different estimates. In the Lower Manhattan replay of September 10, 2021, the 1,427 buildings received 1,366 distinct estimates, from below 0.1 for small sheds to about 8 for 2 World Financial Center, a tall tower with a long facade. NYC footprints record an unknown roof height as `0`. The scorer treats that as missing, so those buildings fall back to the night-level fit. The replay does not load same-night satellite radiance into the scorer.

### Evaluation in the saved artifact

The chronological split holds out the newest 20% of eligible nights, rounding up and keeping all rows for each night together. The separate site split holds out the last 20% of sorted study IDs, rounding up. The base site's holdout IDs are `u`, `v`, `w`, `x`, `y`, and `z`. The benchmark predicts the training mean for the row's season, with the overall training mean as a fallback.

| Profile and holdout | Test rows | MAE, seasonal mean | MAE, base Poisson on the same rows | MAE, fitted profile | Mean Poisson deviance, fitted profile |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base, future nights | 1,587 | 0.861 | — | 0.837 | 3.923 |
| Base, unseen buildings | 385 | 1.499 | **1.429** | 1.470 | 7.41 | **4.41** | 0.04 | 0.04 |
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

## How the forecast AI model works

The pre-sunset forecast uses a gradient-boosted tree ensemble ([src/models/forecast_ai.py](src/models/forecast_ai.py), scikit-learn `HistGradientBoostingRegressor` with a Poisson loss). Trees learn interactions such as heavy migration with a low ceiling, and seasonal timing, without hand-written terms. The Poisson regression would need those terms added by hand.

**Training data.** No labeled pre-sunset forecast history exists yet. That would need issued migration forecasts and HRRR runs joined to survey counts. The model trains on the same rows as the observed model and treats each observed value as a perfect forecast of itself.

| Forecast input at scoring | Training proxy | Unit handling |
| --- | --- | --- |
| `forecast_migration_birds_per_km_h` | Dryad KOKX `observed_migration_birds_per_km_h` | `log1p` |
| `forecast_visibility_m` | LaGuardia `observed_visibility_m` | Forecasts capped at 16,093 m, the station's 10-mile reporting limit |
| `forecast_cloud_ceiling_m` | LaGuardia `observed_cloud_ceiling_m` | A blank HRRR ceiling (clear sky) is scored as Dryad's unlimited `22000` m |
| `forecast_wind_u_m_s`, `forecast_wind_v_m_s` | LaGuardia wind components | None |
| `season_fall`, `day_of_year`, `roof_height_m` | Known in advance | Roof height `log1p`; `0` is missing |

Temperature, cloud cover, and precipitation are left out because one side of the mapping lacks them. Night hours and moonlight are left out because only fall 2021 rows have them, so the trees would learn them as a stand-in for the year. Footprint area is left out because facade exposure already counts size. With 21 buildings, tree splits on area mostly picked out individual sites.

**Fit.** Two fits are saved. The `footprint` fit (5,202 rows, 21 sites, 560 nights) models collisions per 100 m of facade, with the same exposure as the regression: `expected count = (facade_m / 100) × tree rate`. The `base` fit (5,422 rows, 27 sites) is the night-level fallback for buildings without a roof height or perimeter. Hyperparameters were chosen on the holdouts below: 400 trees, learning rate 0.02, 7 leaves per tree, at least 80 rows per leaf, L2 of 2, and fixed `random_state` 0. Expected counts are constrained to rise with migration traffic and roof height.

**Extrapolation.** Trees predict a flat value beyond their training range. On the Lower Manhattan pilot, 60% of candidate buildings are shorter than the shortest study building (27 m). Outside each feature's training range, the model evaluates the trees at the range edge and continues with the slope of a Poisson regression fit to the same rows. A 15 m building therefore scores below a 27 m study site, as it does in the regression. Migration heavier than any study night still raises the estimate.

**Explanations.** For each feature, the scorer replaces the building's value with the training median and measures the change in log rate. The three largest effects become `risk_reasons`, in the same wording as the regression. The facade term appears as `facade_length_per_100m`.

**Evaluation.** The holdouts match the observed model: the newest 20% of nights, and the last 20% of sorted study sites. Each split compares the trees with a Poisson regression on the same features and rows.

| Fit and holdout | Test rows | MAE, seasonal mean | MAE, Poisson | MAE, trees | Deviance, Poisson | Deviance, trees | Top-10% capture, Poisson | Top-10% capture, trees |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Footprint, future nights | 1,397 | 0.921 | 0.861 | **0.860** | 3.94 | 3.94 | 0.27 | **0.39** |
| Footprint, unseen buildings | 297 | 1.746 | 1.610 | **1.511** | 8.79 | **4.33** | 0.39 | 0.39 |
| Base, future nights | 1,587 | 0.861 | 0.838 | **0.828** | 3.92 | **3.84** | 0.21 | 0.21 |
| Base, unseen buildings | 385 | 1.499 | **1.429** | 1.483 | 7.41 | **4.12** | 0.04 | 0.04 |

The trees roughly halve the deviance on unseen buildings, which means far fewer confidently wrong estimates. On future nights they catch 39% of collisions in each night's top 10%, compared with 27% for the regression. Across all training rows, mean predictions match the observed mean (0.34 collisions per survey). Over the busy fall 2021 rows, the mean prediction is 0.88, against 1.04 observed (0.96 before smoothing).

**Migration smoothing.** Trees respond to migration in steps. Before smoothing, the riskiest Lower Manhattan building scored 2.19 at every migration level from about 500 to 1,000 birds/km/h, so forecasts in that range changed nothing. Each estimate now averages the rate over nine migration levels spaced evenly in log space, within ±0.75 of the forecast (about a factor of 2 either way). That is narrower than the automatic migration estimate's typical error of about 3×. Averaging into a curve that flattens at the top lowers estimates, so each fit stores one correction factor (1.058). It is learned from that fit's training rows so that smoothed expected collisions sum to the observed total. The same building now scores 1.90, 2.20, 2.54 and 3.02 at 494, 600, 768 and 1,000 birds/km/h. The cost is on unseen buildings, where deviance rose from 3.96 to 4.33, still half the regression's 8.79. Future-night results are unchanged. All metrics above include the smoothing. On September 10, 2021, the automatic migration estimate of 768 birds/km/h now gives 19 high and 397 medium buildings, against 14 and 343 when the steps left 494 and 768 identical.

**What the evaluation does not show.** Training inputs are observed conditions, so the holdouts contain no forecast error. Treat these scores as an upper bound on pre-sunset skill. HRRR overnight minima and LaGuardia nightly means also differ, which shifts estimates. On October 1, 2021, HRRR forecast a southerly wind (V = 1.05 m/s, against 0.05 observed), and the study-site mean estimate fell from 0.26 with observed weather to 0.12. Back-test against saved daily exports once survey counts can be joined to them.

**Example runs** (Lower Manhattan, 1,427 buildings, regional migration set to the Dryad value with an issue time before the cutoff):

| Night | Migration | High / medium / low | Largest estimate | Time |
| --- | ---: | --- | --- | ---: |
| October 1, 2021 (light) | 298 birds/km/h | 0 / 1 / 1,426 | 0.36, BIN 1066406 (915 m perimeter) | 3.5 s with cached HRRR |
| September 10, 2021 (heavy) | 4,945 birds/km/h | 222 / 968 / 237 | 8.7, BIN 1088797 (324 m) | 33 s including HRRR download |
| September 10, 2021, automatic migration estimate | 494 birds/km/h (estimated) | 14 / 343 / 1,070 | | 1.0 s cached |
| November 8, 2026, all of Manhattan (18,333 buildings), automatic estimate | 26 birds/km/h (estimated) | 0 / 0 / 18,333 | | 11 s cached |

The dashboard has forecast presets for the first two nights, using the study's measured traffic as test migration forecasts, and a third that uses the automatic estimate.

On October 1, the forecast ranks buildings much as the observed regression does (Spearman 0.96 across the 1,427 buildings).

### Forecast computational cost

| Step | Cost |
| --- | --- |
| Train both fits with both holdout evaluations | About 10 s |
| Saved artifact | 0.76 MB joblib plus a 13 KB JSON report |
| Score buildings in a batch | About 50–80 µs each; 14,270 buildings in 0.7 s |
| HRRR weather for one night (72 fields) | About 1 s of computation for any number of buildings, plus download time for uncached runs |

HRRR sampling used to call eccodes' nearest-point search for every building in every field. That costs about 0.4 s per call, which projects to about 11 hours for the 1,427 pilot buildings. The sampler now builds a KD-tree once per run over the grid points near the request, then reads every building's value from each decoded field by index. On cached fields its values match the old search exactly. The 3 km HRRR grid puts all 1,427 pilot buildings in 2 grid cells.

### Known limits and next steps

- **Only 21 matched training buildings,** mostly tall towers in Lower Manhattan. Estimates for short or outer-borough buildings extrapolate beyond the training data, and no collision surveys exist to test them.
- **No uncertainty intervals.** The model outputs a mean count without a confidence range.
- **Hand-set regularization.** The L2 penalty of `0.05` was not tuned by cross-validation.
- **Simpler than the study's model.** Chen et al. (2024) fit negative binomial mixed models with migration × weather interactions, day-of-year polynomials, year effects, and random intercepts for building and volunteer. Their strongest fall effects were interactions: low cloud ceiling and southerly or westerly winds mattered mostly on heavy-migration nights. Adding those interactions, day-of-year terms, and a negative binomial likelihood to `src/models/count.py` is the most direct accuracy improvement. The study's random-effect standard deviation for building was `1.06` in fall (Table S6), which confirms that buildings differ a lot.
- **No artificial-light coefficients.** No training row has a quality-checked satellite radiance sample yet.
- **The forecast model has never seen a forecast.** It learned from observed weather and migration. Replace the proxies with saved daily exports joined to survey counts when those exist. The forecast model ignores prior radiance because no training row has it.
- **The forecast artifact is a pickle.** `joblib` files can run code when loaded. Load only models you trained.

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

To build a real forecast history, save each daily export, join completed survey counts by verified site and night, and keep the weather and migration issue timestamps.

**Automatic migration estimate.** With no daily input file and nothing typed in the dashboard, the forecast estimates regional traffic itself ([src/models/migration_forecast.py](src/models/migration_forecast.py)). Gradient-boosted trees map the day of year and tonight's HRRR overnight weather at the NYC reference point to KOKX birds/km/h. They train on the Dryad study's nightly traffic and LaGuardia weather from years before the forecast night only, so a 2021 replay learns from 2017–2020. The estimate carries the cutoff as its issue time and a region label naming the seasons used. It is blank outside the monitored seasons (March 31–June 7 and August 30–November 14). On held-out nights it ranks nights better than a seasonal median of the same history: Spearman 0.57 on the newest 20% of nights and 0.69 on 2021, against 0.28 and 0.49. A typical night is still off by a factor of about 3.

The raw trees predict a typical night for the conditions, which pulls every estimate toward the middle. The estimate is therefore recalibrated in log space so its spread matches the observed spread: `mean(observed) + scale × (raw − mean(raw))`. The means and scale come from leave-one-year-out predictions within the training years. The scale is about 1.33. With one training year there is nothing to hold out, and the raw estimate is used. Forward-chained over 2019–2021, with each year trained only on earlier years and observed weather standing in for the forecast:

| Migration level (low < 500, medium < 2,000, high ≥ 2,000 birds/km/h) | Raw | Recalibrated |
| --- | ---: | ---: |
| High nights called high | 4 of 37 | **17 of 37** |
| "High" calls that were actually low nights | 0 of 5 | 14 of 42 |
| Low nights called low | 200 of 228 | 188 of 228 |
| Spearman / typical log error | 0.65 / 1.13 | 0.65 / 1.21 |

The recalibration trades false alarms for catching heavy nights, which suits a lights-out alert. Missing a heavy night costs more than dimming on a quiet one.

**Last night's radar was tested and left out.** Consecutive nights correlate (0.33 in log traffic). After date and weather are accounted for, though, last night's forecast error barely predicts tonight's (correlation 0.16). Adding last night's traffic as a tree feature made results slightly worse (Spearman 0.64). Carrying last night's error forward moved Spearman from 0.65 to 0.66. Neither justifies downloading about 100 MB of radar per forecast night. A weather feature from the migrants' source region, north of NYC, is a more promising next input.

An issued forecast such as BirdCast's should replace the estimate whenever available.
The [forecast loading investigation and API request](docs/FORECAST_ANALYSIS.md) records the current
integration gap, the missing-artifact setup, and the November 2026 loading/coverage fixes.

In the dashboard's **Pre-sunset forecast** mode, a regional migration forecast can be typed in with its New York issue time. It overrides the daily input file for the selected nights and is logged with region `NYC (entered in dashboard)`. An issue time after the cutoff makes the estimate unavailable.

The saved October 1, 2021, HRRR replay has no issued migration forecast. The forecast AI model therefore trains on observed proxies, as described above.

When a forecast artifact is absent, expected counts and dimming suggestions are unavailable. The former migration/weather demo priority rules were removed during the [UI cleanup](docs/PRIORITY_UI.md) to keep one priority system.

### Files and fields

| Output | What to expect |
| --- | --- |
| `data/processed/models/forecast.joblib` and `forecast.report.json` | Forecast AI model (`artifact_version` 1, `feature_version` 4). The joblib holds `mode`, `fits`, and `report`. Each fit has `name`, `feature_names`, `exposure_offset`, the fitted `estimator`, `reference` medians for explanations, the training range and `slopes` used for extrapolation, and `smoothing_scale`, the migration-smoothing correction. The JSON copy of `report` has hyperparameters, training proxies, coverage, holdout evaluation against the Poisson regression and seasonal mean, and `sources`. |
| `data/processed/models/observed.json` | Observed Poisson artifact (`artifact_version` 1, `feature_version` 3). Top-level keys are `mode`, `fits`, and `report`. Each entry in `fits` has `feature_names`, `seasons`, `means`, `scales`, `coefficients`, `intercept`, and `exposure_offset` (true for building-trait profiles). `report.profiles` gives each profile's features, eligible coverage, `exposure_offset`, holdout evaluation, or the reason it was skipped. `report.sources` lists each input's path, SHA-256 checksum, and row count. The site traits entry also has `sites` and `rows_matched`. |
| `data/processed/site_features.csv` | Written by `python -m src.ingest sites`. One row per matched study site: `study_building_id`, `bin`, `name`, `feature_location`, `surveyed_facade_m`, `roof_height_m`, `footprint_area_m2`, `footprint_perimeter_m`, `construction_year`, `land_use`, `latitude`, and `longitude`. `construction_year` is informational and is not a model feature. |
| `data/processed/buildings/<night>_<area>[_forecast].csv` and `.geojson` | One candidate row or map feature per BIN and night. Includes building IDs and attributes (including `footprint_perimeter_m`, the facade exposure), source conditions, available timestamps and coverage, `expected_collision_count`, `risk_priority`, `risk_basis`, `risk_features`, `risk_reasons`, `suggested_dimming_pct`, `dimming_band`, and the dimming policy provenance. GeoJSON adds footprint geometry. The suffix `_forecast` is used only in forecast mode. |
| `data/processed/judgements/<night>_<area>[_forecast].csv` and `.geojson` | One decision per candidate building and night. Includes the estimate and priority, `recommendation_status`, `tier`, `action`, `reduction_pct`, eligible zones, UTC and NYC-local times, scheduled, target and projected light output, approved scenarios, and reasons. Missing numeric values are blank in CSV and null in GeoJSON. |
| Dashboard | Collision estimates and suggested dimming, a map with approximately 5% red / 20% amber / 75% pale, and per-building model explanations. Suggested percentages use an explicitly unvalidated planning rule. Historical replay includes the radar timeline when available. |

`expected_collision_count` is a decimal mean, so `0.593` means about 0.593 birds found per comparable survey on average. The project does not output a calibrated probability of at least one collision. The website's suggested dimming is `100 × expected_collision_count / (expected_collision_count + 0.25)`, an explicit planning policy rather than a learned lighting-response relationship. Map colors rank these suggestions with at most roughly 5% red and 25% red/amber combined; tied groups crossing a boundary move to the lower band. Legacy priority fields share this ranking. See [the calculation and limits](docs/DIMMING_ESTIMATES.md). `risk_basis` distinguishes the observed Poisson model, the `forecast AI model (gradient-boosted trees)`, or an unavailable-model label.

## How the light recommendation is calculated

The command-line operator-budget recommendation applies to buildings in the lighting program with an available priority. It is no longer displayed on the website. The program uses NYC building feature code `2100` and PLUTO land-use codes `4` or `5`. Operator settings can be a per-BIN default or a dated override. The operator supplies the light cap and the approved reductions. Priority magnitude does not set the cap or select the cut.

For each approved percentage, the code computes `projected output = fixed output + scheduled eligible output × (1 - reduction_pct / 100)`. It selects the smallest approved cut that meets the cap. If a positive cut is selected, the output schedules the eligible zones from civil dusk to civil dawn. Fixed output includes required lighting. A cap that no approved cut can meet, missing settings, missing priority, or missing twilight routes the case to review where applicable.

For example, with `80 lm` of scheduled eligible output, `20 lm` fixed, a `60 lm` cap, and approved cuts of `0|25|50|100`, the selected cut is **50% of eligible output**. The projected total is `60 lm`. This is a light-output budget calculation. Estimating how the cut changes collisions or flight behavior requires measured lighting and survey outcomes under approved settings.

This operator CSV row encodes that example as the default for the BIN. Fill in `night_date` to override it for one night.

```csv
bin,night_date,eligible_zones,approved_reductions_pct,scheduled_eligible_light_output,fixed_light_output,target_artificial_light_output,light_output_unit
1000001,,facade|decorative,0|25|50|100,80,20,60,lm
```

The recommendation status is `scheduled` for a timed cut or `cap_met` for an approved 0% cut. Review cases carry `cap_unmet`, `missing_risk`, `missing_operator_settings`, or `missing_twilight`. Buildings outside the program carry `not_in_program`.

## Reproduce or run it

Run these commands from the project root with the Python environment active and the requirements installed. [Project setup](README.md) Training is deterministic. With the cached inputs, rerunning the commands below reproduces `observed.json`, `site_features.csv`, and the replay outputs byte for byte. The forecast model uses a fixed random seed and reproduces the same report and estimates. After pulling a change that bumps `feature_version`, rerun the `sites` and `train` steps, because the scorer rejects older artifacts.

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

# Train the forecast AI model on the same rows, using observed conditions as forecast proxies.
python -m src.models train --mode forecast \
  --input data/processed/baseline.csv \
  --augment data/processed/replays/fall-2021/training.csv \
  --sites data/processed/site_features.csv \
  --output data/processed/models/forecast.joblib

# Run the current NYC night with issued inputs and an operator light budget.
python main.py --cli --forecast --area pilot \
  --daily-inputs daily-inputs.csv \
  --operator-settings operator-settings.csv
```

The implementation is in [the Poisson trainer](src/models/count.py), [the forecast AI model](src/models/forecast_ai.py), [the HRRR sampler](src/ingest/hrrr.py), [the feature and availability checks](src/features/model_inputs.py), [the forecast input join](src/features/daily.py), [the building scorer](building_analysis.py), and [the recommendation layer](src/recommendations/dimming.py).
