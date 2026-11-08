"""Pull astronomy data and align UTC events to the local NYC night."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.ingest.nyc import NYC_POINT

USNO = "https://aa.usno.navy.mil/api/rstt/oneday"
NYC = ZoneInfo("America/New_York")
DAY = timedelta(days=1)


def _events(payload, date, key):
    for entry in payload[key]:
        if entry.get("time"):
            hours, minutes = map(int, entry["time"].strip().split(":"))
            yield datetime.combine(date, time(), UTC) + timedelta(hours=hours, minutes=minutes), entry["phen"]


def natural_light(cache, night_date):
    # Query by UTC day so the repeated hour at the autumn DST change stays unambiguous.
    noon = datetime.combine(night_date, time(12), NYC).astimezone(UTC)
    next_noon = datetime.combine(night_date + DAY, time(12), NYC).astimezone(UTC)
    dates = (noon.date(), next_noon.date())
    solar, lunar = [], []
    fraction = None
    for date in dates:
        response = cache.json("usno", USNO, params={
            "date": date.isoformat(), "coords": ",".join(map(str, NYC_POINT)), "tz": 0,
        })
        if response.get("error"):
            raise ValueError(f"USNO returned an error for {date}")
        payload = response["properties"]["data"]
        solar.extend(_events(payload, date, "sundata"))
        lunar.extend((when, event) for when, event in _events(payload, date, "moondata")
                     if event in {"Rise", "Set"})
        if fraction is None and payload.get("fracillum"):
            fraction = float(payload["fracillum"].rstrip("%")) / 100

    local_solar = {event: when for when, event in solar if noon <= when < next_noon}
    sunset = local_solar["Set"]
    sunrise = local_solar["Rise"]
    if sunset >= sunrise:
        raise ValueError(f"Could not resolve sunset and sunrise for {night_date}")
    lunar.sort()
    above = lunar[0][1] == "Set" if lunar else None
    cursor = sunset
    seconds_above = 0
    for when, event in lunar:
        if when >= sunrise:
            break
        if when > sunset:
            if above:
                seconds_above += (when - cursor).total_seconds()
            cursor = when
        above = event == "Rise"
    if above:
        seconds_above += (sunrise - cursor).total_seconds()

    return {
        "sunset_utc": sunset, "sunrise_utc": sunrise,
        "civil_dusk_utc": local_solar.get("End Civil Twilight"),
        "civil_dawn_utc": local_solar.get("Begin Civil Twilight"),
        "moon_illum_fraction": fraction,
        "moon_above_horizon_minutes": seconds_above / 60 if lunar else None,
    }
