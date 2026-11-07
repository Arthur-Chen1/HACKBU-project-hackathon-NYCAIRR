# Weather-Triggered Light Pollution Interceptor

<!-- Explain the problem and the decision the project will help building operators make. -->

Many birds migrate at night. Bright building lights can draw them off course and increase their chances of striking glass. This project will combine migration, weather, and lighting data to recommend **which NYC commercial buildings should dim their nonessential lights, by how much, and for what hours**. Heavy migration, low visibility, low cloud ceilings, and wind are signals to test, with effects that can vary by season. [NYC collision study](https://doi.org/10.1111/1365-2664.14590)

For the **hackathon demo**, replay a past migration night for a small group of Manhattan buildings. Show a ranked map, the factors behind each ranking, and a clearly labeled demo dimming schedule. The first version is advisory and uses configurable rules. A later version can learn collision risk and use pilot data to recommend building-specific dimming levels.

## Data sources

<!-- Record each provider's API and the geographic or time scale it actually measures. -->

| Data | Chosen API or source | What to pull and how to use it |
| --- | --- | --- |
| NYC building layout and use | NYC Open Data Socrata API. [Building Footprints](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue/about_data) and [PLUTO](https://data.cityofnewyork.us/City-Government/Primary-Land-Use-Tax-Lot-Output-PLUTO-/64uk-42ks/about_data) | Get polygons and roof heights from `https://data.cityofnewyork.us/resource/5zhs-2jue.geojson`. Use the Building Identification Number (`bin`) to identify a building. Join `mappluto_bbl` to PLUTO's `bbl` from `/resource/64uk-42ks.json` to identify commercial and office lots. A lot can contain several buildings, so confirm use with pilot operators. |
| Artificial light | NASA Earthdata Common Metadata Repository (CMR) Search API. [API docs](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html) · [VIIRS Black Marble product](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2/) | Search `https://cmr.earthdata.nasa.gov/search/granules.json` for `short_name=VNP46A2`, Collection 2, NYC bounds, and a date range. Use Earthdata Login to download the files and keep quality flags alongside the radiance. Use valid prior observations to estimate neighborhood brightness at roughly 500 m resolution. Several buildings can share one pixel. |
| Natural light | U.S. Naval Observatory Sun and Moon API. [API docs](https://aa.usno.navy.mil/data/api) | Query `https://aa.usno.navy.mil/api/rstt/oneday` for NYC on both dates spanning the night. Use sunset, sunrise, civil twilight, moonrise, moonset, and the illuminated fraction of the Moon. Moon phase and time above the horizon are proxies for available moonlight. Use cloud cover as a separate weather input. |
| Bird migration and collision labels | Dryad REST API. [API docs](https://github.com/datadryad/dryad-app/blob/main/documentation/apis/README.md) · [NYC dataset](https://doi.org/10.5061/dryad.41ns1rnmw) | Under `https://datadryad.org/api/v2`, look up `/datasets/doi%3A10.5061%2Fdryad.41ns1rnmw` and pin the downloaded dataset version. Use `amt`, a nightly regional migration traffic estimate from the KOKX radar, and `count`, the number of birds found per building survey. The dataset includes spring and fall surveys from 2017–2021. For a live demo, enter a regional migration forecast manually and log its issue time. [BirdCast forecast information](https://birdcast.org/science-center/for-meteorologists/) |
| Weather forecasts and archived runs | NOAA High-Resolution Rapid Refresh (HRRR) through the public S3 API. [Archive and access instructions](https://registry.opendata.aws/noaa-hrrr-pds/) | Extract hourly wind, visibility, cloud ceiling, cloud cover, and precipitation for NYC from `s3://noaa-hrrr-bdp-pds`. For a forecast replay, select a run available before the decision time and summarize its hourly values over the target night. |
| Actual building lighting | Pilot operators' building-management system exports or API, paired with measurements of facade lighting | Log the normal schedule, allowed settings for each lighting zone, what actually ran, and a consistent measurement such as facade luminance or the fraction of windows illuminated. Use these logs to learn how a lighting change affects collision risk at a building. |

## Training data and model

<!-- Define the label, the join keys, and what information will be available when an alert is issued. -->

Use **one row per building per migration night**, keyed by `bin` and `night_date`. Set `night_date` to the local date when the night starts in `America/New_York`. Store timestamps in UTC and map a morning survey to the preceding night using the dataset's survey-date convention. Use the study supplement's building addresses to crosswalk survey IDs to BINs and verify matches before joining. Keep the study IDs for the baseline if a match is unclear. [Dataset field definitions](https://doi.org/10.5061/dryad.41ns1rnmw) · [Study and supplement](https://doi.org/10.1111/1365-2664.14590)

Join regional migration by night and assign weather and satellite data by the grid cells that cover each building. Store the source version, observation or forecast time, and when each input was available. Preserve missing values and quality flags so gaps remain visible in both training and the dashboard.

| Column group | Example fields |
| --- | --- |
| Building | `bin`, `roof_height_m`, `footprint_area_m2`, `land_use` |
| Night and conditions | `night_date`, `decision_at_utc`, `season`, `observed_migration_birds_per_km_h`, `forecast_migration_birds_per_km_h`, `forecast_visibility_m`, `forecast_cloud_ceiling_m`, `forecast_wind_u_m_s`, `forecast_wind_v_m_s`, `moon_illum_fraction`, `moon_above_horizon_minutes` |
| Lighting | `prior_neighborhood_radiance`, `radiance_quality`, `scheduled_light_output`, `actual_light_output`, `dimming_reduction_pct` |
| Survey outcome | `survey_completed`, `survey_coverage` (when available), `observed_collision_count` |

1. **Build a retrospective baseline.** Use the Dryad dataset to train a regularized Poisson model for the number of collisions a survey finds. Start with season, migration traffic, and weather, and test whether building and light context improve the predictions. Use zero only for a completed survey that found zero birds. Keep unmonitored nights unlabeled, and account for survey effort when it is recorded. The surveyed buildings were selected for collision risk, so treat citywide scores as provisional.
2. **Train and test the forecast.** Rebuild forecast features from archived inputs available at the pre-sunset cutoff, using forecast weather, prior satellite observations, and migration forecasts or a separately trained migration model. Use same-night observed migration and weather for retrospective analysis. Reserve later seasons and a separate group of buildings for evaluation. Compare against the demo rule and a seasonal baseline using count error and the share of observed collisions on the nights and buildings flagged at a fixed alert budget.
3. **Measure how much dimming helps.** With participating buildings, compare approved lighting settings on matched nights, or randomize among approved settings when feasible. Record actual settings, weather, migration, and the results of consistent morning surveys. Use those observations to estimate how collision counts change with measured light output and report uncertainty before recommending a specific setting based on the model.

## Nightly recommendation

<!-- Separate a demo policy from a dimming level backed by pilot measurements. -->

1. **Rank buildings before sunset.** For the demo, flag nights with high regional migration traffic. Give higher priority to buildings in brighter neighborhoods where the forecast also shows poor visibility, low ceilings, or seasonally adverse winds. Use configurable thresholds and show the reasons for each flag. Mark the score as unavailable if a required feed is missing.
2. **Show a dimming scenario.** Use example reductions of 25%, 50%, or 100% from the scheduled output of eligible nonessential zones. For instance, a high-priority building could be shown with a **50% demo reduction from the end of evening civil twilight to the start of morning civil twilight**, if its eligible zones support that setting. Keep emergency, egress, and other required lights outside the eligible zones. Show the local start and end times, input timestamps, and whether the level came from a demo rule or a pilot model.
3. **Choose a level after the pilot.** Compare the scheduled setting with supported dimming levels. Recommend the smallest approved reduction that meets a pilot-defined collision-risk target with sufficient confidence. Send cases with too little evidence for operator review.

## Proposed project structure

<!-- These paths describe the planned implementation so the team can split up the work. -->

```text
configs/                # Pilot buildings, time window, risk thresholds, and approved settings
data/
  raw/                  # Versioned source snapshots and retrieval metadata
  processed/            # Cleaned building records and the joined building-night table
src/
  ingest/               # API clients and pilot lighting imports
  features/             # Spatial joins, night alignment, and forecast-time features
  models/               # Train, evaluate, and save the risk model
  recommendations/      # Rank buildings and apply approved dimming rules
  dashboard/            # Display the ranked map and nightly recommendations
tests/                  # Check joins, overnight dates, missing data, and dimming limits
```

## Milestones

<!-- Use this checklist to track the demo and the work required for a pilot. -->

- [ ] Download the Dryad dataset and replay one historical night with a small set of NYC building footprints.
- [ ] Add neighborhood radiance and Sun/Moon data, then render the ranked map and demo dimming schedule.
- [ ] Train the baseline on survey outcomes and evaluate it on held-out seasons and buildings.
- [ ] Backtest with forecasts available at alert time, then run a pilot with measured lighting and standardized collision surveys.
