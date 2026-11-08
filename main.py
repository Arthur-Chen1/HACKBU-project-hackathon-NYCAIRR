"""Run the building-night collision model and lighting recommendations.

    python main.py
    python main.py --cli --start 2021-10-01 --area pilot
    python main.py --cli --start 2021-10-01 --cells 26,21 27,22   # specific ~1.1 km grid cells
    python main.py --cli --forecast --daily-inputs forecast.csv --operator-settings lighting.csv

    python main.py --cli --start 2021-09-10 --area queens         # a whole borough (program buildings)

Areas are a borough ("manhattan", "bronx", "brooklyn", "queens", "staten-island"), "citywide", a
neighborhood preset ("midtown", "pilot"), or a list of grid cells [[row, col], ...]. The grid is the
radar's ~1.1 km grid, so cell (row, col) here is cell (row, col) in migration_analysis.

The first run of a night downloads ~100 MB of NEXRAD radar (about a minute at hourly scans); later
runs read the cache in data/raw. A borough's first run also downloads its commercial footprints.
"""

import argparse
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from math import ceil
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # Data paths are relative to the project, wherever Run is pressed from.
sys.path.insert(0, str(ROOT))

try:
    import building_analysis
    import lighting_judgement
    import migration_analysis
    from src.features.daily import load_daily_inputs
    from src.features.model_inputs import NYC, parse_utc
    from src.recommendations.dimming import load_operator_settings
    from src.features import birds
    from src.ingest.nyc import CITY_BOUNDS
except ImportError as error:
    sys.exit(f"Missing dependency ({error.name}). Install them with:\n"
             "  python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")

MAX_NIGHTS = 31
MAX_CELLS = 9  # Dense Manhattan cells hold 1,000-2,000 buildings each.
GRID_BOUNDS = CITY_BOUNDS  # Same ~1.1 km grid as the radar cells in migration_analysis.
_buildings = {}  # Area label -> buildings, so a multi-night period loads footprints once.


