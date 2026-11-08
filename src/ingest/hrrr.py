"""Read only the selected forecast fields from NOAA's public HRRR archive."""

from collections import defaultdict
from datetime import timedelta
from math import isfinite
from statistics import mean

import numpy as np
from eccodes import codes_get, codes_get_array, codes_get_values, codes_new_from_message, codes_release
from requests import HTTPError
from scipy.spatial import cKDTree

ARCHIVE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HOUR = timedelta(hours=1)
EARTH_RADIUS_KM = 6371.0
MAX_SAMPLE_DISTANCE_KM = 10
FIELDS = {
    ("VIS", "surface"): ("forecast_visibility_m", min),
    ("HGT", "cloud ceiling"): ("forecast_cloud_ceiling_m", min),
    ("UGRD", "10 m above ground"): ("forecast_wind_u_m_s", mean),
    ("VGRD", "10 m above ground"): ("forecast_wind_v_m_s", mean),
    ("TCDC", "entire atmosphere"): ("forecast_cloud_cover_pct", mean),
    ("PRATE", "surface"): ("forecast_precip_rate_mm_h", mean),
}


def forecast_hours(sunset, sunrise, *, decision_at=None):
    cutoff = sunset - HOUR
    decision = cutoff if decision_at is None else min(cutoff, decision_at)
    # Allow two hours for publication of the initialized run and all its leads.
    run = (decision - 2 * HOUR).replace(minute=0, second=0, microsecond=0)
    if (sunrise - run) // HOUR > 18:
        # CONUS cycles at 00, 06, 12 and 18 UTC extend through lead 48.
        run -= (run.hour % 6) * HOUR
    valid = sunset.replace(minute=0, second=0, microsecond=0)
    if valid < sunset:
        valid += HOUR
    leads = range(int((valid - run) / HOUR), int((sunrise - run) / HOUR) + 1)
    return decision, run, leads


def _field_ranges(index):
    entries = [line.split(":") for line in index.splitlines() if line.strip()]
    for i, entry in enumerate(entries):
        field = tuple(entry[3:5])
        if field in FIELDS:
            start = int(entry[1])
            end = int(entries[i + 1][1]) - 1 if i + 1 < len(entries) else None
            yield field, (start, end)


def _unit_vectors(latitudes, longitudes):
    lat, lon = np.radians(latitudes), np.radians(longitudes)
    return np.c_[np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]


def _grid_cells(message, points):
    """Nearest grid index for every point, found once per run because every field shares the grid.

    Returns (keys, indices) for points within MAX_SAMPLE_DISTANCE_KM of a grid point. A KD-tree over
    the grid points near the request replaces a full-grid search for each point and field.
    """
    latitudes, longitudes = (np.array(values, dtype=float) for values in zip(*points.values()))
    grid_lat = codes_get_array(message, "latitudes")
    grid_lon = (codes_get_array(message, "longitudes") + 180) % 360 - 180
    margin = 0.5  # degrees; HRRR's ~3 km spacing puts a grid point well inside this
    nearby = np.flatnonzero((grid_lat >= latitudes.min() - margin) & (grid_lat <= latitudes.max() + margin)
                            & (grid_lon >= longitudes.min() - margin) & (grid_lon <= longitudes.max() + margin))
    if not len(nearby):
        return [], np.array([], dtype=int)
    chord, index = cKDTree(_unit_vectors(grid_lat[nearby], grid_lon[nearby])).query(_unit_vectors(latitudes, longitudes))
    distance_km = 2 * EARTH_RADIUS_KM * np.arcsin(np.minimum(chord / 2, 1))
    close = distance_km < MAX_SAMPLE_DISTANCE_KM
    return [key for key, ok in zip(points, close) if ok], nearby[index[close]]


def _sample(message, cells, field):
    """Decode one field once and read the value at every requested grid cell."""
    keys, indices = cells
    values = codes_get_values(message)[indices]
    missing = codes_get(message, "missingValue")
    scale = 3600 if field == ("PRATE", "surface") else 1  # kg m-2 s-1 to mm/h
    for key, value in zip(keys, values.tolist()):
        if isfinite(value) and value != missing:
            yield key, value * scale


def weather_forecast(cache, sunset, sunrise, points, *, decision_at=None):
    decision, run, leads = forecast_hours(sunset, sunrise, decision_at=decision_at)
    samples = defaultdict(lambda: defaultdict(list))
    cells = None
    for lead in leads:
        if lead > 48:
            break
        url = f"{ARCHIVE}/hrrr.{run:%Y%m%d}/conus/hrrr.t{run:%H}z.wrfsfcf{lead:02d}.grib2"
        try:
            index = cache.get("hrrr", url + ".idx").read_text()
        except HTTPError as error:
            if error.response.status_code == 404:
                continue
            raise
        for field, byte_range in _field_ranges(index):
            path = cache.get("hrrr", url, byte_range=byte_range, signature=b"GRIB")
            message = codes_new_from_message(path.read_bytes())
            try:
                if cells is None:
                    cells = _grid_cells(message, points)
                for key, value in _sample(message, cells, field):
                    samples[key][field].append(value)
            finally:
                codes_release(message)

    result = {}
    for key in points:
        fields = samples[key]
        values = {name: aggregate(fields[field]) if fields[field] else None
                  for field, (name, aggregate) in FIELDS.items()}
        # A cloud-free grid cell may have a missing ceiling. Report its coverage separately.
        required_hours = min(len(fields[field]) for field in FIELDS if field != ("HGT", "cloud ceiling"))
        result[key] = {
            "decision_at_utc": decision,
            "hrrr_run_utc": run,
            "hrrr_expected_hours": len(leads),
            "hrrr_required_field_hours": required_hours,
            "hrrr_ceiling_hours": len(fields["HGT", "cloud ceiling"]),
            **values,
        }
    return result
