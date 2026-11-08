"""Retrieve footprints and land use from NYC Open Data."""

import csv
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import count

from pyproj import Geod
from shapely import orient_polygons
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from src.ingest.http import Cache

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
            # Two sites may share a BIN: the study surveyed different facades of one building in
            # different seasons (1 Hotel Brooklyn Bridge Park is both "l" and "r").
            if site in result:
                raise ValueError(f"Crosswalk lists study site {site!r} more than once")
            result[site] = bin_id
    return result


def _pages(cache, dataset, columns, where, order, *, log=None):
    for offset in count(step=PAGE_SIZE):
        page = cache.json("nyc", SODA + dataset + ".json", params={
            "$select": columns, "$where": where, "$order": order,
            "$limit": PAGE_SIZE, "$offset": offset,
        })
        if log:
            log(f"NYC land use: loaded {offset + len(page):,} lot records")
        yield from page
        if len(page) < PAGE_SIZE:
            break


def _lot_filter(column, bbls, *, quoted=False, borough=None):
    # PLUTO stores BBL as a number. Building Footprints stores it as text.
    for start in range(0, len(bbls), 300):
        values = bbls[start:start + 300]
        # Ignore batches that cannot contain the requested borough.
        if borough is not None and not values[0][0] <= borough <= values[-1][0]:
            continue
        if quoted:
            values = [f"'{bbl}'" for bbl in values]
        yield f"{column} in ({','.join(values)})"


def buildings(cache, bounds, extra_bins, *, land_uses=None, borough=None, footprint_workers=1, log=None):
    log = log or (lambda _: None)
    west, south, east, north = bounds
    ring = f"{west} {south},{east} {south},{east} {north},{west} {north},{west} {south}"
    where = f"intersects(the_geom,'POLYGON (({ring}))')"
    if extra_bins:
        where += " OR bin in (" + ",".join(f"'{bin_id}'" for bin_id in sorted(extra_bins)) + ")"

    # For a citywide commercial replay, select eligible lots before downloading geometry.
    land_use = {}
    footprint_filters = [where]
    if land_uses is not None:
        codes = ",".join(f"'{code}'" for code in sorted(land_uses))
        lot_where = f"landuse in ({codes})"
        if borough is not None:
            if borough not in {"1", "2", "3", "4", "5"}:
                raise ValueError("Expected a borough code from 1 to 5")
            # BBL is numeric in PLUTO; its leading digit identifies the borough.
            lot_where += f" AND bbl >= {int(borough) * 10**9} AND bbl < {(int(borough) + 1) * 10**9}"
        for row in _pages(cache, "64uk-42ks", "bbl,landuse", lot_where, "bbl", log=log):
            bbl = row["bbl"].partition(".")[0]
            if re.fullmatch(r"\d{10}", bbl):
                land_use[bbl] = row["landuse"]
        footprint_filters = [f"({where}) AND {lots}" for lots in
                             _lot_filter("mappluto_bbl", sorted(land_use), quoted=True, borough=borough)]

    parts = defaultdict(list)
    # Field definitions: github.com/CityOfNewYork/nyc-geo-metadata, Metadata_BuildingFootprints.md
    columns = "bin,mappluto_bbl,height_roof,the_geom,objectid,name,feature_code,construction_year,ground_elevation"
    log(f"NYC footprints: loading {len(footprint_filters):,} batches")
    def collect(footprints):
        for row in footprints:
            bin_id = row.get("bin", "")
            if borough is not None and not bin_id.startswith(borough):
                continue
            if re.fullmatch(r"[1-5]\d{6}", bin_id) and bin_id[1:] != "000000" and row.get("the_geom"):
                parts[bin_id].append(row)

    if footprint_workers > 1 and len(footprint_filters) > 1:
        # Each lot batch has its own request keys and its own HTTP session.
        def fetch_batch(footprint_filter):
            batch_cache = Cache(cache.root)
            try:
                return list(_pages(batch_cache, "5zhs-2jue", columns, footprint_filter, "objectid"))
            finally:
                batch_cache.session.close()

        with ThreadPoolExecutor(max_workers=footprint_workers) as pool:
            for completed, batch in enumerate(pool.map(fetch_batch, footprint_filters), 1):
                collect(batch)
                log(f"NYC footprints: loaded {completed}/{len(footprint_filters)} batches")
    else:
        for footprint_filter in footprint_filters:
            collect(_pages(cache, "5zhs-2jue", columns, footprint_filter, "objectid"))

    if land_uses is None:
        bbls = sorted({row["mappluto_bbl"] for rows in parts.values() for row in rows
                       if re.fullmatch(r"\d{10}", row.get("mappluto_bbl", ""))})
        for lot_filter in _lot_filter("bbl", bbls):
            land_use.update((row["bbl"].partition(".")[0], row.get("landuse")) for row in
                            _pages(cache, "64uk-42ks", "bbl,landuse", lot_filter, "bbl"))

    result = {}
    log(f"Preparing geometry and height for {len(parts):,} buildings")
    for bin_id, rows in parts.items():
        geometry = unary_union([shape(row["the_geom"]) for row in rows])
        point = geometry.representative_point()
        heights = [float(row["height_roof"]) for row in rows if row.get("height_roof")]
        lots = {row["mappluto_bbl"] for row in rows if re.fullmatch(r"\d{10}", row.get("mappluto_bbl", ""))}
        bbl = next(iter(lots)) if len(lots) == 1 else None
        codes = {int(row["feature_code"]) for row in rows if str(row.get("feature_code", "")).isdigit()}
        years = [int(row["construction_year"]) for row in rows
                 if str(row.get("construction_year", "")).isdigit() and int(row["construction_year"]) > 0]
        grounds = [float(row["ground_elevation"]) for row in rows if row.get("ground_elevation")]
        area, perimeter = GEOD.geometry_area_perimeter(orient_polygons(geometry))
        result[bin_id] = {
            "type": "Feature",
            "geometry": mapping(geometry),
            "properties": {
                "bin": bin_id, "bbl": bbl, "land_use": land_use.get(bbl),
                "latitude": point.y, "longitude": point.x,
                "roof_height_m": max(heights) * 0.3048 if heights else None,
                "footprint_area_m2": area,
                # Facade length at street level; the collision model uses it as exposure.
                "footprint_perimeter_m": perimeter,
                "name": next((row["name"].strip() for row in rows if row.get("name", "").strip()), None),
                # 2100 = building; others are parking, tanks, placeholders, skybridges, etc.
                "feature_code": 2100 if 2100 in codes else min(codes) if codes else None,
                "construction_year": min(years) if years else None,
                "ground_elevation_m": min(grounds) * 0.3048 if grounds else None,
            },
        }
    return result
