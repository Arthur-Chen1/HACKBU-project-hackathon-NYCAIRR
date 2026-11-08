# DarkSky Migrations

DarkSky Migrations helps people explore bird migration around New York City and identify buildings where reducing unnecessary lighting may be useful.

The website runs on your computer. It can:

- Estimate bird collisions for individual buildings using migration, weather, and building information.
- Suggest a dimming percentage based on each building’s collision estimate.
- Show results on a map for a borough, neighborhood, or area you select.
- Explore past nights with radar data or estimate conditions for an upcoming night.
- Explain results through a clickable bird guide with a speech bubble and bird facts.

**Dimming percentages are planning suggestions, not proven reductions in collisions.** They apply to nonessential lighting only. The website does not control any lights.

## What you need

- The project folder, containing `main.py` and `requirements.txt`.
- Python 3.11 or newer installed on your computer.
- An internet connection and a web browser.

## First-time setup

These steps prepare the software and its prediction models. You only need to do them once. If someone has already set up this copy for you, go to [Start the website](#start-the-website).

### 1. Open the project folder in a command window

**Mac:** Open Terminal. Type `cd `, including the space, drag the project folder into the window, then press Enter.

**Windows:** Open the project folder in File Explorer. Type `cmd` into its address bar and press Enter. This opens Command Prompt in the correct folder.

Keep this window open. Copy the following commands into it, one line at a time, pressing Enter after each line. Wait for each command to finish before continuing.

### 2. Install the required software

On **Mac**, run:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

On **Windows**, run:

```bat
py -m venv .venv
.venv\Scripts\activate.bat
```

Then, on either system, run:

```sh
python -m pip install -r requirements.txt
```

This downloads the supporting software into the project folder. It may take several minutes.

### 3. Prepare the prediction models

Skip this step if `data/processed/models` already contains **both** `forecast.joblib` and `observed.json`. Otherwise, run:

```sh
python -m src.ingest coverage
python -m src.ingest sites --crosswalk configs/study_building_bins.csv
python -m src.models train --mode forecast --sites data/processed/site_features.csv
python -m src.models train --mode observed --sites data/processed/site_features.csv
```

These commands download study data and prepare predictions for forecasts and historical replays. Allow each command to finish. If one reports an error, resolve it before continuing.

## Start the website

Open a command window in the project folder as described above.

On **Mac**, run:

```sh
source .venv/bin/activate
python main.py
```

On **Windows**, run:

```bat
.venv\Scripts\activate.bat
python main.py
```

Your browser should open automatically. If it does not, open [the dashboard](http://127.0.0.1:8765/) in your browser.

**Keep the command window open while using the website.** To stop it, click that window and press **Ctrl+C**.

## Use the dashboard

1. **Choose a mode.** Use **Pre-sunset forecast** for an upcoming night, or **Historical replay** to explore a completed night. Start with one night. A forecast may be unavailable if the weather data does not reach that far ahead.
2. **Choose a date or preset.** Historical presets provide examples. In forecast mode, leave the optional migration forecast blank to use the automatic estimate.
3. **Choose an area.** Start with **Lower Manhattan** or **Midtown** for a smaller analysis. You can also choose a borough or select grid squares on the map.
4. Click **Analyze buildings**. Watch **Progress** while data loads. First-time downloads can take several minutes or longer; repeat runs reuse saved data.
5. Select a completed night, then click a building to see its suggested dimming percentage, collision estimate, and explanation.

Click the bird to open or close its guide. **Next fact** shows another bird fact. The × button or **Escape** closes the bubble.

## Understand the map

| Color | Meaning within the selected area and night |
| --- | --- |
| Red | Roughly the top 5% of suggested dimming amounts |
| Amber | The next 20% |
| Pale | The remaining buildings |
| Unavailable | There is not enough information to calculate an estimate |

Equal estimates stay together, so there may be fewer red buildings. Small selections may have a larger red percentage because buildings cannot be divided into fractions.

A suggestion of **“Dim by 50%”** means reducing nonessential light output by half. It does **not** mean 50% fewer collisions. The number is a rule of thumb driven by the collision estimate; it is not a measured ideal brightness for birds. A building’s color can change with the selected area, even when its suggested percentage stays the same.

## Common problems

| Problem | What to do |
| --- | --- |
| Python is not found | Install Python 3.11 or newer, then reopen the command window. |
| A command cannot find `main.py` or `requirements.txt` | Make sure the command window is open in the project folder. |
| The website will not open | Check that `python main.py` is still running, then try the dashboard link above. |
| Forecast model is missing | Complete step 3 of the first-time setup, restart the website, and run the analysis again. |
| An estimate is unavailable | Read the building’s explanation. Try a different night; missing source data is not treated as zero. |
| Loading seems slow | Check Progress and your internet connection. Try one night and a smaller area. |
| Changes are not appearing | Stop the website with Ctrl+C, start it again, reload the browser page, and rerun the analysis. |
| Installation or a download fails | Keep the error message and share it with the person maintaining the project. |

## More information

- [How dimming suggestions are calculated](docs/DIMMING_ESTIMATES.md)
- [Bird guide](docs/BIRD_GUIDE.md)
- [Forecast setup and data limitations](docs/FORECAST_ANALYSIS.md)
- [Technical project outline](OUTLINE.md)
