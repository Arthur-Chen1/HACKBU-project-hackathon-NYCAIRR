# DarkSky Migrations model design

DarkSky will estimate the number of collisions a morning survey is expected to find at a building. The hackathon deliverable is a historical replay for a small set of Manhattan buildings, with explainable priorities and an illustrative dimming schedule. Keep the first version small enough to run locally. The current code supplies training data and replay inputs. [Setup and commands](README.md)

## Where new work belongs

| Path | Responsibility |
| --- | --- |
| `src/ingest/__main__.py` | Runs `coverage` and `pull`, applies CLI options, joins source responses, and writes exports. |
| `src/ingest/http.py` | Handles HTTP retries and caches response bodies and retrieval metadata. The cache key includes the request URL and byte range. |
| `src/ingest/dryad.py` | Downloads Dryad version `272593`, verifies the source CSV checksum, and maps morning surveys to NYC night dates. |
| `src/ingest/nyc.py` | Validates the study-to-BIN crosswalk, fetches footprints and PLUTO records, and computes each footprint's area and representative point. |
| `src/ingest/usno.py` | Fetches two UTC days of Sun and Moon events at a common NYC point and aligns them to the local night, including daylight-saving transitions. |
| `src/ingest/viirs.py` | Finds Black Marble Collection 2 tiles in CMR and samples the nearest quality-checked pixel from downloaded HDF5 tiles. |
| `src/ingest/hrrr.py` | Selects a forecast run before the replay cutoff, retrieves GRIB messages by byte range, samples the nearest grid cells, and aggregates hourly values. |
| `src/features/training.py` | Groups repeated surveys, computes contiguous coverage windows, and writes CSV and JSON exports. Put reusable feature construction and decision-time joins under `src/features/`. |
| `tests/test_ingest.py` | Tests survey deduplication, coverage gaps, overnight dates, forecast selection and byte ranges, lot joins, and satellite quality handling without API calls. |
| `configs/` (planned) | Holds the verified crosswalk, pilot buildings, thresholds, and allowed lighting settings. Keep tokens in the environment. |
| `src/models/` (planned) | Fits the baseline and forecast models, evaluates them on held-out data, and saves the feature schema and fitted model. |
| `src/recommendations/` (planned) | Ranks candidate buildings, explains the priority, and applies approved lighting rules independently of the risk estimate. |
| `src/dashboard/` (planned) | Reads scored rows and footprint geometry to show the map and lighting schedule. |

## Data inventory

`data/raw/` contains the response bodies and JSON metadata sidecars grouped by provider. The current cache contains Dryad, NYC, USNO, NASA CMR, and NOAA HRRR pulls. A sidecar records the request URL, byte range, retrieval time, and any `Last-Modified` header. Preserve this cache so old runs can be replayed. Retrieval time records when this pipeline fetched an input. A forecast replay needs a separate source issue time or other evidence of when it became available.

| Saved path under `data/processed/` | Coverage and use |
| --- | --- |
| `baseline.csv` and `baseline_coverage.json` | 5,422 usable building-nights across 27 study sites and 560 monitored nights from March 31, 2017 through November 14, 2021. Use the CSV for the first retrospective fit. The report records 21 excluded building-nights with conflicting counts. |
| `replays/fall-2021/` | 1,190 labeled rows across 76 consecutive nights from August 31 through November 14, 2021. Includes Sun and Moon features and the Dryad observations. |
| `replays/2021-10-01-hrrr/` | Eight labeled rows for October 1, 2021. Includes Sun and Moon features and 12 hours of archived HRRR coverage for each required non-ceiling field at the NYC reference point. The ceiling field is missing in this snapshot. |

