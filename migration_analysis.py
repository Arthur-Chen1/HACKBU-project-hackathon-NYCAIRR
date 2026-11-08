"""Summarize one night of NEXRAD bird radar into the migration signal building_analysis uses.

Usage:
    python migration_analysis.py 2021-09-10          # analyze an already-processed night
    python migration_analysis.py 2021-09-10 --fetch  # run the radar pipeline first if needed

Input: data/processed/radar/<night>/ from `python -m src.ingest radar`
(summary.json, scans.csv, cells.geojson). Times are UTC datetimes. "Night" is the local NYC date
when the night starts, so the night of 2021-09-10 runs from sunset Sep 10 to sunrise Sep 11.

Functions
---------
fetch(night, minutes=30, root=RADAR_DIR, cache=Path("data/raw")) -> None
    Downloads and processes the NEXRAD scans for one night, keeping one scan per `minutes`, and
    writes data/processed/radar/<night>/. Does nothing if that night is already processed.
    Needs network access; takes ~1 minute and ~100 MB per night at hourly scans.

level(traffic) -> str
    Classifies a nightly mean migration traffic (birds/km/h) as "high" (>= HIGH_TRAFFIC),
    "medium" (>= MEDIUM_TRAFFIC), "low", or "unknown" when traffic is None (usually rain).

analyze(night, root=RADAR_DIR) -> dict
    The main entry point. Reads a processed night and returns:
    {
      "night": {
        "night_date": date,
        "level": "high" | "medium" | "low" | "unknown",
        "mean_migration_traffic_birds_per_km_h": float | None,  # birds crossing 1 km per hour
        "nightly_traffic_birds_per_km": float | None,           # total across the night
        "peak_birds_per_km2": float | None,                     # densest scan, whole column of air
        "peak_scan_utc": datetime | None,
        "direction_deg": float | None,          # heading birds fly toward; 180 = south
        "fraction_below_600m": float | None,    # 0-1 share of birds at skyscraper height or lower
        "mean_height_m": float | None,
        "active_window_utc": (start, end) | None,  # scans >= ACTIVE_SHARE of the peak traffic
        "sunset_utc": datetime, "sunrise_utc": datetime,
        "scans_used": int,       # scans with a complete, rain-free profile
        "scans_processed": int,
      },
      "cells": {
        # Absolute birds/km2 per cell can be off by several times on nights when birds fly higher
        # or lower over NYC than near the radar (range correction). Comparisons between cells on
        # the same night are reliable, so decisions use the relative densities.
        (row, col): {            # ~1.1 km grid cell over NYC
          "mean_birds_per_km2": float,
          "peak_birds_per_km2": float,
          "peak_scan_utc": datetime,
          "relative_density": float | None,  # cell mean / median cell; > 1 = more birds than typical
          "neighborhood_relative_density": float | None,  # 3x3-cell (~3 km) average; less noisy
          "polygon": [[lon, lat], ...],      # closed ring, SW corner first
        },
      },
      "bounds": (west, south, east, north),  # the grid's extent, used by cell_for()
    }
    Raises FileNotFoundError if the night has not been processed (call fetch() first).
    Rain-out nights return level "unknown" and no cells. Treat them as unknown, not as zero birds.

cell_for(analysis, lat, lon) -> dict | None
    Returns the "cells" entry containing a point, such as a building's latitude and longitude.
    Returns None when the point is outside the grid or the cell had no usable radar data.

main() -> None
    Command-line interface. Prints the night's level, heading, altitude, active window, and the
    five densest cells.

Internal helpers: _value parses CSV text into None/bool/float/datetime, _scans reads scans.csv,
and _weighted returns a traffic-weighted mean of one scans.csv column.
"""

import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from types import SimpleNamespace

from src.features.birds import cell_index
from src.ingest.nyc import CITY_BOUNDS

RADAR_DIR = Path("data/processed/radar")
# Nightly mean migration traffic (birds crossing 1 km per hour). Busy fall 2021 nights ran
# ~3,000-6,000 and quiet ones under ~300, so these are starting points to tune.
HIGH_TRAFFIC = 2000
MEDIUM_TRAFFIC = 500
# Scans at or above this share of the night's peak traffic count as the active migration window.
ACTIVE_SHARE = 0.25


def fetch(night, minutes=30, root=RADAR_DIR, cache=Path("data/raw")):
    """Run the NEXRAD pipeline for one night unless its outputs already exist."""
    if (root / night.isoformat() / "summary.json").is_file():
        return
    from src.ingest.__main__ import radar
    from src.ingest.http import Cache
    from src.ingest.nexrad import SITE

    store = Cache(cache)
    try:
        radar(store, SimpleNamespace(start=night, end=night, site=SITE, minutes=minutes,
                                     bounds=CITY_BOUNDS, compare_dryad=False, output=root.parent))
    finally:
        store.session.close()


def _value(text):
    if text in ("", None):
        return None
    if text in ("True", "False"):
        return text == "True"
    try:
        return float(text)
    except ValueError:
        return datetime.fromisoformat(text)


def _scans(folder):
    with (folder / "scans.csv").open(newline="") as file:
        return [{key: _value(value) for key, value in row.items()} for row in csv.DictReader(file)]


def level(traffic):
    if traffic is None:
        return "unknown"
    return "high" if traffic >= HIGH_TRAFFIC else "medium" if traffic >= MEDIUM_TRAFFIC else "low"


def _weighted(scans, key):
    pairs = [(s["migration_traffic_birds_per_km_h"], s[key]) for s in scans if s[key] is not None]
    total = sum(weight for weight, _ in pairs)
    return sum(weight * value for weight, value in pairs) / total if total else None