def nights(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def grid_info():
    """The NYC selection grid: {"bounds", "lat_deg", "lon_deg", "rows", "cols", "max_cells"}."""
    west, south, east, north = GRID_BOUNDS
    return {"bounds": GRID_BOUNDS, "lat_deg": birds.GRID_LAT_DEG, "lon_deg": birds.GRID_LON_DEG,
            "rows": ceil((north - south) / birds.GRID_LAT_DEG), "cols": ceil((east - west) / birds.GRID_LON_DEG),
            "max_cells": MAX_CELLS}


def resolve_area(area):
    """An area name or grid cells [[row, col], ...] -> {"label", "bounds", "cells", "borough", "land_uses"}.

    Names are boroughs, "citywide", or neighborhood presets (building_analysis.REPLAY_AREAS). Borough
    and citywide areas load only lighting-program buildings, filtered by borough code. Cells need not
    touch. Raises ValueError for unknown names, too many cells, or cells outside NYC.
    """
    if isinstance(area, str):
        if area not in building_analysis.REPLAY_AREAS:
            raise ValueError(f"Unknown area {area!r}")
        borough = building_analysis.BOROUGH_CODES.get(area)
        large = borough is not None or area == "citywide"
        return {"label": area, "bounds": tuple(building_analysis.AREAS["citywide" if borough else area]),
                "cells": None, "borough": borough,
                "land_uses": building_analysis.LIGHTING_PROGRAM_LAND_USES if large else None}
    grid = grid_info()
    cells = {(int(r), int(c)) for r, c in area}
    if not cells or len(cells) > MAX_CELLS:
        raise ValueError(f"Select between 1 and {MAX_CELLS} grid cells")
    if any(not (0 <= r < grid["rows"] and 0 <= c < grid["cols"]) for r, c in cells):
        raise ValueError("Grid cell outside NYC")
    corners = [birds.cell_bounds(r, c, GRID_BOUNDS) for r, c in cells]
    bounds = (min(b[0] for b in corners), min(b[1] for b in corners),
              max(b[2] for b in corners), max(b[3] for b in corners))
    label = "grid-" + "_".join(f"{r}x{c}" for r, c in sorted(cells))
    return {"label": label, "bounds": bounds, "cells": cells, "borough": None, "land_uses": None}


def area_buildings(area, log=print, night=""):
    """Buildings for an area or grid selection, loaded once per area."""
    spec = resolve_area(area)
    if spec["label"] not in _buildings:
        log(f"{night}: loading {spec['label']} buildings")
        _buildings[spec["label"]] = building_analysis.load_buildings(
            spec["bounds"], land_uses=spec["land_uses"], borough=spec["borough"], log=log)
    buildings = _buildings[spec["label"]]
    if spec["cells"] is not None:  # The bounding box can include unselected cells; keep only selected ones.
        buildings = [b for b in buildings
                     if birds.cell_index(b["latitude"], b["longitude"], GRID_BOUNDS) in spec["cells"]]
    return spec, buildings


def overview(analysis, decisions, weather, *, forecast=False, model=None):
    n = analysis["night"]
    counts = Counter(d["tier"] for d in decisions)
    priorities = Counter(d["risk_priority"] for d in decisions)
    scored = sum(d["expected_collision_count"] is not None for d in decisions)
    if forecast and not model:
        note = ("No forecast AI model: estimates and priority are unavailable. Train it with "
                "python -m src.models train --mode forecast to get expected counts.")
    elif forecast:
        note = "" if scored else "Expected counts unavailable. See each building’s reasons and input coverage."
    elif n["scans_processed"] == 0:
        note = "No radar scans in the archive for this night"
    elif n["level"] == "unknown":
        note = "Radar profiles unusable"
    else:
        note = ""
    if not forecast and not scored:
        note = " · ".join(filter(None, (note, "Expected counts require an observed model and Dryad inputs.")))
    window = n["active_window_utc"]
    local = lighting_judgement._local
    return {
        "night": n["night_date"],
        "level": n["level"],
        "traffic_birds_per_km_h": n["mean_migration_traffic_birds_per_km_h"],
        "peak_birds_per_km2": n["peak_birds_per_km2"],
        "direction_deg": n["direction_deg"],
        "fraction_below_600m": n["fraction_below_600m"],
        "mean_height_m": n["mean_height_m"],
        "sunset_local": local(n["sunset_utc"]), "sunrise_local": local(n["sunrise_utc"]),
        "peak_local": local(n["peak_scan_utc"]),
        "active_window_local": f"{local(window[0])} to {local(window[1])}" if window else None,
        "scans_used": n["scans_used"], "scans_processed": n["scans_processed"],
        "weather": weather,
        "mode": "forecast" if forecast else "observed",
        "model": model,
        "decision_at_utc": n.get("decision_at_utc"),
        "buildings": len(decisions), "scored_buildings": scored,
        "max_expected_collision_count": max((d["expected_collision_count"] for d in decisions
                                             if d["expected_collision_count"] is not None), default=None),
        "priorities": dict(priorities),
        "tiers": {tier: counts[tier] for tier in ("off", "dim", "reduce", "none", "review", "not_in_program")},
        "note": note,
    }


def run_night(night, area=building_analysis.DEFAULT_AREA, weather="dryad", minutes=60, log=print, *,
              forecast=False, daily_inputs=None, operator_settings=None, model_path=None):
    started = perf_counter()
    model_text = building_analysis.forecast_model_summary(model_path) if forecast else None
    if forecast and not model_text:
        log("Forecast model missing: collision estimates and priority are unavailable. "
            "See README.md forecast setup.")
    spec, buildings = area_buildings(area, log, night)
    buildings_ready = perf_counter()
    log(f"{night}: {len(buildings):,} buildings loaded in {buildings_ready - started:.1f}s")
    if forecast:
        log(f"{night}: Sun, Moon and HRRR forecast")
        rows = building_analysis.analyze_forecast(buildings, night, daily_inputs or {}, model_path=model_path, log=log)
        first = rows[0] if rows else {}
        traffic = first.get("forecast_migration_birds_per_km_h")
        issue = parse_utc(first.get("forecast_migration_issue_utc"))
        decision_at = first.get("decision_at_utc")
        traffic = traffic if issue and decision_at and issue <= decision_at else None
        analysis = {"cells": {}, "night": {
            "night_date": night, "level": migration_analysis.level(traffic),
            "mean_migration_traffic_birds_per_km_h": traffic, "peak_birds_per_km2": None,
            "direction_deg": None, "fraction_below_600m": None, "mean_height_m": None,
            "sunset_utc": first.get("sunset_utc"), "sunrise_utc": first.get("sunrise_utc"),
            "peak_scan_utc": None, "active_window_utc": None, "scans_used": 0, "scans_processed": 0,
            "decision_at_utc": decision_at,
        }}
        covered = sum(bool(row.get("hrrr_expected_hours")) and
                      row["hrrr_expected_hours"] == row.get("hrrr_required_field_hours") for row in rows)
        weather_text = f"HRRR coverage for {covered:,} of {len(rows):,} buildings"
        if traffic is not None:
            weather_text += f" · migration forecast {traffic:,.0f} birds/km/h ({first.get('forecast_migration_region')})"
    else:
        log(f"{night}: radar")
        model_text = None
        migration_analysis.fetch(night, minutes)
        analysis = migration_analysis.analyze(night)
        rows = building_analysis.analyze_replay(buildings, analysis, weather=weather, model_path=model_path)
        first = rows[0] if rows else {}
        if weather == "dryad":
            weather_text = "Dryad observed (LaGuardia)" if first.get("observed_visibility_m") is not None else "Dryad unavailable"
        elif weather == "hrrr":
            weather_text = "HRRR forecast (replay context)" if first.get("hrrr_required_field_hours") else "HRRR unavailable"
        else:
            weather_text = "not used"
    settings = load_operator_settings(operator_settings, night) if operator_settings else {}
    decisions = lighting_judgement.model_decisions(rows, settings)
    summary = overview(analysis, decisions, weather_text, forecast=forecast, model=model_text)
    summary["elapsed_seconds"] = round(perf_counter() - started, 2)
    summary["building_load_seconds"] = round(buildings_ready - started, 2)
    log(f"{night}: {summary['scored_buildings']:,} expected counts for {len(rows):,} building-nights")
    log(f"{night}: completed in {summary['elapsed_seconds']:.1f}s")
    return {"night": night, "area": spec["label"], "overview": summary,
            "decisions": decisions, "rows": rows, "analysis": analysis}


def run_period(start, end, area=building_analysis.DEFAULT_AREA, weather="dryad", minutes=60, log=print, **options):
    results = []
    for night in nights(start, end):
        try:
            results.append(run_night(night, area, weather, minutes, log, **options))
        except Exception as error:  # Keep going so one bad night doesn't sink the period.
            log(f"{night}: failed ({error})")
            results.append({"night": night, "error": str(error)})
    return results


def validate(start, end, *, forecast=False):
    if start > end:
        raise ValueError("Start must be on or before end")
    if not forecast and end >= datetime.now(NYC).date():
        raise ValueError("Pick nights before today; the radar archive covers completed nights")
    if len(nights(start, end)) > MAX_NIGHTS:
        raise ValueError(f"Pick at most {MAX_NIGHTS} nights at a time")


def _print_table(results):
    print(f"\n{'Night':<11} {'Migration':<9} {'Scored':>7} {'High':>5} {'Medium':>6} "
          f"{'Low':>5} {'Review':>7}  Note")
    for result in results:
        if "error" in result:
            print(f"{result['night']!s:<11} error: {result['error']}")
            continue
        o = result["overview"]
        p = o["priorities"]
        print(f"{o['night']!s:<11} {o['level']:<9} {o['scored_buildings']:>7} {p.get('high', 0):>5} "
              f"{p.get('medium', 0):>6} {p.get('low', 0):>5} {o['tiers']['review']:>7}  {o['note']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cli", action="store_true", help="Print results instead of opening the dashboard")
    parser.add_argument("--start", type=date.fromisoformat, help="First NYC night (forecast defaults to today)")
    parser.add_argument("--end", type=date.fromisoformat, help="Last night, inclusive (default: --start)")
    parser.add_argument("--area", choices=building_analysis.REPLAY_AREAS, default=building_analysis.DEFAULT_AREA)
    parser.add_argument("--cells", nargs="+", metavar="ROW,COL",
                        help="Grid cells instead of --area, e.g. --cells 26,21 27,22 (see the dashboard grid)")
    parser.add_argument("--weather", choices=["none", "dryad", "hrrr"], default="dryad", help="Replay weather source")
    parser.add_argument("--forecast", action="store_true", help="Use pre-sunset forecasts with no radar observations")
    parser.add_argument("--daily-inputs", type=Path, help="Issued migration forecasts and optional prior radiance CSV")
    parser.add_argument("--operator-settings", type=Path, help="Operator lighting budget CSV")
    parser.add_argument("--model", type=Path, help="Override the model artifact for a CLI run")
    parser.add_argument("--minutes", type=int, default=60, help="Radar scan interval (30 = finer, slower)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Start the dashboard without opening a browser")
    args = parser.parse_args()

    if not args.cli:
        from src.dashboard.server import serve
        serve(args.port, open_browser=not args.no_browser, daily_inputs=args.daily_inputs,
              operator_settings=args.operator_settings)
        return
    start = args.start or (datetime.now(NYC).date() if args.forecast else date(2021, 9, 10))
    end = args.end or start
    try:
        validate(start, end, forecast=args.forecast)
        area = [cell.split(",") for cell in args.cells] if args.cells else args.area
        resolve_area(area)
        daily_inputs = load_daily_inputs(args.daily_inputs) if args.forecast else None
    except (OSError, ValueError) as error:
        parser.error(str(error))
    results = run_period(start, end, area, args.weather, args.minutes, forecast=args.forecast,
                         daily_inputs=daily_inputs, operator_settings=args.operator_settings, model_path=args.model)
    _print_table(results)
    for result in results:
        if "error" not in result:
            suffix = "_forecast" if args.forecast else ""
            name = f"{result['night']}_{result['area']}{suffix}"
            rows_path, _ = building_analysis.write(result["rows"], name)
            decisions_path, _ = lighting_judgement.write(result["decisions"], result["rows"], name)
            print(f"Saved {rows_path} and {decisions_path}")


if __name__ == "__main__":
    main()