Each replay contains `training.csv` with the available features, `training_complete.csv` with the subset meeting the pipeline's coverage checks, `buildings.geojson` with candidate footprints, and `coverage.json` with the date windows and counts by join stage. Both saved replays used `--skip-satellite` and lack a verified crosswalk. They contain zero complete rows, so their complete CSVs contain only the schema. The 1,427 footprints in each replay are candidate map geometries. The surveys in those exports use the shared NYC reference point for gridded features.

The baseline report shows Black Marble catalog entries for 559 of 560 monitored nights, including all 76 nights in the fall replay. The missing catalog night is September 28, 2017. Catalog presence establishes that a tile was listed. Use a downloaded tile and its quality flags to check pixel coverage.

## Building-night contract

- **Key.** In the study exports, one row represents `(study_building_id, night_date)`. Preserve the study ID even after joining a verified `bin`. Use `(bin, night_date)` for candidate-building scores and retain BIN and BBL as identifiers. The study-to-BIN mapping is one-to-one. BBL is the tax-lot join key, and several buildings can share a lot. Use the study supplement's addresses to verify each match. [Study and supplement](https://doi.org/10.1111/1365-2664.14590)
- **Time.** `night_date` is the date the night starts in `America/New_York`. The source survey date is the following morning. Use timezone-aware UTC for event, observation, issue, and decision timestamps. Convert to NYC local time for the operator display. The NYC Black Marble overnight overpass uses the UTC date of the following morning.
- **Label.** `observed_collision_count` counts birds found on a morning survey. Zero is a completed survey with no birds found. Unsurveyed nights have no label. Repeated records with the same count collapse to one label, with `survey_records` recording the number of source rows. Conflicting counts are excluded from training exports. Use any independently recorded survey effort to account for differences in monitoring.
- **Location.** Join geometry by BIN and sample gridded inputs at the footprint's representative point when available. Use `feature_location` to distinguish `matched_footprint` from `nyc_reference_point`. Regional inputs and shared grid cells can produce identical values across buildings.
- **Missingness and lineage.** Read blank CSV fields as missing and preserve legitimate zeros. Keep provider versions, issue and valid times, sampling locations, quality flags, and coverage alongside features. Current footprint and PLUTO snapshots may postdate the study. Use historical snapshots or document that limitation when evaluating historical building features.

| Field group | Fields and interpretation |
| --- | --- |
| Survey and regional conditions | `season`, `survey_records`, `observed_collision_count`, `observed_migration_birds_per_km_h` (KOKX radar), and LaGuardia `observed_visibility_m`, `observed_cloud_ceiling_m`, `observed_wind_u_m_s`, `observed_wind_v_m_s`, and `observed_temperature_c`. |
| Buildings | `bin`, `bbl`, `land_use`, `roof_height_m`, `footprint_area_m2`, `feature_latitude`, `feature_longitude`, and `feature_location`. PLUTO land use describes a lot. Confirm a pilot building's use with its operator. |
| Natural light | `sunset_utc`, `sunrise_utc`, `civil_dusk_utc`, `civil_dawn_utc`, `moon_illum_fraction` from 0 to 1, and `moon_above_horizon_minutes` between sunset and sunrise. The moon fields describe potential moonlight. Model cloud cover separately. |
| Satellite light | `observed_neighborhood_radiance`, `radiance_quality`, `radiance_cloud_mask`, and `radiance_granule`. The sampler retains finite, nonnegative radiance with mandatory quality flag 0 and preserves the cloud mask for downstream checks. The approximately 500 m pixel describes a neighborhood. Building-level light output needs operator measurements. |
| Forecast weather | `decision_at_utc`, `hrrr_run_utc`, `hrrr_expected_hours`, `hrrr_required_field_hours`, and `hrrr_ceiling_hours`. Overnight summaries are the minimum of `forecast_visibility_m` and `forecast_cloud_ceiling_m`, and the means of `forecast_wind_u_m_s`, `forecast_wind_v_m_s`, `forecast_cloud_cover_pct`, and `forecast_precip_rate_mm_h`. The pipeline tracks ceiling separately because a cloud-free grid cell can lack a ceiling value. |
| Future forecast and pilot inputs | `forecast_migration_birds_per_km_h` with issue time and forecast region, `prior_neighborhood_radiance` with granule and availability time, and operator records for scheduled and actual settings, eligible zones, and measured light output. |