def analyze(night, root=RADAR_DIR):
    folder = root / night.isoformat()
    if not (folder / "summary.json").is_file():
        raise FileNotFoundError(f"No radar output for {night}; run with --fetch or `python -m src.ingest radar`")
    summary = json.loads((folder / "summary.json").read_text())
    scans = [s for s in _scans(folder)
             if s["profile_complete"] and s["migration_traffic_birds_per_km_h"] is not None]
    traffic = summary["mean_migration_traffic_birds_per_km_h"]

    window = None
    if scans:
        peak = max(s["migration_traffic_birds_per_km_h"] for s in scans)
        active = [s for s in scans if peak and s["migration_traffic_birds_per_km_h"] >= ACTIVE_SHARE * peak]
        if active:
            window = (active[0]["scan_time_utc"],
                      active[-1]["scan_time_utc"] + timedelta(hours=active[-1]["hours_represented"]))

    features = json.loads((folder / "cells.geojson").read_text())["features"]
    typical = median(f["properties"]["mean_birds_per_km2"] for f in features) if features else None
    cells = {}
    for feature in features:
        p = feature["properties"]
        cells[p["cell_row"], p["cell_col"]] = {
            "mean_birds_per_km2": p["mean_birds_per_km2"],
            "peak_birds_per_km2": p["peak_birds_per_km2"],
            "peak_scan_utc": datetime.fromisoformat(p["peak_scan_utc"]),
            # >1 means more birds than a typical NYC cell tonight.
            "relative_density": p["mean_birds_per_km2"] / typical if typical else None,
            "polygon": feature["geometry"]["coordinates"][0],
        }
    # Cells are ~1.1 km but the radar beam is ~1.6 km wide over NYC, so single cells are noisy.
    for (row, col), cell in cells.items():
        around = [cells[r, c]["relative_density"] for r in range(row - 1, row + 2) for c in range(col - 1, col + 2)
                  if (r, c) in cells and cells[r, c]["relative_density"] is not None]
        cell["neighborhood_relative_density"] = sum(around) / len(around) if around else None

    return {
        "night": {
            "night_date": night,
            "level": level(traffic),
            "mean_migration_traffic_birds_per_km_h": traffic,
            "nightly_traffic_birds_per_km": summary["nightly_traffic_birds_per_km"],
            "peak_birds_per_km2": summary["peak_birds_per_km2"],
            "peak_scan_utc": summary["peak_scan_utc"] and datetime.fromisoformat(summary["peak_scan_utc"]),
            "direction_deg": summary["traffic_weighted_direction_deg"],
            # Share of birds below ~600 m, the height of NYC's tallest buildings.
            "fraction_below_600m": _weighted(scans, "fraction_below_600m"),
            "mean_height_m": _weighted(scans, "mean_height_m"),
            "active_window_utc": window,
            "sunset_utc": datetime.fromisoformat(summary["sunset_utc"]),
            "sunrise_utc": datetime.fromisoformat(summary["sunrise_utc"]),
            "scans_used": len(scans),
            "scans_processed": summary["scans_processed"],
        },
        "cells": cells,
        "bounds": tuple(summary.get("grid_bounds") or CITY_BOUNDS),
    }


def cell_for(analysis, lat, lon):
    """Migration values for the radar cell containing a point, or None if outside or missing."""
    return analysis["cells"].get(cell_index(lat, lon, analysis["bounds"]))


def main():
    # Show only the summary and usage in --help, not the full function reference.
    parser = argparse.ArgumentParser(description=__doc__.split("\nInput:")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("night", type=date.fromisoformat, help="Local night, YYYY-MM-DD")
    parser.add_argument("--fetch", action="store_true", help="Run the radar pipeline if outputs are missing")
    parser.add_argument("--minutes", type=int, default=30, help="Scan interval when fetching")
    parser.add_argument("--root", type=Path, default=RADAR_DIR)
    args = parser.parse_args()
    if args.fetch:
        fetch(args.night, args.minutes, args.root)
    result = analyze(args.night, args.root)

    n = result["night"]
    fmt = lambda x, spec=",.0f": "n/a" if x is None else format(x, spec)
    print(f"Night of {n['night_date']}: {n['level'].upper()} migration "
          f"({fmt(n['mean_migration_traffic_birds_per_km_h'])} birds/km/h, {n['scans_used']}/{n['scans_processed']} scans usable)")
    if n["level"] == "unknown":
        print("  No usable radar profiles (usually rain, or missing scans). Treat as unknown, not as zero birds.")
        return
    print(f"  Heading {fmt(n['direction_deg'])} deg, mean height {fmt(n['mean_height_m'])} m, "
          f"{fmt(n['fraction_below_600m'] and 100 * n['fraction_below_600m'])}% below 600 m")
    if n["active_window_utc"]:
        start, end = n["active_window_utc"]
        print(f"  Active window {start:%H:%M}Z to {end:%H:%M}Z, peak {n['peak_scan_utc']:%H:%M}Z")
    ranked = sorted(result["cells"].items(), key=lambda item: -item[1]["mean_birds_per_km2"])[:5]
    print(f"  {len(result['cells'])} cells; densest:")
    for (row, col), cell in ranked:
        (west, south), (east, north) = cell["polygon"][0], cell["polygon"][2]
        lat, lon = (south + north) / 2, (west + east) / 2
        print(f"    cell {row},{col} near ({lat:.3f}, {lon:.3f}): mean {cell['mean_birds_per_km2']:.0f} birds/km2, "
              f"{cell['relative_density']:.1f}x typical")


if __name__ == "__main__":
    main()
