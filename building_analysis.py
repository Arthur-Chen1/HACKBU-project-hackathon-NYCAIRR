"""Load NYC buildings and estimate collision counts for each building-night.

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
      "footprint_perimeter_m": float,  # street-level facade length; collision model exposure
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

from requests import RequestException

import migration_analysis
from src.features.daily import forecast_rows
from src.features.model_inputs import forecast_issues, natural_light_features, number, usable_radiance
from src.features.training import write_csv, write_json
from src.ingest import dryad, hrrr, nyc, usno
from src.ingest.http import Cache
from src.models.count import DEFAULT_FORECAST_MODEL, DEFAULT_OBSERVED_MODEL, load_model

OUTPUT_DIR = Path("data/processed/buildings")
RAW_DIR = Path("data/raw")
HIGH_EXPECTED_COUNT = 1.0
MEDIUM_EXPECTED_COUNT = 0.25
DEFAULT_AREA = "manhattan"
BOROUGH_CODES = {"manhattan": "1", "bronx": "2", "brooklyn": "3", "queens": "4", "staten-island": "5"}
AREAS = {
    "citywide": nyc.CITY_BOUNDS,                        # All five boroughs
    "pilot": nyc.PILOT_BOUNDS,                         # Lower Manhattan, ~1,400 buildings
    "midtown": (-73.995, 40.745, -73.970, 40.765),     # Dense, tall, brightly lit
}
DASHBOARD_AREAS = (*BOROUGH_CODES, "midtown", "pilot")
REPLAY_AREAS = (*DASHBOARD_AREAS, "citywide")
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


def load_buildings(bounds=nyc.PILOT_BOUNDS, cache=Path("data/raw"), *, land_uses=None, borough=None):
    store = Cache(cache)
    try:
        features = nyc.buildings(store, bounds, set(), land_uses=land_uses, borough=borough,
                                 footprint_workers=6 if land_uses is not None and bounds == nyc.CITY_BOUNDS else 1)
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
            "footprint_perimeter_m": p.get("footprint_perimeter_m"),
            "latitude": p["latitude"], "longitude": p["longitude"],
            "feature_location": "matched_footprint",
            "feature_latitude": p["latitude"], "feature_longitude": p["longitude"],
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


def _model(path, mode):
    if path is None:
        path = DEFAULT_FORECAST_MODEL if mode == "forecast" else DEFAULT_OBSERVED_MODEL
        if not path.is_file():
            return None
    model = load_model(path)
    if model.mode != mode:
        raise ValueError(f"Expected a {mode} model at {path}, found {model.mode}")
    return model


def _demo_priority(row):
    issues = list(forecast_issues(row))
    traffic = number(row.get("forecast_migration_birds_per_km_h"), 0)
    visibility = number(row.get("forecast_visibility_m"), 0)
    ceiling = number(row.get("forecast_cloud_ceiling_m"), 0)
    light = natural_light_features(row)
    if traffic is None:
        issues.append("forecast migration traffic")
    if visibility is None:
        issues.append("forecast visibility")
    if light["potential_moonlit_hours"] is None:
        issues.append("Sun and Moon coverage")
    if issues:
        return "unavailable", ("Missing or unusable inputs: " + ", ".join(dict.fromkeys(issues)),)

    priority = migration_analysis.level(traffic)
    reasons = [f"Issued migration forecast of {traffic:,.0f} birds/km/h"]
    if visibility < 8_000 or (ceiling is not None and ceiling < 1_000):
        reasons.append("Low visibility or low forecast cloud ceiling")
        if priority == "medium":
            priority = "high"
    reasons.append(f"{light['potential_moonlit_hours']:.1f} potential moonlit hours before cloud effects")
    radiance = usable_radiance(row, "forecast")
    if radiance is not None:
        reasons.append(f"Prior neighborhood radiance {radiance:g} (quality-checked satellite sample)")
    return priority, tuple(reasons)


def score_buildings(rows, model, *, mode):
    """Keep the building-night inputs, score, and actual fitted features in each output row."""
    scored = []
    for row in rows:
        expected, priority, features = None, "unavailable", ()
        basis = f"{mode} Poisson model"
        if model is None and mode == "forecast":
            basis = "forecast demo rules"
            priority, reasons = _demo_priority(row)
        elif model is None:
            reasons = ("Train an observed model with python -m src.models train",)
        else:
            expected = model.predict(row)
            if expected is None:
                missing = model.missing_inputs(row)
                reasons = ("Missing or unusable inputs: " + ", ".join(missing),) if missing else (
                    "No fitted model is available for these inputs",)
            else:
                features = tuple(model.features_for(row))
                priority = ("high" if expected >= HIGH_EXPECTED_COUNT else
                            "medium" if expected >= MEDIUM_EXPECTED_COUNT else "low")
                reasons = tuple(f"{field.replace('_', ' ')} {'raised' if effect > 0 else 'lowered'} the estimate"
                                for field, effect in model.contributions(row)[:3] if effect)
        scored.append({**row, "expected_collision_count": expected, "risk_priority": priority,
                       "risk_basis": basis, "risk_features": features,
                       "risk_reasons": (*row.get("source_issues", ()), *reasons)})
    return scored


def analyze_forecast(buildings, night, inputs, *, model_path=None, cache=RAW_DIR, now=None):
    model = _model(model_path, "forecast")
    rows = forecast_rows(buildings, night, inputs, cache, now=now)
    return score_buildings(rows, model, mode="forecast")


def analyze_replay(buildings, analysis, *, weather="dryad", model_path=None, cache=RAW_DIR):
    model = _model(model_path, "observed")
    rows = attach_migration(buildings, analysis)
    night = analysis["night"]["night_date"]
    observed, natural, issues, forecasts = {}, {}, [], {}
    store = Cache(cache)
    try:
        try:
            natural = usno.natural_light(store, night)
        except (RequestException, OSError, ValueError, KeyError):
            issues.append("USNO Sun and Moon data unavailable")
        if weather == "dryad":
            survey = next((r for r in dryad.surveys(store) if r["night_date"] == night), None)
            if survey:
                # Retrospective inputs match the study's source, even if the radar map uses a different estimator.
                observed = {key: survey[key] for key in ("season", *dryad.OBSERVED_COLUMNS)}
        elif weather == "hrrr" and rows:
            first = rows[0]
            forecasts = hrrr.weather_forecast(store, first["sunset_utc"], first["sunrise_utc"],
                                               {None: nyc.NYC_POINT})[None]
    finally:
        store.session.close()
    inputs = [{**row, **natural, **observed, **forecasts, "source_issues": tuple(issues)} for row in rows]
    return score_buildings(inputs, model, mode="observed")


def prepare(night=None, bounds=nyc.PILOT_BOUNDS, *, program_only=False, fetch=False,
            weather="dryad", model_path=None):
    buildings = load_buildings(bounds, land_uses=LIGHTING_PROGRAM_LAND_USES if program_only else None)
    if program_only:
        buildings = [b for b in buildings if b["in_program"]]
    if night is None:
        return buildings
    if fetch:
        migration_analysis.fetch(night)
    return analyze_replay(buildings, migration_analysis.analyze(night), weather=weather, model_path=model_path)


def write(rows, name):
    csv_path, geojson_path = OUTPUT_DIR / f"{name}.csv", OUTPUT_DIR / f"{name}.geojson"
    properties = [{k: v for k, v in row.items() if k != "geometry"} for row in rows]
    write_csv(csv_path, [{key: " | ".join(value) if isinstance(value, tuple) else value
                         for key, value in row.items()} for row in properties])
    write_json(geojson_path, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": row["geometry"], "properties": props}
        for row, props in zip(rows, properties)]})
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
    parser.add_argument("--model", type=Path, help="Override the observed model artifact")
    args = parser.parse_args()

    rows = prepare(args.night, args.bounds or AREAS[args.area], program_only=args.program_only,
                   fetch=args.fetch, model_path=args.model)
    in_program = [r for r in rows if r["in_program"]]
    print(f"{len(rows):,} buildings, {len(in_program):,} in the lighting program "
          f"(land use {sorted(LIGHTING_PROGRAM_LAND_USES)})")
    print("  Height classes:", dict(Counter(r["height_class"] for r in rows).most_common()))
    print("  Land use:", dict(Counter(r["land_use_label"] or "unknown" for r in rows).most_common(5)))

    if args.night:
        scored = [r for r in rows if r["expected_collision_count"] is not None]
        print(f"Night {args.night}: {len(scored):,} of {len(rows):,} building-nights scored")
        top = sorted(scored, key=lambda r: -r["expected_collision_count"])[:5]
        for r in top:
            print(f"  BIN {r['bin']} {r['name'] or ''}  {r['expected_collision_count']:.3f} expected collisions")

    name = f"{args.night or 'buildings'}_{args.area if not args.bounds else 'custom'}"
    csv_path, geojson_path = write(rows, name)
    print(f"Wrote {csv_path} and {geojson_path}")


if __name__ == "__main__":
    main()