## Train and evaluate

1. **Fit the retrospective baseline.** Start from `data/processed/baseline.csv`. Check uniqueness of the study ID and night, the count distribution, and missingness by field and season. Compare a seasonal historical mean with a regularized Poisson count model using season, migration traffic, and observed weather. Compare added footprint and light features on the same eligible rows so coverage changes are visible. Keep survey counts and post-survey information out of the predictors.
2. **Hold out future nights and unseen buildings.** Keep all records for a given night together in chronological splits to avoid sharing that night's conditions across train and test. Evaluate a separate held-out group of sites to measure transfer to buildings outside the training set. Fit preprocessing on training data only. Report mean absolute count error, Poisson deviance, and the fraction of observed collisions captured at a fixed alert budget. Include coverage and sample sizes with each result. Study buildings were selected for collision risk, so citywide scores remain provisional.
3. **Add a probability if the dashboard needs one.** Fit and calibrate a binary model for `observed_collision_count > 0`, or validate the probability implied by the count model's distribution. A 30% risk means a comparable morning survey is expected to find one or more collisions on about 30 out of 100 similar building-nights. Check calibration on held-out nights and sites before displaying a percentage. Use priority labels for the demo while calibration is pending.
4. **Build a separate pre-sunset feature set.** The HRRR replay sets the decision time one hour before sunset and selects the latest hourly run initialized at least two hours before that decision. Treat the two-hour publication allowance as a replay assumption and verify availability when building the forecast evaluation set. Use only data available at the cutoff, including issued migration and weather forecasts, known astronomy, and previously available quality-checked radiance. Same-night observed migration, LaGuardia weather, and overnight satellite brightness belong in retrospective experiments. For a demo, a manually entered regional BirdCast forecast can be logged with its issue time. [BirdCast forecast information](https://birdcast.org/science-center/for-meteorologists/)
5. **Save enough to reproduce each run.** Record the model and preprocessing versions, ordered feature list, source and feature versions, split dates and sites, metrics, and coverage. Keep generated outputs separate from source code. For a forecast model, report how often missing inputs prevent scoring.

## From scores to a dimming schedule

The recommendation layer takes a candidate BIN, local night, score or rule priority, reason codes, input timestamps, and the operator's allowed settings. Return the priority, main factors, eligible zones, proposed reduction relative to scheduled output, local start and end times, and the basis for the action. Use the same BIN to join the dashboard row to `buildings.geojson`.

For the hackathon, use configurable thresholds for high regional migration and conditions such as low visibility, low cloud ceilings, and seasonally adverse winds. Add neighborhood brightness when a usable reading is available. Mark the score unavailable when a required input is missing. Show approved example reductions such as 25%, 50%, or 100% from civil dusk to civil dawn. A 100% reduction applies only to eligible zones. Emergency, egress, and other required lighting stays outside the dimming plan.

A pilot can estimate the effect of dimming by comparing approved settings on matched nights or randomizing among approved settings when feasible. Record what actually ran, measured facade lighting, weather, migration, and consistent survey effort and outcomes. With sufficient evidence, choose the smallest approved reduction that meets the pilot's collision-risk target and report uncertainty. Route cases with insufficient evidence to operator review.

## First implementation pass

Start with the saved baseline and place the trainer and evaluator under `src/models/`. Use the one-night HRRR replay to check timestamp and coverage handling before assembling a wider forecast set. Verify study-to-BIN mappings and rerun a night with satellite sampling before attributing gridded readings to individual buildings. Keep the training interface, demo rule, and dashboard output separate so the map can run while forecast inputs and pilot data are still being collected.
