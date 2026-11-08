"""Run with python -m src.ingest from the project directory."""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from requests import RequestException

from src.features import birds
from src.features.training import DAY, building_nights, date_coverage, write_csv, write_json
from src.ingest import dryad, hrrr, nexrad, nyc, usno, viirs
from src.ingest.http import Cache

RADIANCE_COLUMNS = (
    "observed_neighborhood_radiance", "radiance_quality", "radiance_cloud_mask", "radiance_granule",
)


def coverage(cache, rows, output):
    """Export labeled Dryad rows and report which nights have NASA catalog entries."""
    labeled = [row for row in rows if row["observed_collision_count"] is not None]
    nights = {row["night_date"] for row in labeled}
    # Query the catalog for available tiles. The baseline CSV uses only Dryad data.
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
    """Attach the available building and environmental fields to each survey row."""
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
    """Add source data for the requested nights and report how much could be joined."""
    grouped = defaultdict(list)
    for row in rows:
        if args.start <= row["night_date"] <= args.end:
            grouped[row["night_date"]].append(row)
    if not grouped:
        raise ValueError("No monitored Dryad nights in the requested date range; run coverage for available dates")

    # A verified mapping lets us connect study site codes to actual NYC footprints.
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
        # Unmatched survey sites share the NYC reference point for gridded inputs.
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
    # Keep every labeled row in the main export and write fully joined rows separately.
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


def _cell_features(cells, bounds):
    by_cell = defaultdict(list)
    for cell in cells:
        if cell["birds_per_km2"] is not None:
            by_cell[cell["cell_row"], cell["cell_col"]].append(cell)
    for (row, col), samples in sorted(by_cell.items()):
        west, south, east, north = birds.cell_bounds(row, col, bounds)
        peak = max(samples, key=lambda cell: cell["birds_per_km2"])
        traffic = [cell["migration_traffic_birds_per_km_h"] for cell in samples
                   if cell["migration_traffic_birds_per_km_h"] is not None]
        yield {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [west, south], [east, south], [east, north], [west, north], [west, south]]]},
            "properties": {
                "cell_row": row, "cell_col": col, "scans": len(samples),
                "mean_birds_per_km2": sum(cell["birds_per_km2"] for cell in samples) / len(samples),
                "peak_birds_per_km2": peak["birds_per_km2"], "peak_scan_utc": peak["scan_time_utc"],
                "mean_migration_traffic_birds_per_km_h": sum(traffic) / len(traffic) if traffic else None,
            },
        }


def radar(cache, args):
    observed = {}
    if args.compare_dryad:
        observed = {row["night_date"]: row["observed_migration_birds_per_km_h"] for row in dryad.surveys(cache)}
    night = args.start
    while night <= args.end:
        natural = usno.natural_light(cache, night)
        found = nexrad.scans(cache.session, args.site, natural["sunset_utc"], natural["sunrise_utc"])
        chosen = nexrad.every(found, args.minutes)
        print(f"{night}: {len(chosen)} of {len(found)} {args.site} scans between sunset and sunrise",
              file=sys.stderr, flush=True)
        profiles, scans, cells = [], [], []
        for index, (when, url) in enumerate(chosen):
            try:
                volume = nexrad.read(cache, url)
            except (RequestException, OSError, ValueError, IndexError) as error:
                print(f"  skipped {when:%H:%M}Z: {error}", file=sys.stderr)
                continue
            swept = birds.sweeps(volume)
            layers = birds.profile(swept)
            scan = birds.summarize(layers)
            following = chosen[index + 1][0] if index + 1 < len(chosen) else natural["sunrise_utc"]
            scan_meta = {"scan_time_utc": when, "hours_represented": (following - when).total_seconds() / 3600}
            scans.append({**scan_meta, **scan})
            profiles.extend({"scan_time_utc": when, **layer} for layer in layers)
            cells.extend({"scan_time_utc": when, **cell} for cell in birds.grid(swept, layers, scan, args.bounds))
            print(f"  {when:%H:%M}Z  {scan['birds_per_km2']:7.1f} birds/km2  "
                  f"traffic {scan['migration_traffic_birds_per_km_h'] or 0:7.0f} birds/km/h", file=sys.stderr)

        folder = args.output / "radar" / night.isoformat()
        write_csv(folder / "profiles.csv", profiles)
        write_csv(folder / "scans.csv", scans)
        write_csv(folder / "cells.csv", cells)
        write_json(folder / "cells.geojson", {"type": "FeatureCollection",
                                              "features": list(_cell_features(cells, args.bounds))})
        complete = [scan for scan in scans if scan["profile_complete"]]
        traffic = [scan for scan in complete if scan["migration_traffic_birds_per_km_h"] is not None]
        peak = max(complete, key=lambda scan: scan["birds_per_km2"], default=None)
        moving = [scan for scan in traffic if scan["direction_deg"] is not None]
        summary = {
            "night_date": night, "site": args.site, "radar_location": nexrad.SITE_LOCATION.get(args.site),
            "sunset_utc": natural["sunset_utc"], "sunrise_utc": natural["sunrise_utc"],
            "scans_available": len(found), "scans_processed": len(scans), "scans_with_complete_profile": len(complete),
            "mean_birds_per_km2": sum(scan["birds_per_km2"] for scan in complete) / len(complete) if complete else None,
            "peak_birds_per_km2": peak["birds_per_km2"] if peak else None,
            "peak_scan_utc": peak["scan_time_utc"] if peak else None,
            "mean_migration_traffic_birds_per_km_h":
                sum(scan["migration_traffic_birds_per_km_h"] for scan in traffic) / len(traffic) if traffic else None,
            # Birds crossing a 1 km line over the night; each scan stands for the time until the next one.
            "nightly_traffic_birds_per_km": sum(scan["migration_traffic_birds_per_km_h"] * scan["hours_represented"]
                                                for scan in traffic) if traffic else None,
            "traffic_weighted_direction_deg": birds.mean_direction(moving),
            "dryad_observed_migration_birds_per_km_h": observed.get(night),
            "grid_bounds": list(args.bounds),
            "method": "vol2bird-style profile within 5-35 km of the radar, range-corrected to ~1 km NYC cells",
        }
        write_json(folder / "summary.json", summary)
        print(f"  wrote {folder}", file=sys.stderr)
        night += DAY


