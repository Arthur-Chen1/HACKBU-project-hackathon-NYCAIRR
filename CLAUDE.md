# CLAUDE.md

Project context for Claude. Treat this file as the primary reference for project goals and decisions; update it when decisions change.

## Project

**DarkSky Migrations – Weather-Triggered Light Pollution Interceptor**

Built for a **20-hour hackathon**. Prioritize a working demo over completeness: simple, readable Python; no over-engineering.

**Problem:** Billions of birds migrate at night and become disoriented by bright commercial building lights, leading to fatal collisions. Asking a city to dim lights every night of a ~3-month migration season is economically impossible.

**Idea:** Only trigger dimming on the nights that matter — when heavy migration coincides with risky weather (low visibility, low cloud ceilings, adverse winds) — and only for the buildings that matter most.

**Scope:** New York City.

## Planned code structure

Keep it flat and simple:

- `main.py` — entry point; ties data loading, prediction, and building recommendations together.
- Data analysis file(s) (e.g. `data_analysis.py`) — predict bird migration intensity/patterns for a given night.
- A building-recommendation file (name TBD) — identifies which buildings should turn off or reduce their lights.

**Tiered recommendations (leaning toward):** categorize each building by how much it should reduce lighting, e.g. *completely off* / *dim* / *no action*.

> Note: [README.md](README.md) contains a more detailed, longer-term plan (a `src/` package layout, Poisson model, pilot program, etc.). For the hackathon, the flat structure above takes precedence; use the README for data-source details and ideas.

## Data sources (not finalized)

- **Bird migration:** Leaning toward **BirdCast** (birdcast.org). Caveat: it gives regional migration forecasts/intensity, not precise bird locations, so building-level risk must come from combining regional intensity with building attributes (height, location, brightness) and weather.
  - The README also lists the Dryad NYC collision dataset (doi:10.5061/dryad.41ns1rnmw, KOKX radar migration traffic + building collision counts, 2017–2021) as a historical/training option.
- **NYC buildings:** Considering the City of New York's open-source geo metadata repo: https://github.com/CityOfNewYork/nyc-geo-metadata/tree/main. The README also lists NYC Open Data Building Footprints (`5zhs-2jue`, keyed by `bin`) and PLUTO (`64uk-42ks`, join on `bbl`) for heights and land use.
- **Weather / light (from README, optional):** NOAA HRRR for weather; NASA VIIRS Black Marble for artificial light; USNO API for sunset/twilight/moon.

When a data source decision is made, record it here.
