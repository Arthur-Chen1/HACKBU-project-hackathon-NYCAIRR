"""List and download NEXRAD Level II volume scans from the public Unidata/NOAA S3 archive."""

import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

ARCHIVE = "https://unidata-nexrad-level2.s3.amazonaws.com"
# Upton, NY. About 95 km east of Midtown Manhattan; the lowest beam is ~1-1.6 km up over NYC.
SITE = "KOKX"
# KDIX (Fort Dix, NJ) is ~95 km southwest of Manhattan and can cross-check KOKX.
SITE_LOCATION = {"KOKX": (40.8655, -72.8639), "KDIX": (39.9471, -74.4108)}
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_KEY = re.compile(r"(?P<site>[A-Z]{4})(?P<time>\d{8}_\d{6})_V\d{2}$")


def scans(session, site, start, end):
    """Return [(scan_time_utc, url)] for volume scans starting in [start, end]."""
    found = []
    day = start.date()
    while day <= end.date():
        params = {"list-type": "2", "prefix": f"{day:%Y/%m/%d}/{site}/"}
        while True:
            response = session.get(ARCHIVE + "/", params=params, timeout=(15, 60))
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.iter(_S3 + "Key"):
                match = _KEY.search(item.text)
                if match:  # Skips the *_MDM metadata files.
                    when = datetime.strptime(match["time"], "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
                    if start <= when <= end:
                        found.append((when, f"{ARCHIVE}/{item.text}"))
            token = root.findtext(_S3 + "NextContinuationToken")
            if not token:
                break
            params["continuation-token"] = token
        day += timedelta(days=1)
    return sorted(found)


def every(found, minutes):
    """Thin scans to at most one per `minutes`, keeping the first scan of each interval."""
    kept = []
    for when, url in found:
        if not kept or when - kept[-1][0] >= timedelta(minutes=minutes):
            kept.append((when, url))
    return kept


def read(cache, url):
    """Download (cached) and decode one volume scan into a Py-ART Radar object."""
    path = cache.get("nexrad", url, signature=b"AR2V")
    os.environ.setdefault("PYART_QUIET", "1")
    import pyart  # Heavy import; only needed for radar commands.

    return pyart.io.read_nexrad_archive(str(path))
