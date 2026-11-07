"""Load NYC buildings and attach each one's migration conditions for a night.

This is the data-prep layer for the lighting judgement. It answers "what is this building, and
what are the birds doing around it tonight?" and leaves "what should it do with its lights?" to
the judgement step.

Usage:
    python building_analysis.py                          # buildings only, pilot area
    python building_analysis.py --night 2021-09-10       # join that night's radar migration
    python building_analysis.py --night 2021-09-10 --area midtown --fetch

Inputs:
    Buildings: NYC OTI Building Footprints + PLUTO land use, through src/ingest/nyc.py (cached
    under data/raw/nyc). The footprint fields are defined in the city's metadata repo:
    https://github.com/CityOfNewYork/nyc-geo-metadata/blob/main/Metadata/Metadata_BuildingFootprints.md
    That repo documents the datasets; the data itself is served by NYC Open Data (5zhs-2jue).
    Migration: migration_analysis.analyze(), from data/processed/radar/<night>/.

Functions
---------
load_buildings(bounds=PILOT_BOUNDS, cache=Path("data/raw")) -> list[dict]
    Downloads (or reads cached) footprints and land use inside bounds. One dict per building:
    {
      "bin": str,                      # NYC Building Identification Number, the building's ID
      "bbl": str | None,               # tax lot; None if the building spans several lots
      "name": str | None,              # e.g. "Empire State Building"; only well-known buildings
      "feature_code": int | None,      # 2100 = building; 1000 parking, 1003 placeholder, 5100 under construction...
      "construction_year": int | None,
      "land_use": int | None,          # PLUTO code, e.g. 4 = mixed res/commercial, 5 = office
      "land_use_label": str | None,
      "in_program": bool,              # program land use (LIGHTING_PROGRAM_LAND_USES) and a real building
      "roof_height_m": float | None,
      "height_class": "low" | "mid" | "tall" | "unknown",   # see HEIGHT_CLASSES
      "footprint_area_m2": float,
      "latitude": float, "longitude": float,  # point guaranteed inside the footprint
      "geometry": GeoJSON geometry dict,
    }

attach_migration(buildings, analysis) -> list[dict]
    Adds one night's migration values (from migration_analysis.analyze) to each building.
    Returns new dicts with every load_buildings field plus:
    {
      "night_date": date,
      "night_level": "high" | "medium" | "low" | "unknown",
      "night_traffic_birds_per_km_h": float | None,
      "night_direction_deg": float | None,
      "night_fraction_below_600m": float | None,
      "active_window_start_utc": datetime | None, "active_window_end_utc": datetime | None,
      "sunset_utc": datetime, "sunrise_utc": datetime,
      "cell_row": int | None, "cell_col": int | None,   # None when no radar cell covers it
      "cell_mean_birds_per_km2": float | None,
      "cell_peak_birds_per_km2": float | None,
      "cell_peak_scan_utc": datetime | None,
      "cell_relative_density": float | None,   # > 1 = more birds than a typical NYC cell
      "neighborhood_relative_density": float | None,  # 3x3-cell average; use this for decisions
      "radar_coverage": bool,                  # False: treat migration as unknown, not zero
    }

prepare(night=None, bounds=PILOT_BOUNDS, *, program_only=False, fetch=False) -> list[dict]
    Convenience wrapper: load_buildings, optionally keep only in_program buildings, and attach
    migration when a night is given. fetch=True runs the radar pipeline if that night is missing.

write(rows, name) -> (csv_path, geojson_path)
    Saves rows to data/processed/buildings/<name>.csv (no geometry) and <name>.geojson.

main() -> None
    Command-line interface. Prints counts by land use and height class, and the in-program
    buildings with the most birds, then writes the files.
"""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import migration_analysis
from src.features.training import write_csv, write_json
from src.ingest import nyc
from src.ingest.http import Cache

OUTPUT_DIR = Path("data/processed/buildings")
AREAS = {
    "pilot": nyc.PILOT_BOUNDS,                         # Lower Manhattan, ~1,400 buildings
    "midtown": (-73.995, 40.745, -73.970, 40.765),     # Dense, tall, brightly lit
}
LAND_USE_LABELS = {
    1: "One & two family", 2: "Multi-family walk-up", 3: "Multi-family elevator",
    4: "Mixed residential & commercial", 5: "Commercial & office", 6: "Industrial & manufacturing",
    7: "Transportation & utility", 8: "Public facilities & institutions", 9: "Open space",
    10: "Parking", 11: "Vacant land",
}
# The program targets commercial lighting. Add 8 (public facilities) to include institutions.
LIGHTING_PROGRAM_LAND_USES = {4, 5}
# Footprint FEATURE_CODE for a regular building (see nyc-geo-metadata). Parking structures, tanks,
# placeholders, skybridges, and construction sites are kept in the data but not in the program.
BUILDING_FEATURE_CODE = 2100


@dataclass(frozen=True)
class HeightClass:
    name: str
    min_m: float


# Most collisions happen low (lit glass near trees), but tall towers reach the migration layer.
HEIGHT_CLASSES = (HeightClass("tall", 150), HeightClass("mid", 40), HeightClass("low", 0))


def height_class(height_m):
    if height_m is None:
        return "unknown"
    return next(c.name for c in HEIGHT_CLASSES if height_m >= c.min_m)


