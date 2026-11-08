# Weather-Triggered Light Pollution Interceptor

# DarkSky

<!-- Explain the problem and the decision the project will help building operators make. -->

Many birds migrate at night. Bright building lights can draw them off course and increase their chances of striking glass. This project will combine migration, weather, and lighting data to recommend **which NYC commercial buildings should dim their nonessential lights, by how much, and for what hours**. Heavy migration, low visibility, low cloud ceilings, and wind are signals to test, with effects that can vary by season. [NYC collision study](https://doi.org/10.1111/1365-2664.14590)

For the **hackathon demo**, replay a past migration night across all five boroughs. The dashboard shows eligible commercial and mixed-use buildings, the factors behind each lighting tier, and a demo dimming schedule. Midtown and Lower Manhattan are also available for smaller replays. The dashboard uses configurable rules. The separately trained collision model still uses the historical survey sites.

From the project directory, run `python main.py` to open the dashboard. Select a borough or one of the smaller Manhattan areas. Manhattan is selected by default. For a terminal replay, run `python main.py --cli --start 2021-09-10 --area queens`. The terminal also supports `--area citywide` for a combined export. Building footprints for commercial and mixed-use lots are cached for later replays.

## Data sources

<!-- Record each provider's API and the geographic or time scale it actually measures. -->

| Data | Chosen API or source | What to pull and how to use it |
| --- | --- | --- |
| NYC building layout and use | NYC Open Data Socrata API. [Building Footprints](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue/about_data) and [PLUTO](https://data.cityofnewyork.us/City-Government/Primary-Land-Use-Tax-Lot-Output-PLUTO-/64uk-42ks/about_data) | Get polygons and roof heights from `https://data.cityofnewyork.us/resource/5zhs-2jue.geojson`. Use the Building Identification Number (`bin`) to identify a building. Join `mappluto_bbl` to PLUTO's `bbl` from `/resource/64uk-42ks.json` to identify commercial and office lots. A lot can contain several buildings, so confirm use with pilot operators. |
| Artificial light | NASA Earthdata Common Metadata Repository (CMR) Search API. [API docs](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html) · [VIIRS Black Marble product](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2/) | Search `https://cmr.earthdata.nasa.gov/search/granules.json` for `short_name=VNP46A2`, Collection 2, NYC bounds, and a date range. Use Earthdata Login to download the files and keep quality flags alongside the radiance. Use valid prior observations to estimate neighborhood brightness at roughly 500 m resolution. Several buildings can share one pixel. |
| Natural light | U.S. Naval Observatory Sun and Moon API. [API docs](https://aa.usno.navy.mil/data/api) | Query `https://aa.usno.navy.mil/api/rstt/oneday` for NYC on both dates spanning the night. Use sunset, sunrise, civil twilight, moonrise, moonset, and the illuminated fraction of the Moon. Moon phase and time above the horizon are proxies for available moonlight. Use cloud cover as a separate weather input. |
| Bird migration and collision labels | Dryad REST API. [API docs](https://github.com/datadryad/dryad-app/blob/main/documentation/apis/README.md) · [NYC dataset](https://doi.org/10.5061/dryad.41ns1rnmw) | Under `https://datadryad.org/api/v2`, look up `/datasets/doi%3A10.5061%2Fdryad.41ns1rnmw` and pin the downloaded dataset version. Use `amt`, a nightly regional migration traffic estimate from the KOKX radar, and `count`, the number of birds found per building survey. The dataset includes spring and fall surveys from 2017–2021. For a live demo, enter a regional migration forecast manually and log its issue time. [BirdCast forecast information](https://birdcast.org/science-center/for-meteorologists/) |
| Bird migration patterns |  Nexrad (https://registry.opendata.aws/noaa-nexrad/) | Grab bird migration data to be used in migration_analysis.py |
| Weather forecasts and archived runs | NOAA High-Resolution Rapid Refresh (HRRR) through the public S3 API. [Archive and access instructions](https://registry.opendata.aws/noaa-hrrr-pds/) | Extract hourly wind, visibility, cloud ceiling, cloud cover, and precipitation for NYC from `s3://noaa-hrrr-bdp-pds`. For a forecast replay, select a run available before the decision time and summarize its hourly values over the target night. |
| Actual building lighting | Pilot operators' building-management system exports or API, paired with measurements of facade lighting | Log the normal schedule, allowed settings for each lighting zone, what actually ran, and a consistent measurement such as facade luminance or the fraction of windows illuminated. Use these logs to learn how a lighting change affects collision risk at a building. |

## Run the Python data pipeline

<!-- Start with the public baseline, then add sources to a short replay before doing a bulk pull. -->

From the project directory, use Python 3.11 or newer.

In VS Code on Windows, open the project folder, then open a PowerShell terminal and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt tzdata
.\.venv\Scripts\python.exe -m src.ingest coverage
.\.venv\Scripts\python.exe train.py
```

If `.venv` already exists, start with the install line. `tzdata` supplies the New York time-zone rules that Python needs on Windows.

On macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.ingest coverage
python train.py
```

`coverage` downloads the pinned Dryad release and checks NASA's public catalog. It writes `data/processed/baseline.csv` and `data/processed/baseline_coverage.json`. The baseline contains collision labels, regional migration, and observed weather from LaGuardia.

**The finest shared training interval is one NYC night.** Dryad contains 5,422 usable building-night labels across 560 monitored nights from **March 31, 2017 through November 14, 2021**, with gaps between and within seasons. The longest consecutive interval is **August 31 through November 14, 2021 (76 nights)**. NASA lists a tile for all 76 nights and for 559 of the 560 monitored nights overall. The catalog confirms file availability. The pull checks whether a building actually has a usable pixel, since clouds and quality flags can reduce coverage. [Dryad dataset](https://doi.org/10.5061/dryad.41ns1rnmw) · [NASA Black Marble product](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2/)

For a one-night replay without a NASA account, run the following. `--weather hrrr` adds hourly HRRR archive pulls and reduces them to nightly features. The replay uses a decision time one hour before sunset and allows two hours for the forecast run to be published. Leave out this option for larger runs that only need the observed weather included in Dryad.

```sh
python -m src.ingest pull --start 2021-10-01 --end 2021-10-01 --skip-satellite --weather hrrr
```

To include artificial light, set `EARTHDATA_TOKEN` in your shell to an Earthdata download token with LAADS access and omit `--skip-satellite`. The token stays out of the cache and exports. Black Marble tiles are roughly 40 MB each. The pipeline caches them and retains the quality flags alongside the sampled radiance. [Earthdata Login](https://urs.earthdata.nasa.gov/) · [LAADS download instructions](https://ladsweb.modaps.eosdis.nasa.gov/learn/download-files-using-edl-tokens/)

To join the surveyed buildings to actual footprints, supply `--crosswalk configs/study_building_bins.csv`. Create the CSV with the header `study_building_id,bin` and one verified study code and seven-digit BIN per row. The study's addresses are documented in its supplement. The default footprint query covers a small Lower Manhattan area and includes any verified BINs outside it. Use `--bounds WEST SOUTH EAST NORTH` to change the map extent. [Study and supplement](https://doi.org/10.1111/1365-2664.14590) · [NYC Building Footprints](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue/about_data)

`pull` writes the following files under `data/processed/`.

| File | Contents |
| --- | --- |
| `training.csv` | All usable labels for the requested nights, with available features. Blank fields represent missing values. Unmatched sites keep their study ID and use a shared NYC reference point for satellite and forecast features. |
| `training_complete.csv` | Rows with a matched footprint, valid natural and artificial light, and the requested weather coverage. |
| `buildings.geojson` | NYC footprints and available land-use attributes for the requested area and any verified BINs. |
| `coverage.json` | Row counts, missing footprint matches, and actual contiguous date windows at each stage of the join. |

To train with the expanded data, give `train.py` the joined CSV. It adds roof height, footprint area, moon illumination, time the Moon is above the horizon, and neighborhood radiance when those columns have values in the training period. Use a date range that spans both sides of the test cutoff. This PowerShell example keeps the expanded exports and model separate from the original baseline:

```powershell
.\.venv\Scripts\python.exe -m src.ingest --output data/processed/enriched pull --start 2020-09-01 --end 2021-11-14 --skip-satellite
.\.venv\Scripts\python.exe train.py --input data/processed/enriched/training.csv --output data/processed/enriched/collision_model.joblib
```

Add `--crosswalk configs/study_building_bins.csv` to the pull once you have verified survey-site matches. Set `EARTHDATA_TOKEN` and remove `--skip-satellite` to include nighttime radiance. The script prints which optional inputs it used.

Raw API responses and retrieval metadata are cached under `data/raw/`. To load the baseline in Python, use `pandas.read_csv("data/processed/baseline.csv", parse_dates=["night_date"])`. `observed_collision_count` is the target. Group train/test splits by night and building to account for shared inputs and repeated surveys. This dataset is for retrospective modeling. Same-night observed migration, weather, and satellite brightness become available after the pre-sunset decision, so a live alert model needs forecast-time replacements.

## Map migrating birds with NEXRAD radar

<!-- Finer than a regional nightly number: ~1 km cells over NYC, every scan, with altitude and direction. -->

`radar` reads NOAA NEXRAD Level II volume scans for the KOKX radar (Upton, NY) from the public `unidata-nexrad-level2` S3 bucket. It needs no account. For each night, from sunset to sunrise, it estimates how many birds are aloft, how high they fly, which way they head, and how the density varies across NYC.

```sh
python -m src.ingest radar --start 2021-09-10 --minutes 30 --compare-dryad
```

The method is a simplified version of the published vol2bird/bioRad approach. [vol2bird](https://doi.org/10.1098/rsif.2010.0116) · [bioRad](https://doi.org/10.1111/ecog.04028) · [range correction](https://doi.org/10.1111/2041-210X.13466)

1. **Separate birds from rain.** Gates with a correlation coefficient below 0.95 count as biological echoes. Gates without an echo count as zero birds.
2. **Build a vertical profile.** Gates 5–35 km from the radar give bird density in 200 m layers up to 3 km. A VAD fit to the radial velocities gives flight speed and direction. Layers whose velocity spread is below 2 m/s are treated as insects or rain.
3. **Correct for distance across NYC.** Over Manhattan, the lowest beam is about 1–1.6 km up. For each cell of about 1 km, the observed reflectivity is compared with what the beam would see under the profile. That ratio scales the profile's birds/km² to the cell.

The command writes these files under `data/processed/radar/<night>/`.

| File | Contents |
| --- | --- |
| `scans.csv` | Per scan: birds/km², migration traffic (birds/km/h), flight speed and direction, mean height, and the share of birds below 600 m |
| `profiles.csv` | Per scan and 200 m layer: birds/km³, flight vector, velocity spread, rain fraction |
| `cells.csv` | Per scan and grid cell: birds/km², traffic, range-correction factor, beam height, rain fraction |
| `cells.geojson` | Nightly mean and peak birds/km² per cell, as polygons for mapping or building joins |
| `summary.json` | Nightly mean and peak, total traffic, traffic-weighted direction, and the Dryad estimate when requested |

**Validation.** Nine fall 2021 nights were processed with hourly scans and compared with the Dryad study's independent nightly KOKX migration estimate. Spearman ρ = 0.93 across the 8 nights with radar values. Oct 10 was rained out. Busy nights matched within about 25% (Sep 10: 6,005 vs 4,945 birds/km/h; Oct 14: 3,453 vs 3,189). The radar directions were the expected fall SSW (208–220°). Rainy nights had few complete profiles and are reported as missing, not zero.

**Limits.** One radar about 95 km away cannot see birds below roughly 600 m over Manhattan. The low-altitude share comes from the profile near the radar and is assumed to hold over NYC. Cells resolve neighborhoods, not individual buildings, so building-level risk still depends on building height, glass, and lighting. Insects on warm nights and residual ground clutter can inflate estimates. KDIX (Fort Dix, NJ) is a second radar that can cross-check KOKX with `--site KDIX`. The archive supports replay and same-night nowcasts. A pre-sunset alert still needs a migration forecast, such as BirdCast.

## Training data and model

<!-- Define the label, the join keys, and what information will be available when an alert is issued. -->

Use **one row per building per migration night**, keyed by `bin` and `night_date`. Set `night_date` to the local date when the night starts in `America/New_York`. Store timestamps in UTC and map a morning survey to the preceding night using the dataset's survey-date convention. Use the study supplement's building addresses to crosswalk survey IDs to BINs and verify matches before joining. Keep the study IDs for the baseline if a match is unclear. [Dataset field definitions](https://doi.org/10.5061/dryad.41ns1rnmw) · [Study and supplement](https://doi.org/10.1111/1365-2664.14590)

Join regional migration by night and assign weather and satellite data by the grid cells that cover each building. Store the source version, observation or forecast time, and when each input was available. Preserve missing values and quality flags so gaps remain visible in both training and the dashboard.

| Column group | Example fields |
| --- | --- |
| Building | `bin`, `roof_height_m`, `footprint_area_m2`, `land_use` |
| Night and conditions | `night_date`, `decision_at_utc`, `season`, `observed_migration_birds_per_km_h`, `forecast_migration_birds_per_km_h`, `forecast_visibility_m`, `forecast_cloud_ceiling_m`, `forecast_wind_u_m_s`, `forecast_wind_v_m_s`, `moon_illum_fraction`, `moon_above_horizon_minutes` |
| Lighting | `observed_neighborhood_radiance`, `radiance_quality`; future inputs include `prior_neighborhood_radiance`, `scheduled_light_output`, `actual_light_output`, `dimming_reduction_pct` |
| Survey outcome | `survey_completed`, `survey_coverage` (when available), `observed_collision_count` |

1. **Train the retrospective baseline.** [`train.py`](train.py) uses logistic regression to estimate whether a survey finds at least one collision. It learns from season, observed migration traffic, visibility, cloud ceiling, wind, and temperature. See [how training and evaluation work](MODEL_DESIGN.md#what-does-trainpy-do). A future count model could estimate how many birds a survey might find.
2. **Train and test the forecast.** Rebuild forecast features from archived inputs available at the pre-sunset cutoff, using forecast weather, prior satellite observations, and migration forecasts or a separately trained migration model. Use same-night observed migration and weather for retrospective analysis. Reserve later seasons and a separate group of buildings for evaluation. Compare against the demo rule and a seasonal baseline using count error and the share of observed collisions on the nights and buildings flagged at a fixed alert budget.
3. **Measure how much dimming helps.** With participating buildings, compare approved lighting settings on matched nights, or randomize among approved settings when feasible. Record actual settings, weather, migration, and the results of consistent morning surveys. Use those observations to estimate how collision counts change with measured light output and report uncertainty before recommending a specific setting based on the model.

## Nightly recommendation

<!-- Separate a demo policy from a dimming level backed by pilot measurements. -->

1. **Rank buildings before sunset.** For the demo, flag nights with high regional migration traffic. Give higher priority to buildings in brighter neighborhoods where the forecast also shows poor visibility, low ceilings, or seasonally adverse winds. Use configurable thresholds and show the reasons for each flag. Mark the score as unavailable if a required feed is missing.
2. **Show a dimming scenario.** Use example reductions of 25%, 50%, or 100% from the scheduled output of eligible nonessential zones. For instance, a high-priority building could be shown with a **50% demo reduction from the end of evening civil twilight to the start of morning civil twilight**, if its eligible zones support that setting. Keep emergency, egress, and other required lights outside the eligible zones. Show the local start and end times, input timestamps, and whether the level came from a demo rule or a pilot model.
3. **Choose a level after the pilot.** Compare the scheduled setting with supported dimming levels. Recommend the smallest approved reduction that meets a pilot-defined collision-risk target with sufficient confidence. Send cases with too little evidence for operator review.

## Project files

| File or folder | What it does |
| --- | --- |
| [`src/ingest/__main__.py`](src/ingest/__main__.py) | Runs `coverage` and `pull`, joins records, and writes the datasets and coverage reports. |
| [`src/ingest/dryad.py`](src/ingest/dryad.py) | Reads the pinned study release and assigns morning surveys to the preceding NYC night. |
| [`src/ingest/nyc.py`](src/ingest/nyc.py) | Validates the survey-site-to-building mapping and joins NYC footprints with tax-lot land use. |
| [`src/ingest/hrrr.py`](src/ingest/hrrr.py) | Samples archived hourly NOAA forecasts near each site and summarizes conditions overnight. |
| [`src/ingest/usno.py`](src/ingest/usno.py) | Calculates overnight Sun and Moon features from U.S. Naval Observatory data. |
| [`src/ingest/viirs.py`](src/ingest/viirs.py) | Finds NASA Black Marble tiles and checks quality flags when sampling neighborhood radiance. |
| [`src/ingest/http.py`](src/ingest/http.py) | Handles downloads, retries, and the local cache. |
| [`src/features/training.py`](src/features/training.py) | Combines survey records by building and night, checks date coverage, and writes CSV and JSON files. |
| [`train.py`](train.py) | Reads the baseline CSV, trains the collision model, scores the held-out period, and saves the fitted model. |
| `data/raw/` and `data/processed/` | Download cache and generated datasets, coverage reports, maps, and model files. |
| [`main.py`](main.py) | Starts the dashboard or runs a historical replay in the terminal. |
| [`building_analysis.py`](building_analysis.py) and [`migration_analysis.py`](migration_analysis.py) | Load NYC footprints, summarize radar migration, and connect migration estimates to buildings. |
| [`lighting_judgement.py`](lighting_judgement.py) and [`src/dashboard/`](src/dashboard/) | Apply lighting rules and display the results. The dashboard uses these rules to assign tiers. |
| [`tests/test_ingest.py`](tests/test_ingest.py) | Tests the data pipeline. Run `python -m unittest discover -s tests` from the project directory with the project environment active. |

## Milestones

<!-- Use this checklist to track the demo and the work required for a pilot. -->

- [x] Download the Dryad dataset and export the supervised baseline and coverage report.
- [ ] Verify survey-to-BIN matches and replay one historical night with all five sources joined.
- [ ] Add neighborhood radiance and Sun/Moon data, then render the ranked map and demo dimming schedule.
- [x] Train the baseline on survey outcomes and evaluate it on a later year.
- [ ] Evaluate the model on buildings held out of training.
- [ ] Backtest with forecasts available at alert time, then run a pilot with measured lighting and standardized collision surveys.