SITE_COLUMNS = ("roof_height_m", "footprint_area_m2", "footprint_perimeter_m", "construction_year", "land_use",
                "latitude", "longitude")


def sites(cache, rows, args):
    """Write one row of building traits per study site, for `python -m src.models train --sites`."""
    crosswalk = nyc.crosswalk(args.crosswalk)
    if not crosswalk:
        raise ValueError(f"{args.crosswalk} has no study sites")
    known_sites = {row["study_building_id"] for row in rows}
    if unknown := crosswalk.keys() - known_sites:
        raise ValueError(f"Unknown study building IDs in crosswalk: {', '.join(sorted(unknown))}")
    # The bounds are only a small default query; the crosswalk BINs are fetched wherever they are.
    buildings = nyc.buildings(cache, nyc.PILOT_BOUNDS, set(crosswalk.values()))
    if missing := sorted(bin_id for bin_id in crosswalk.values() if bin_id not in buildings):
        raise ValueError(f"No NYC footprint found for BIN(s) {', '.join(missing)}; check the crosswalk")
    # Table S2 "Distance (m)": facade length the volunteers surveyed, the exposure behind each count.
    with args.crosswalk.open(newline="", encoding="utf-8-sig") as file:
        surveyed = {row["study_building_id"].strip().lower(): row.get("survey_distance_m", "").strip()
                    for row in csv.DictReader(file)}
    output = []
    for site, bin_id in sorted(crosswalk.items()):
        properties = buildings[bin_id]["properties"]
        output.append({"study_building_id": site, "bin": bin_id, "name": properties.get("name"),
                       "feature_location": "matched_footprint",
                       "surveyed_facade_m": surveyed.get(site) or None,
                       **{column: properties.get(column) for column in SITE_COLUMNS}})
    write_csv(args.output / "site_features.csv", output)
    unmatched = sorted(known_sites - crosswalk.keys())
    print(f"Wrote building traits for {len(output)} study sites to {args.output / 'site_features.csv'}.")
    if unmatched:
        print(f"{len(unmatched)} study sites have no crosswalk entry and train without building traits: "
              f"{', '.join(unmatched)}")


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
    site_parser = commands.add_parser("sites", help="Write study-site building traits from a verified crosswalk")
    site_parser.add_argument("--crosswalk", type=Path, default=Path("configs/study_building_bins.csv"),
                             help="CSV with verified study_building_id and bin columns (from the study's Table S2)")
    sky = commands.add_parser("radar", help="Map migrating birds over NYC from NEXRAD radar, night by night")
    sky.add_argument("--start", type=date.fromisoformat, required=True, help="First local night, YYYY-MM-DD")
    sky.add_argument("--end", type=date.fromisoformat, help="Last local night, inclusive (default: --start)")
    sky.add_argument("--site", default=nexrad.SITE, help="NEXRAD site ID")
    sky.add_argument("--minutes", type=int, default=30, help="Process at most one volume scan per this many minutes")
    sky.add_argument("--bounds", type=float, nargs=4, default=nyc.CITY_BOUNDS,
                     metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="Grid bounds in WGS84")
    sky.add_argument("--compare-dryad", action="store_true",
                     help="Add the Dryad study's nightly migration estimate to summary.json")
    args = parser.parse_args()

    if args.command == "radar":
        args.end = args.end or args.start
        if args.start > args.end:
            parser.error("--start must be on or before --end")
        if args.minutes < 1:
            parser.error("--minutes must be at least 1")

    if args.command == "pull":
        if args.start > args.end:
            parser.error("--start must be on or before --end")
        west, south, east, north = args.bounds
        city_west, city_south, city_east, city_north = nyc.CITY_BOUNDS
        if not (city_west <= west < east <= city_east and city_south <= south < north <= city_north):
            parser.error("--bounds must be ordered west south east north and inside the NYC data bounds")

    cache = Cache(args.cache)
    try:
        if args.command == "radar":
            radar(cache, args)
            return
        # Coverage and pull start with the same Dryad building-night survey records.
        rows = list(building_nights(dryad.surveys(cache)))
        if args.command == "coverage":
            coverage(cache, rows, args.output)
        elif args.command == "sites":
            sites(cache, rows, args)
        else:
            pull(cache, rows, args)
    except (RequestException, OSError, ValueError) as error:
        parser.exit(1, f"Ingestion failed: {error}\n")
    finally:
        cache.session.close()


if __name__ == "__main__":
    main()
