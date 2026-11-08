"""Find daily Black Marble granules and sample quality-checked pixels."""

import os
import re
from collections import defaultdict
from datetime import date, timedelta
from math import isfinite

import h5py
from requests import HTTPError

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
COLLECTION = "C3365931269-LAADS"
LAADS = "https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5200/VNP46A2"
HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
FIELDS = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields"


def granules_by_date(cache, first, last, bounds):
    granules = defaultdict(dict)
    page = 1
    while True:
        records = cache.json("cmr", CMR, params={
            "collection_concept_id": COLLECTION,
            "bounding_box": ",".join(map(str, bounds)),
            "temporal": f"{first}T00:00:00Z,{last}T23:59:59Z",
            "page_size": 2000, "page_num": page,
        })["feed"]["entry"]
        for record in records:
            name = record["producer_granule_id"]
            match = re.fullmatch(r"VNP46A2\.A(\d{4})(\d{3})\.(h\d{2}v\d{2})\.002\.(\d+)\.h5", name)
            if not match:
                raise ValueError(f"Unexpected Black Marble filename {name}")
            year, day_of_year, tile, _ = match.groups()
            day = date(int(year), 1, 1) + timedelta(days=int(day_of_year) - 1)
            if first <= day <= last:
                # Prefer the latest production of a tile if CMR lists duplicates.
                granules[day][tile] = max(name, granules[day].get(tile, ""))
        if len(records) < 2000:
            break
        page += 1
    return {day: tuple(tiles.values()) for day, tiles in granules.items()}


def sample_file(path, points, granule):
    result = {}
    with h5py.File(path, "r") as file:
        fields = file[FIELDS]
        latitudes = fields["lat"][:]
        longitudes = fields["lon"][:]
        radiance = fields["DNB_BRDF-Corrected_NTL"]
        quality = fields["Mandatory_Quality_Flag"]
        cloud = fields["QF_Cloud_Mask"]
        lat_pad = abs(latitudes[1] - latitudes[0]) / 2
        lon_pad = abs(longitudes[1] - longitudes[0]) / 2
        for key, (lat, lon) in points.items():
            if not (min(latitudes[0], latitudes[-1]) - lat_pad <= lat <= max(latitudes[0], latitudes[-1]) + lat_pad
                    and min(longitudes[0], longitudes[-1]) - lon_pad <= lon <= max(longitudes[0], longitudes[-1]) + lon_pad):
                continue
            i = int(abs(latitudes - lat).argmin())
            j = int(abs(longitudes - lon).argmin())
            value = float(radiance[i, j])
            flag = int(quality[i, j])
            result[key] = {
                "observed_neighborhood_radiance": value if flag == 0 and isfinite(value) and value >= 0 else None,
                "radiance_quality": flag,
                "radiance_cloud_mask": int(cloud[i, j]),
                "radiance_granule": granule,
            }
    return result


def neighborhood_light(cache, day, granules, points):
    result = {}
    for name in granules:
        url = f"{LAADS}/{day:%Y}/{day:%j}/{name}"
        try:
            path = cache.get("viirs", url, token=os.getenv("EARTHDATA_TOKEN"), signature=HDF5_SIGNATURE)
        except (HTTPError, ValueError) as error:
            if isinstance(error, HTTPError) and error.response.status_code not in (401, 403):
                raise
            raise ValueError(
                "NASA did not return an HDF5 tile. Set EARTHDATA_TOKEN to an Earthdata download token "
                "with LAADS access, or use --skip-satellite for a partial dataset."
            ) from error
        result.update(sample_file(path, points, name))
    return result
