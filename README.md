# DarkSky Migrations

DarkSky Migrations helps NYC commercial building operators decide which buildings should dim eligible lights on high-risk migration nights, when to dim them, and by how much. Bright lights can draw nocturnal migrants toward buildings, where they can strike glass. The hackathon demo will replay a historical night for a small group of Manhattan buildings and show a ranked map, the reasons behind each ranking, and an illustrative dimming schedule. [NYC collision study](https://doi.org/10.1111/1365-2664.14590)

The working Python pipeline pulls and caches source data and exports building-night training rows. The risk model, ranked map, and dimming policy are planned. [Model and implementation guide](MODEL_DESIGN.md)

## Project layout

```text
README.md
MODEL_DESIGN.md           # Data contracts, model design, and next implementation steps
requirements.txt          # Dependencies for the ingestion pipeline
src/
  ingest/                 # CLI and source-specific API clients
  features/               # Survey deduplication, coverage, and export helpers
tests/                    # Offline ingestion and data-join tests
data/
  raw/                    # Cached API responses and retrieval metadata
  processed/              # Baseline CSV and coverage report
    replays/              # Separate folder for each saved replay
```

Keep verified building mappings and operator settings in `configs/` when available. Add `src/models/`, `src/recommendations/`, and `src/dashboard/` as those components are implemented. The data directories are local and ignored by Git. The pipeline creates them as needed.

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
- To match surveys to footprints, create `configs/study_building_bins.csv` with the header `study_building_id,bin` and supply it with `--crosswalk`. Verify the BINs using study addresses and NYC records. The default query covers Lower Manhattan and includes any verified BINs outside that area. Use `--bounds WEST SOUTH EAST NORTH` to change the map extent. [Study supplement](https://doi.org/10.1111/1365-2664.14590)

Each replay writes `training.csv`, `training_complete.csv`, `buildings.geojson`, and `coverage.json`. Start with `training.csv` and check the coverage report before selecting features. A replay without valid satellite samples or verified footprint matches can produce a header-only `training_complete.csv`. The existing `fall-2021` and `2021-10-01-hrrr` snapshots under `data/processed/replays/` are examples of partial coverage.

## Data sources

| Source | What it contributes |
| --- | --- |
| [Dryad NYC survey dataset](https://doi.org/10.5061/dryad.41ns1rnmw) | Building survey counts, regional KOKX radar migration observations, and overnight LaGuardia weather. |
| [NYC Building Footprints](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue/about_data) and [PLUTO](https://data.cityofnewyork.us/City-Government/Primary-Land-Use-Tax-Lot-Output-PLUTO-/64uk-42ks/about_data) | Footprint geometry and roof height by building identification number (BIN), with lot-level land use joined by BBL. |
| [NASA CMR](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html) and [VIIRS Black Marble](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2/) | Tile catalog and quality-checked neighborhood radiance at roughly 500 m resolution. |
| [USNO Sun and Moon API](https://aa.usno.navy.mil/data/api) | Sunset, sunrise, civil twilight, lunar illumination, and moonrise and moonset for NYC. |
| [NOAA HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/) | Archived hourly wind, visibility, cloud ceiling, cloud cover, and precipitation forecasts. |
| [BirdCast forecast information](https://birdcast.org/science-center/for-meteorologists/) and operator logs | Planned forecast and pilot inputs. Record regional migration forecasts with their issue times. Collect lighting schedules, eligible zones, actual settings, and measured light output from pilot operators. |

## Milestones

- [x] Export the labeled Dryad baseline and catalog coverage report.
- [x] Replay fall 2021 with Sun and Moon data and one historical night with archived HRRR.
- [ ] Verify study-to-BIN matches and replay a night with usable satellite samples.
- [ ] Train and evaluate the retrospective collision baseline.
- [ ] Show the ranked map and a configurable demo schedule.
- [ ] Backtest forecast-time scores and measure lighting effects with a pilot.
