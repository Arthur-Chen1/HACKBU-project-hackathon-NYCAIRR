# DarkSky Migrations

DarkSky Migrations estimates how many bird collisions a morning survey might find at a building. It combines migration traffic, weather, building features, and available light data for each night. NYC building operators can use the collision forecasts and explicitly labeled dimming planning estimates to identify buildings for attention. Bright lights can draw nocturnal migrants toward buildings, where they can strike glass. [NYC collision study](https://doi.org/10.1111/1365-2664.14590)

The Python pipeline pulls and caches source data, exports building-night training rows, and fits two collision models. A Poisson regression scores historical replays. A gradient-boosted machine-learning model scores pre-sunset forecasts. [Model and implementation guide](MODEL_DESIGN.md)

## Project layout

```text
MODEL_DESIGN.md           # Data contracts, model design, and evaluation plan
MODEL_RUNBOOK.md          # What was trained, metrics, computational cost, and limits
docs/FORECAST_ANALYSIS.md # Forecast loading findings, setup, and API integration request
docs/PRIORITY_UI.md       # Priority cleanup history and compatibility notes
docs/DIMMING_ESTIMATES.md # Current dimming rule, map distribution, and evidence limits
configs/                  # Verified study-site-to-BIN crosswalk (from the study's Table S2)
main.py                   # Dashboard and command-line pipeline
building_analysis.py      # NYC building-night rows and scoring
lighting_judgement.py     # Lighting decisions and exports
migration_analysis.py     # Historical radar summaries
src/
  ingest/                 # CLI and source-specific API clients
  features/               # Survey deduplication, coverage, and feature joins
  models/                 # Train, evaluate, and save the collision model
  recommendations/        # Dimming scenarios for eligible zones
  dashboard/              # Local map and nightly recommendations
tests/                    # Offline tests
data/
  raw/                    # Cached API responses and retrieval metadata
  processed/              # Baseline data, saved models, replays, and predictions
```

Keep verified building mappings and operator settings in `configs/` when available. The data directories are local and ignored by Git. The pipeline creates them as needed.

## Run the pipeline

Use Python 3.11 or newer from the project root.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m src.ingest coverage
```

`coverage` reads the pinned Dryad release and checks NASA's public catalog. It writes `data/processed/baseline.csv` and `data/processed/baseline_coverage.json`. The saved baseline contains 5,422 labeled building-nights across 560 monitored nights from March 31, 2017 through November 14, 2021. The longest continuous window is August 31 through November 14, 2021 (76 nights).

Replay October 1, 2021 with archived HRRR weather and no NASA downloads. Choose a separate output folder to keep saved runs intact.

```sh
python -m src.ingest --output data/processed/replays/demo-2021-10-01 pull \
  --start 2021-10-01 --end 2021-10-01 --skip-satellite --weather hrrr
```

- `--start` and `--end` are inclusive NYC night dates. The default weather mode uses the observations included with Dryad. `--weather hrrr` also adds pre-sunset archived forecasts.
- For satellite sampling, set `EARTHDATA_TOKEN` in your shell to a download token with LAADS access and omit `--skip-satellite`. The cache retains the downloaded tiles and their retrieval metadata. [Earthdata Login](https://urs.earthdata.nasa.gov/) · [LAADS token instructions](https://ladsweb.modaps.eosdis.nasa.gov/learn/download-files-using-edl-tokens/)
- To match surveys to footprints, pass `--crosswalk configs/study_building_bins.csv`. The file maps 21 of the 27 study sites to verified BINs from the study's Table S2. [configs/README.md](configs/README.md) explains how the matches were checked and why six sites are excluded. The default query covers Lower Manhattan and includes any verified BINs outside that area. Use `--bounds WEST SOUTH EAST NORTH` to change the map extent. [Study supplement](https://doi.org/10.1111/1365-2664.14590)

Start with `training.csv` and check `coverage.json` before selecting features. The saved `fall-2021` and `2021-10-01-hrrr` examples both have partial coverage and a header-only `training_complete.csv`.

| Replay output | Contents |
| --- | --- |
| `training.csv` | All usable labels for the requested nights and their available features. Blanks represent missing values. Unmatched sites keep their study ID and use a shared NYC reference point for satellite and forecast features. |
| `training_complete.csv` | Rows with a matched footprint, valid natural and artificial light, and the requested weather coverage. |
| `buildings.geojson` | NYC footprints and available land-use attributes for the requested area and any verified BINs. |
| `coverage.json` | Row counts, missing footprint matches, and contiguous date windows at each stage of the join. |

## Use the dashboard

The [bird guide](docs/BIRD_GUIDE.md) explains the selected night's migration and dimming suggestions
and shares rotating bird facts. Click the bird to toggle its speech bubble; × or Escape closes it.

**Forecast setup:** numeric building estimates require a locally trained
`data/processed/models/forecast.joblib`. Without it collision and dimming estimates are unavailable.
See [forecast setup and loading diagnostics](docs/FORECAST_ANALYSIS.md) for training commands,
missing-input explanations, performance changes, and the requested migration API integration.

Run `python main.py` from the project root, or press Run on `main.py` in VS Code with the `.venv` interpreter selected. A local server starts at `http://127.0.0.1:8765/` and opens in the browser. It uses only the standard library, and the map tiles load from OpenStreetMap.

1. Choose **Historical replay** or **Pre-sunset forecast**, then a preset or an inclusive range of NYC nights, up to 31 nights before today. In forecast mode, you can type a regional migration forecast in birds/km/h, such as BirdCast's, with its New York issue time. It overrides the daily input file. Leave it blank and the forecast estimates migration from tonight's HRRR weather and past seasons. The **Forecast demo** presets replay 2021 nights with test migration values, so the forecast runs without any input files. The forecast AI model then estimates each building's expected collisions from HRRR weather, that forecast, the date, and the building's height and facade length.
2. Choose an **area**: a borough (Manhattan by default), a Manhattan neighborhood (Midtown or Lower Manhattan), or **Pick grid cells on map** to click up to 9 cells. Cells are the same ~1.1 km grid as the radar analysis and do not need to touch. Borough loads include only lighting-program buildings (land use 4 or 5).
3. Press **Analyze buildings**. Each night appears in the table as it finishes. The map now shows **suggested dimming**: red for roughly the largest 5% of cuts, amber for the next 20%, and pale for the rest. Tied groups cannot expand the red population. Select a building to see its suggested percentage, the AI collision estimate driving it, and its main factors. The number is an explicitly unvalidated planning rule for nonessential light output: `100 × expected collisions / (expected collisions + 0.25)`. It is not a measured ideal light level or a prediction of collisions prevented. Changing the area can change colors/ranks but not a building's percentage. See [dimming calculation and evidence limits](docs/DIMMING_ESTIMATES.md).

For the command line, use `python main.py --cli`. Use `--area` for a borough, `citywide`, `midtown`, or `pilot`. Use `--cells ROW,COL ...` for grid cells, as in `python main.py --cli --start 2021-09-10 --cells 26,21 27,22`. A new replay night downloads about 100 MB of radar, which takes about a minute. Cached nights load in seconds.

## Data sources

| Source | What it contributes |
| --- | --- |
| [Dryad NYC survey dataset](https://doi.org/10.5061/dryad.41ns1rnmw) | Building survey counts, regional KOKX radar migration observations, and overnight LaGuardia weather. |
| [NYC Building Footprints](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue/about_data) and [PLUTO](https://data.cityofnewyork.us/City-Government/Primary-Land-Use-Tax-Lot-Output-PLUTO-/64uk-42ks/about_data) | Footprint geometry and roof height by building identification number (BIN), with lot-level land use joined by BBL. |
| [NASA CMR](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html) and [VIIRS Black Marble](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2/) | Tile catalog and quality-checked neighborhood radiance at roughly 500 m resolution. |
| [USNO Sun and Moon API](https://aa.usno.navy.mil/data/api) | Sunset, sunrise, civil twilight, lunar illumination, and moonrise and moonset for NYC. |
| [NOAA HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/) | Archived hourly wind, visibility, cloud ceiling, cloud cover, and precipitation forecasts. |
| [NOAA NEXRAD archive](https://registry.opendata.aws/noaa-nexrad/) | Radar volume scans for historical migration maps. |
| [BirdCast forecast information](https://birdcast.org/science-center/for-meteorologists/) | Regional migration forecasts. Record their issue time and match their region and units to the model inputs. |
| Operator logs | Lighting schedules, eligible zones, approved reductions, actual settings, and measured building light output. |

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

## Train and replay the collision model

Use **one row per building per migration night**. Survey rows are keyed by `study_building_id` and `night_date`. Candidate scores use `bin` and `night_date`. The night starts on that date in `America/New_York` and the label comes from the following morning's survey. Use timezone-aware UTC timestamps and keep unknown values blank. [Dataset field definitions](https://doi.org/10.5061/dryad.41ns1rnmw) · [Study supplement](https://doi.org/10.1111/1365-2664.14590)

```sh
python -m src.ingest --output data/processed/replays/fall-2021 pull \
  --start 2021-08-31 --end 2021-11-14 --skip-satellite
python -m src.ingest sites --crosswalk configs/study_building_bins.csv

python -m src.models train \
  --input data/processed/baseline.csv \
  --augment data/processed/replays/fall-2021/training.csv \
  --sites data/processed/site_features.csv \
  --output data/processed/models/observed.json

python main.py --cli --start 2021-10-01 --area pilot
```

The regularized Poisson model estimates the count a comparable survey would find. The model artifact includes its feature schema, data coverage, and evaluation against a seasonal mean on held-out nights and sites. The retrospective baseline has migration and weather. The fall replay adds Sun and Moon features for 1,190 survey rows. `sites` attaches the roof height, footprint area, and surveyed facade length of each matched study building. The model learns building-level differences per 100 m of facade, and it scores each candidate building separately using that building's footprint perimeter as its facade length. On held-out study buildings, flagging each night's top 10% caught 39% of collisions, compared with 2% without building traits. Training takes under a second, and scoring runs at about 115,000 buildings per second. The training buildings are mostly Lower Manhattan towers, so treat scores for very different buildings as provisional. There are no artificial-light coefficients yet. Metrics, costs, and limits are in [MODEL_RUNBOOK.md](MODEL_RUNBOOK.md).

## Predict a current night

Forecasts use a gradient-boosted tree model (scikit-learn) in place of the regression. No history of issued forecasts joined to survey counts exists yet. It therefore trains on the study's observed migration and LaGuardia weather, treated as perfect forecasts. Train it on the same files as the observed model.

```sh
python -m src.models train --mode forecast \
  --input data/processed/baseline.csv \
  --augment data/processed/replays/fall-2021/training.csv \
  --sites data/processed/site_features.csv \
  --output data/processed/models/forecast.joblib

python main.py --cli --forecast --area pilot \
  --daily-inputs path/to/daily-inputs.csv \
  --operator-settings path/to/operator-settings.csv
```

On held-out study buildings, the model roughly halves the regression's Poisson deviance (4.33 vs 8.79). On held-out future nights, it catches 39% of collisions in each night's top 10%, compared with 27% for the regression. These holdouts contain no forecast error, so treat them as an upper bound. Outside the study buildings' range, such as buildings shorter than 27 m, it extrapolates with the regression's slope. A Lower Manhattan forecast night takes about 4 s with cached weather. Details, metrics, and limits are in [MODEL_RUNBOOK.md](MODEL_RUNBOOK.md#how-the-forecast-ai-model-works). Save the daily building-night exports and join completed survey counts by verified BIN and night to build a real forecast history for retraining.

Forecast mode defaults to tonight in New York. Use `--start` and `--end` for an inclusive range and `--model` to select a different forecast artifact. To use the same input files from the dashboard, launch `python main.py --daily-inputs path/to/daily-inputs.csv --operator-settings path/to/operator-settings.csv` and select forecast mode.

The daily input file accepts one regional migration forecast row per night with a blank `bin`. Use birds crossing one kilometer per hour and record the region and UTC issue time. Optional per-BIN rows can supply prior neighborhood radiance with a quality flag of `0`, its granule, and the UTC time it became available. Replace the illustrative values below with source data for the target night.

```csv
night_date,bin,forecast_migration_birds_per_km_h,forecast_migration_issue_utc,forecast_migration_region,prior_neighborhood_radiance,prior_radiance_quality,prior_radiance_available_at_utc,prior_radiance_granule
2026-10-01,,800,2026-10-01T16:00:00Z,NYC,,,,
2026-10-01,1000001,,,,12.5,0,2026-09-30T14:00:00Z,example-granule
```

The pipeline fetches USNO astronomy and HRRR weather and uses a cutoff of one hour before sunset or the actual run time if earlier. Each building's HRRR values come from its nearest grid cell, found once per run, with concurrent hourly downloads and progress reporting; actual loading time depends on cached data and source availability. Without an issued migration forecast, the pipeline estimates regional traffic from tonight's HRRR weather at the NYC reference point and the Dryad seasons before that year. It is usable from March 31 to June 7 and August 30 to November 14, and is less accurate than an issued forecast: a typical night is off by a factor of about 3. It is recalibrated so busy nights can reach high. In 2019–2021 tests it called 17 of 37 high nights high, and 14 of its 42 high calls were quiet nights. With no input files, `python main.py --cli --forecast` therefore still scores tonight in season. When `forecast.joblib` is missing, expected counts and dimming suggestions are unavailable. An expected collision count requires the trained forecast model and valid inputs. Dimming suggestions follow the explicit planning policy above; map colors use a sparse 5/20/75 distribution. Legacy priority fields share that ranking. The CLI saves building-night rows and lighting decisions as CSV and GeoJSON under `data/processed/buildings/` and `data/processed/judgements/`.

## Set a dimming target (command line only)

This existing operator-budget tool is separate from the website and does not estimate collision reductions. The website instead uses the separately approved [AI-driven planning rule](docs/DIMMING_ESTIMATES.md), which is not an operator-approved budget calculation.

Supply operator-approved settings to calculate a dimming scenario. Leave `night_date` blank for the default settings for a BIN or specify a date to override those settings. Separate zones and approved reductions with `|`. Report scheduled, fixed, and target output in the same unit. Include emergency, egress, and other required lighting in the fixed output.

```csv
bin,night_date,eligible_zones,approved_reductions_pct,scheduled_eligible_light_output,fixed_light_output,target_artificial_light_output,light_output_unit
1000001,,facade|decorative,0|25|50|100,80,20,60,lm
```

For a building in the lighting program with an available priority, the example selects a 50% reduction in the eligible zones from civil dusk to civil dawn. The scheduled 80 lm of eligible output falls to 40 lm, and the 20 lm of fixed output brings the total to the 60 lm operator cap. If the cap is unattainable at approved settings, the scenario calls for operator review. Satellite radiance describes neighborhood brightness. Estimating how a building's lighting changes collision risk requires a pilot that measures actual building light output and survey outcomes under approved settings.

## Milestones

- [x] Export the labeled Dryad baseline and catalog coverage report.
- [x] Replay fall 2021 with Sun and Moon data and one historical night with archived HRRR.
- [x] Verify study-to-BIN matches from Table S2 (21 of 27 sites) and train building-level coefficients.
- [ ] Replay a night with usable satellite samples and fit artificial-light coefficients.
- [x] Train and evaluate the retrospective collision baseline.
- [x] Map historical migration from NEXRAD radar and validate it against the study's nightly estimates.
- [x] Run the dashboard by borough, neighborhood, or selected grid cells.
- [ ] Add migration × weather interactions, day-of-year terms, and a negative binomial likelihood, as in the study's model.
- [x] Score daily inputs and calculate scenarios for approved lighting settings.
- [x] Replace the forecast regression with a gradient-boosted model and sample HRRR for every building in about a second.
- [ ] Backtest forecast-time scores and measure lighting effects with a pilot.
