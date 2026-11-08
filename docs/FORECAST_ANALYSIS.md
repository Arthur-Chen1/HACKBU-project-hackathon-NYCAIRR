# Forecast loading and per-building values

Investigation date: November 8, 2026. Scope: the dashboard's **Analyze buildings** action in forecast mode.

## Findings

The request follows `src/dashboard/server.py` → `main.run_night` → NYC building loading →
`building_analysis.analyze_forecast` → `src/features/daily.py` → HRRR and migration inputs →
`ForecastModel.score_rows` → lighting decisions and map output.

| Finding | Effect | Resolution |
| --- | --- | --- |
| This checkout has no `data/processed` directory or `models/forecast.joblib`. | No numeric expected collision counts are possible. Demo rules produce priorities only. | The dashboard warns before analysis; the job logs the missing model before loading buildings. See setup below. |
| Borough loading queried all citywide eligible PLUTO lots before selecting borough footprints. | Many sequential API pages precede any building calculation. The local cache contains 78 PLUTO responses / 77,515 rows and 56 footprint responses / 18,483 rows; these are cache totals, not a timed single run or unique-building count. | Filter numeric BBLs by borough in the PLUTO query. Citywide mode remains citywide. Report lot and footprint progress. |
| HRRR fetched each hourly index and six field ranges sequentially. | A 12-hour night can require 84 HTTP requests before scoring. | Download at most four hours concurrently, with a separate session per hour. Decode fields sequentially and reuse the existing nearest-grid lookup. |
| HRRR inherited long generic HTTP retries and timeouts. | An unavailable source could stall a forecast with little feedback. | Weather downloads use a 5-second connection timeout, 20-second read timeout, and one retry. These are request inactivity limits, not a total job deadline. |
| A missing hour could discard otherwise retrieved weather, while an absent ceiling field could look like clear sky. | Poor diagnostics and potentially misleading clear-sky assumptions. | Preserve complete hours, report unavailable leads, require every requested field in each index, and retain the full-night coverage gate. A present ceiling field with missing cell values still represents the existing clear-sky convention. |
| Dates beyond the selected HRRR run's full-night horizon still attempted partial downloads. | The downloaded partial night could never pass coverage validation. | Return explicit horizon/coverage issues without downloading those hours. |
| Migration estimation loaded training data and fitted models before checking weather coverage. | Failed weather could still trigger Dryad downloads and several model fits. | Validate regional weather first; close the training-data HTTP session after use. |
| Progress was sparse and clicking twice before the start response could queue duplicate jobs. | Analysis looked frozen, or repeated work waited on the shared worker. | Disable the button immediately; log queue status, source stages, hourly progress, scoring, building-load time, and total time. |

The forecast collision scorer already batches buildings. It does **not** make an API call for each
building. Weather is sampled at the nearest HRRR cell; multiple buildings can share weather.
The automatic migration estimate still trains on first use for a forecast year, then uses its existing
in-memory cache. First-use downloads, training, geometry processing, and network outages can still
take time. Borough query changes create new raw-cache keys; older cache files are retained.

## What makes a building value available

1. A compatible trained forecast artifact must exist.
2. The night must have a sunset and a decision time no later than one hour before sunset.
3. HRRR initialization must precede that decision by at least two hours, and required fields must
   cover every requested overnight hour at the building's sampled location.
4. Regional migration traffic must be finite and nonnegative, with an issue timestamp at or before
   the decision time. The manual/CSV forecast takes precedence over the automatic estimate.
5. The model needs visibility, wind components, migration, and seasonal/date inputs. A missing
   roof height or facade length invokes the existing night-level fallback; such a value is less
   building-specific and can be identical across buildings.

For the building fit, expected collisions are the modeled rate multiplied by footprint perimeter /
100 m. Roof height also changes the modeled rate. These are expected survey counts, not calibrated
collision probabilities. The model was trained on observed conditions as forecast proxies; adding
an API does not by itself validate real forecast accuracy.

Inspect `risk_reasons`, `risk_features`, `source_issues`, `hrrr_expected_hours`, and
`hrrr_required_field_hours` in building rows. The dashboard exposes reasons and coverage. Missing
values stay blank/null; they are not replaced with zero. No trained artifact was generated during
this investigation.

## Forecast setup

From the project root with the virtual environment active:

```sh
python -m src.ingest coverage
python -m src.ingest sites --crosswalk configs/study_building_bins.csv
python -m src.models train --mode forecast \
  --input data/processed/baseline.csv \
  --sites data/processed/site_features.csv \
  --output data/processed/models/forecast.joblib
python main.py
```

The first two commands download source data. `coverage` also checks the satellite catalog. Inspect
the generated model report for the `footprint` profile and held-out evaluation. A base-only model
cannot capture building height and facade differences. Reload the dashboard after training so its
setup message refreshes. The existing [model runbook](../MODEL_RUNBOOK.md) describes optional
augmented training data and the model's limitations.

## API integration request

**Status: documented follow-up, as requested by the user; no provider integration implemented.**

The current forecast path uses USNO astronomy and NOAA HRRR weather, but has no external migration
forecast API client. The fallback estimates migration from weather and earlier Dryad seasons. It is
not a provider-issued migration forecast and cannot cover every season or date reliably.

Requested implementation:

- Select an authorized provider and confirm endpoint, access requirements, license, coverage,
  forecast horizon, timestamps, and units. Do not assume that an informational forecast website
  exposes a usable public API.
- Put the client in `src/ingest/`, reuse cached retrieval metadata, and keep credentials outside
  source files. Add an example configuration in `configs/` and document setup there.
- Normalize provider output to `forecast_migration_birds_per_km_h`,
  `forecast_migration_issue_utc`, and `forecast_migration_region`, retaining provider provenance and
  the target night. Different units require a documented conversion; never relabel density as traffic.
- Join through `src/features/daily.py`. Preserve manual/CSV overrides and the decision-time gate.
  Label provider data and the optional estimated fallback distinctly.
- Report unavailable, stale, late, wrong-region, and insufficient-horizon responses explicitly.
  Do not manufacture per-building migration precision from a regional forecast.
- Add offline fixtures for complete, partial, expired, malformed, rate-limited, and timed-out
  responses. Save issued forecasts for later validation against surveys.

Acceptance: a valid provider forecast scores the same as equivalent supplied daily inputs;
invalid/unavailable responses remain unscored or explicitly use the labeled existing fallback;
retrieval happens once per region/night, never once per building.

## Validation

Run `python -m unittest discover -s tests -v`. New regressions exercise actual overlapping weather
download tasks, equality with sequential sampling, preserved incomplete-hour coverage, absent
ceiling-field detection, horizon rejection without downloads, skipping migration training for
incomplete weather, and borough filtering. Existing tests cover per-building height and facade
effects, missing-input gates, the night-level fallback, and dashboard payloads.

Validation is offline. Live API latency and a fully trained live forecast were not measured here.
Use the new progress messages and `building_load_seconds` / `elapsed_seconds` overview fields to
compare a first run and a cached run in the actual environment.
