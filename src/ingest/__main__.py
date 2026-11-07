"""Run with python -m src.ingest from the project directory."""

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from requests import RequestException

from src.features.training import DAY, building_nights, date_coverage, write_csv, write_json
from src.ingest import dryad, hrrr, nyc, usno, viirs
from src.ingest.http import Cache

RADIANCE_COLUMNS = (
    "observed_neighborhood_radiance", "radiance_quality", "radiance_cloud_mask", "radiance_granule",
)


def coverage(cache, rows, output):
    labeled = [row for row in rows if row["observed_collision_count"] is not None]
    nights = {row["night_date"] for row in labeled}
    granules = viirs.granules_by_date(cache, min(nights) + DAY, max(nights) + DAY, nyc.CITY_BOUNDS)
    satellite_nights = {night for night in nights if night + DAY in granules}
    write_csv(output / "baseline.csv", labeled)
    report = {
        "grain": "one row per study building per NYC night",
        "dryad_version_id": dryad.VERSION,
        "baseline_rows": len(labeled),
        "conflicting_building_nights_excluded": len(rows) - len(labeled),
        "survey_and_observed_weather": date_coverage(nights),
        "with_satellite_catalog_record": date_coverage(satellite_nights),
        "missing_satellite_catalog_nights": sorted(nights - satellite_nights),
        "catalog_note": "CMR granule exists for the overnight UTC date. Pixel quality and building joins require pull.",
    }
    write_json(output / "baseline_coverage.json", report)
    print(f"{len(labeled):,} labeled building-nights across {len(nights)} nights ({min(nights)} to {max(nights)}).")
    print(f"{len(satellite_nights)} nights have a Black Marble catalog record. See {output / 'baseline_coverage.json'}.")


def _merge_night(rows, buildings, crosswalk, natural, light, forecasts):
    for survey in rows:
        bin_id = crosswalk.get(survey["study_building_id"])
        feature = buildings.get(bin_id)
        building = feature["properties"] if feature else {}
        point_key = bin_id if feature else None
        radiance = light.get(point_key, {})
        yield {
            **survey,
            "bin": bin_id,
            "bbl": building.get("bbl"),
            "land_use": building.get("land_use"),
            "roof_height_m": building.get("roof_height_m"),
            "footprint_area_m2": building.get("footprint_area_m2"),
            "feature_latitude": building.get("latitude", nyc.NYC_POINT[0]),
            "feature_longitude": building.get("longitude", nyc.NYC_POINT[1]),
            "feature_location": "matched_footprint" if feature else "nyc_reference_point",
            **natural,
            **{column: radiance.get(column) for column in RADIANCE_COLUMNS},
            **forecasts.get(point_key, {}),
        }