def load_buildings(bounds=nyc.PILOT_BOUNDS, cache=Path("data/raw")):
    store = Cache(cache)
    try:
        features = nyc.buildings(store, bounds, set())
    finally:
        store.session.close()
    result = []
    for feature in features.values():
        p = feature["properties"]
        code = int(p["land_use"]) if p["land_use"] else None
        result.append({
            "bin": p["bin"], "bbl": p["bbl"], "name": p.get("name"),
            "feature_code": p.get("feature_code"),
            "construction_year": p.get("construction_year"),
            "land_use": code, "land_use_label": LAND_USE_LABELS.get(code),
            "in_program": code in LIGHTING_PROGRAM_LAND_USES and p.get("feature_code") == BUILDING_FEATURE_CODE,
            "roof_height_m": p["roof_height_m"], "height_class": height_class(p["roof_height_m"]),
            "footprint_area_m2": p["footprint_area_m2"],
            "latitude": p["latitude"], "longitude": p["longitude"],
            "geometry": feature["geometry"],
        })
    return sorted(result, key=lambda b: b["bin"])


def attach_migration(buildings, analysis):
    night = analysis["night"]
    window = night["active_window_utc"] or (None, None)
    shared = {
        "night_date": night["night_date"],
        "night_level": night["level"],
        "night_traffic_birds_per_km_h": night["mean_migration_traffic_birds_per_km_h"],
        "night_direction_deg": night["direction_deg"],
        "night_fraction_below_600m": night["fraction_below_600m"],
        "active_window_start_utc": window[0], "active_window_end_utc": window[1],
        "sunset_utc": night["sunset_utc"], "sunrise_utc": night["sunrise_utc"],
    }
    rows = []
    for building in buildings:
        index = migration_analysis.cell_index(building["latitude"], building["longitude"], analysis["bounds"])
        cell = analysis["cells"].get(index)
        rows.append({
            **building, **shared,
            "cell_row": index[0] if cell else None, "cell_col": index[1] if cell else None,
            "cell_mean_birds_per_km2": cell and cell["mean_birds_per_km2"],
            "cell_peak_birds_per_km2": cell and cell["peak_birds_per_km2"],
            "cell_peak_scan_utc": cell and cell["peak_scan_utc"],
            "cell_relative_density": cell and cell["relative_density"],
            "neighborhood_relative_density": cell and cell["neighborhood_relative_density"],
            "radar_coverage": cell is not None,
        })
    return rows


def prepare(night=None, bounds=nyc.PILOT_BOUNDS, *, program_only=False, fetch=False):
    buildings = load_buildings(bounds)
    if program_only:
        buildings = [b for b in buildings if b["in_program"]]
    if night is None:
        return buildings
    if fetch:
        migration_analysis.fetch(night)
    return attach_migration(buildings, migration_analysis.analyze(night))


def write(rows, name):
    csv_path, geojson_path = OUTPUT_DIR / f"{name}.csv", OUTPUT_DIR / f"{name}.geojson"
    write_csv(csv_path, [{k: v for k, v in row.items() if k != "geometry"} for row in rows])
    write_json(geojson_path, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": row["geometry"],
         "properties": {k: v for k, v in row.items() if k != "geometry"}} for row in rows]})
    return csv_path, geojson_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\nInputs:")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--night", type=date.fromisoformat, help="Local night to join, YYYY-MM-DD")
    parser.add_argument("--area", choices=AREAS, default="pilot", help="Preset map extent")
    parser.add_argument("--bounds", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                        help="Custom extent in WGS84; overrides --area")
    parser.add_argument("--program-only", action="store_true", help="Keep only commercial/mixed-use buildings")
    parser.add_argument("--fetch", action="store_true", help="Run the radar pipeline if the night is missing")
    args = parser.parse_args()

    rows = prepare(args.night, args.bounds or AREAS[args.area], program_only=args.program_only, fetch=args.fetch)
    in_program = [r for r in rows if r["in_program"]]
    print(f"{len(rows):,} buildings, {len(in_program):,} in the lighting program "
          f"(land use {sorted(LIGHTING_PROGRAM_LAND_USES)})")
    print("  Height classes:", dict(Counter(r["height_class"] for r in rows).most_common()))
    print("  Land use:", dict(Counter(r["land_use_label"] or "unknown" for r in rows).most_common(5)))

    if args.night:
        first = rows[0] if rows else {}
        covered = [r for r in in_program if r["radar_coverage"]]
        print(f"Night {args.night}: {first.get('night_level', 'n/a')} migration; "
              f"{len(covered):,} of {len(in_program):,} program buildings have radar data")
        top = sorted(covered, key=lambda r: -r["cell_mean_birds_per_km2"])[:5]
        for r in top:
            print(f"  BIN {r['bin']} {(r['name'] or '')[:24]:<24} {r['height_class']:>4} {r['roof_height_m'] or 0:5.0f} m  "
                  f"{r['cell_mean_birds_per_km2']:5.0f} birds/km2 ({r['cell_relative_density']:.1f}x typical)")

    name = f"{args.night or 'buildings'}_{args.area if not args.bounds else 'custom'}"
    csv_path, geojson_path = write(rows, name)
    print(f"Wrote {csv_path} and {geojson_path}")


if __name__ == "__main__":
    main()
