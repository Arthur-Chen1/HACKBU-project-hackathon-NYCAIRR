"""Retrieve footprints and land use from NYC Open Data."""

import csv
import re
from collections import defaultdict
from itertools import count

from pyproj import Geod
from shapely import orient_polygons
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

SODA = "https://data.cityofnewyork.us/resource/"
CITY_BOUNDS = (-74.26, 40.49, -73.69, 40.92)
PILOT_BOUNDS = (-74.02, 40.70, -74.00, 40.72)
NYC_POINT = (40.7128, -74.0060)
GEOD = Geod(ellps="WGS84")
PAGE_SIZE = 1000


def crosswalk(path):
    if path is None:
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            site = row["study_building_id"].strip().lower()
            bin_id = row["bin"].strip()
            if not site or not re.fullmatch(r"[1-5]\d{6}", bin_id):
                raise ValueError("Crosswalk entries need a study_building_id and a seven-digit NYC BIN")
            if site in result or bin_id in result.values():
                raise ValueError("Crosswalk must map each study site to a unique BIN")
            result[site] = bin_id
    return result


def _pages(cache, dataset, columns, where, order):
    for offset in count(step=PAGE_SIZE):
        page = cache.json("nyc", SODA + dataset + ".json", params={
            "$select": columns, "$where": where, "$order": order,
            "$limit": PAGE_SIZE, "$offset": offset,
        })
        yield from page
        if len(page) < PAGE_SIZE:
            break


def buildings(cache, bounds, extra_bins):
    west, south, east, north = bounds
    ring = f"{west} {south},{east} {south},{east} {north},{west} {north},{west} {south}"
    where = f"intersects(the_geom,'POLYGON (({ring}))')"
    if extra_bins:
        where += " OR bin in (" + ",".join(f"'{bin_id}'" for bin_id in sorted(extra_bins)) + ")"

    parts = defaultdict(list)
    for row in _pages(cache, "5zhs-2jue", "bin,mappluto_bbl,height_roof,the_geom,objectid", where, "objectid"):
        bin_id = row.get("bin", "")
        if re.fullmatch(r"[1-5]\d{6}", bin_id) and bin_id[1:] != "000000" and row.get("the_geom"):
            parts[bin_id].append(row)

    bbls = sorted({row["mappluto_bbl"] for rows in parts.values() for row in rows
                   if re.fullmatch(r"\d{10}", row.get("mappluto_bbl", ""))})
    land_use = {}
    for start in range(0, len(bbls), 300):
        where = "bbl in (" + ",".join(bbls[start:start + 300]) + ")"
        # Socrata serializes PLUTO's numeric BBL with trailing decimal zeros.
        land_use.update((row["bbl"].partition(".")[0], row.get("landuse")) for row in
                        _pages(cache, "64uk-42ks", "bbl,landuse", where, "bbl"))

    result = {}
    for bin_id, rows in parts.items():
        geometry = unary_union([shape(row["the_geom"]) for row in rows])
        point = geometry.representative_point()
        heights = [float(row["height_roof"]) for row in rows if row.get("height_roof")]
        lots = {row["mappluto_bbl"] for row in rows if re.fullmatch(r"\d{10}", row.get("mappluto_bbl", ""))}
        bbl = next(iter(lots)) if len(lots) == 1 else None
        result[bin_id] = {
            "type": "Feature",
            "geometry": mapping(geometry),
            "properties": {
                "bin": bin_id, "bbl": bbl, "land_use": land_use.get(bbl),
                "latitude": point.y, "longitude": point.x,
                "roof_height_m": max(heights) * 0.3048 if heights else None,
                "footprint_area_m2": GEOD.geometry_area_perimeter(orient_polygons(geometry))[0],
            },
        }
    return result