def pull(cache, rows, args):
    grouped = defaultdict(list)
    for row in rows:
        if args.start <= row["night_date"] <= args.end:
            grouped[row["night_date"]].append(row)
    if not grouped:
        raise ValueError("No monitored Dryad nights in the requested date range; run coverage for available dates")

    crosswalk = nyc.crosswalk(args.crosswalk)
    known_sites = {row["study_building_id"] for row in rows}
    if unknown := crosswalk.keys() - known_sites:
        raise ValueError(f"Unknown study building IDs in crosswalk: {', '.join(sorted(unknown))}")
    buildings = nyc.buildings(cache, args.bounds, set(crosswalk.values()))
    # Include NYC in the catalog search even when the requested footprint bounds are smaller.
    granules = {} if args.skip_satellite else viirs.granules_by_date(
        cache, min(grouped) + DAY, max(grouped) + DAY, nyc.CITY_BOUNDS,
    )
    output = args.output
    result = []
    for night, surveys in sorted(grouped.items()):
        print(f"Processing {night} ({len(surveys)} surveyed buildings)", file=sys.stderr, flush=True)
        natural = usno.natural_light(cache, night)
        points = {None: nyc.NYC_POINT}
        for survey in surveys:
            bin_id = crosswalk.get(survey["study_building_id"])
            if bin_id in buildings:
                props = buildings[bin_id]["properties"]
                points[bin_id] = (props["latitude"], props["longitude"])
        # Black Marble's NYC overnight overpass is on the UTC date of the following morning.
        day = natural["sunrise_utc"].date()
        light = viirs.neighborhood_light(cache, day, granules.get(day, ()), points)
        forecasts = {}
        if args.weather == "hrrr":
            forecasts = hrrr.weather_forecast(cache, natural["sunset_utc"], natural["sunrise_utc"], points)
        result.extend(_merge_night(surveys, buildings, crosswalk, natural, light, forecasts))

    labeled = [row for row in result if row["observed_collision_count"] is not None]
    with_natural_light = [row for row in labeled if row["moon_illum_fraction"] is not None
                          and row["moon_above_horizon_minutes"] is not None]
    with_light = [row for row in with_natural_light if row["observed_neighborhood_radiance"] is not None]
    with_buildings = [row for row in with_light if row["feature_location"] == "matched_footprint"]
    with_hrrr = [row for row in labeled if row.get("hrrr_expected_hours")
                 and row["hrrr_expected_hours"] == row["hrrr_required_field_hours"]]
    full = with_buildings
    if args.weather == "hrrr":
        full = [row for row in with_buildings if row["hrrr_expected_hours"]
                and row["hrrr_expected_hours"] == row["hrrr_required_field_hours"]]
    columns = list(result[0])
    write_csv(output / "training.csv", labeled, columns)
    write_csv(output / "training_complete.csv", full, columns)
    write_json(output / "buildings.geojson", {"type": "FeatureCollection", "features": list(buildings.values())})
    write_json(output / "coverage.json", {
        "grain": "one row per study building per NYC night",
        "date_range_requested": [args.start, args.end],
        "weather_mode": args.weather,
        "satellite_skipped": args.skip_satellite,
        "dryad_version_id": dryad.VERSION,
        "footprints": len(buildings),
        "verified_crosswalk_entries": len(crosswalk),
        "crosswalk_bins_without_footprints": sorted(set(crosswalk.values()) - buildings.keys()),
        "conflicting_building_nights_excluded": len(result) - len(labeled),
        "labeled_rows": len(labeled),
        "complete_rows": len(full),
        "labeled": date_coverage(row["night_date"] for row in labeled),
        "valid_natural_light": date_coverage(row["night_date"] for row in with_natural_light),
        "valid_artificial_and_natural_light": date_coverage(row["night_date"] for row in with_light),
        "with_hrrr": date_coverage(row["night_date"] for row in with_hrrr),
        "complete_building_features": date_coverage(row["night_date"] for row in full),
    })
    print(f"Wrote {len(labeled)} training rows and {len(full)} rows with all requested sources joined by building.")
    print(f"See {output / 'coverage.json'} for exact date windows.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("data/processed"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("coverage", help="Export the full baseline and check the satellite catalog")
    run = commands.add_parser("pull", help="Fetch NYC, Sun/Moon, satellite, migration, and weather data")
    run.add_argument("--start", type=date.fromisoformat, required=True, help="First local night, YYYY-MM-DD")
    run.add_argument("--end", type=date.fromisoformat, required=True, help="Last local night, inclusive")
    run.add_argument("--bounds", type=float, nargs=4, default=nyc.PILOT_BOUNDS,
                     metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="Footprint bounds in WGS84")
    run.add_argument("--crosswalk", type=Path, help="CSV with verified study_building_id and bin columns")
    run.add_argument("--skip-satellite", action="store_true", help="Keep a partial dataset without NASA tile downloads")
    run.add_argument("--weather", choices=["observed", "hrrr"], default="observed",
                     help="Use Dryad observations, or also pull archived HRRR forecasts")
    args = parser.parse_args()

    if args.command == "pull":
        if args.start > args.end:
            parser.error("--start must be on or before --end")
        west, south, east, north = args.bounds
        city_west, city_south, city_east, city_north = nyc.CITY_BOUNDS
        if not (city_west <= west < east <= city_east and city_south <= south < north <= city_north):
            parser.error("--bounds must be ordered west south east north and inside the NYC data bounds")

    cache = Cache(args.cache)
    try:
        rows = list(building_nights(dryad.surveys(cache)))
        if args.command == "coverage":
            coverage(cache, rows, args.output)
        else:
            pull(cache, rows, args)
    except (RequestException, OSError, ValueError) as error:
        parser.exit(1, f"Ingestion failed: {error}\n")
    finally:
        cache.session.close()


if __name__ == "__main__":
    main()
