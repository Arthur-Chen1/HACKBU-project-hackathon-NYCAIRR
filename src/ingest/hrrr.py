"""Read only the selected forecast fields from NOAA's public HRRR archive."""

from collections import defaultdict
from datetime import timedelta
from math import isfinite
from statistics import mean

from eccodes import codes_get, codes_grib_find_nearest_multiple, codes_new_from_message, codes_release
from requests import HTTPError

ARCHIVE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HOUR = timedelta(hours=1)
FIELDS = {
    ("VIS", "surface"): ("forecast_visibility_m", min),
    ("HGT", "cloud ceiling"): ("forecast_cloud_ceiling_m", min),
    ("UGRD", "10 m above ground"): ("forecast_wind_u_m_s", mean),
    ("VGRD", "10 m above ground"): ("forecast_wind_v_m_s", mean),
    ("TCDC", "entire atmosphere"): ("forecast_cloud_cover_pct", mean),
    ("PRATE", "surface"): ("forecast_precip_rate_mm_h", mean),
}


def forecast_hours(sunset, sunrise):
    decision = sunset - HOUR
    # Allow two hours for publication of the initialized run and all its leads.
    run = (decision - 2 * HOUR).replace(minute=0, second=0, microsecond=0)
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


def _nearest(path, points, field):
    latitudes, longitudes = zip(*points.values())
    message = codes_new_from_message(path.read_bytes())
    try:
        nearest = codes_grib_find_nearest_multiple(message, False, latitudes, [lon % 360 for lon in longitudes])
        missing = codes_get(message, "missingValue")
        for key, sample in zip(points, nearest):
            value = float(sample.value)
            if isfinite(value) and value != missing and sample.distance < 10:
                yield key, value * 3600 if field == ("PRATE", "surface") else value
    finally:
        codes_release(message)


def weather_forecast(cache, sunset, sunrise, points):
    decision, run, leads = forecast_hours(sunset, sunrise)
    samples = defaultdict(lambda: defaultdict(list))
    for lead in leads:
        url = f"{ARCHIVE}/hrrr.{run:%Y%m%d}/conus/hrrr.t{run:%H}z.wrfsfcf{lead:02d}.grib2"
        try:
            index = cache.get("hrrr", url + ".idx").read_text()
        except HTTPError as error:
            if error.response.status_code == 404:
                continue
            raise
        for field, byte_range in _field_ranges(index):
            path = cache.get("hrrr", url, byte_range=byte_range, signature=b"GRIB")
            for key, value in _nearest(path, points, field):
                samples[key][field].append(value)

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
