"""Build forecast building-night rows using only pre-sunset inputs."""

import csv
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from requests import RequestException

from src.features.model_inputs import number, parse_utc, season_for
from src.ingest import hrrr, usno
from src.ingest.http import Cache
from src.ingest.nyc import NYC_POINT
from src.models import migration_forecast

MIGRATION_FIELDS = (
    "forecast_migration_birds_per_km_h", "forecast_migration_issue_utc", "forecast_migration_region",
)
RADIANCE_FIELDS = (
    "prior_neighborhood_radiance", "prior_radiance_quality", "prior_radiance_available_at_utc",
    "prior_radiance_granule",
)
REGION_KEY = "__nyc_reference_point__"  # HRRR sample for the regional migration estimate
ASTRONOMY_FIELDS = (
    "sunset_utc", "sunrise_utc", "civil_dusk_utc", "civil_dawn_utc",
    "moon_illum_fraction", "moon_above_horizon_minutes",
)


def load_daily_inputs(path):
    """Read one optional regional forecast and one prior light sample per BIN and night."""
    if path is None:
        return {}
    inputs = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not {"night_date", "bin"}.issubset(reader.fieldnames or ()):
            raise ValueError("Daily inputs need night_date and bin columns")
        for row in reader:
            try:
                night = date.fromisoformat((row["night_date"] or "").strip())
                bin_id = (row["bin"] or "").strip()
                if bin_id and not re.fullmatch(r"[1-5]\d{6}", bin_id):
                    raise ValueError("expected a seven-digit NYC BIN or a blank regional BIN")
                key = night, bin_id
                if key in inputs:
                    raise ValueError("duplicate night and BIN")
                fields, other = (RADIANCE_FIELDS, MIGRATION_FIELDS) if bin_id else (MIGRATION_FIELDS, RADIANCE_FIELDS)
                if any((row.get(field) or "").strip() for field in other):
                    raise ValueError("use a blank BIN for regional migration and a specific BIN for prior radiance")
                values = {field: (row.get(field) or "").strip() or None for field in fields}
                amount, quality = number(values[fields[0]]), number(values.get("prior_radiance_quality"))
                timestamp = parse_utc(values[fields[-2]])
                if amount is None or amount < 0 or timestamp is None or not values[fields[-1]]:
                    raise ValueError("expected a nonnegative value, an aware timestamp, and a region or granule")
                values[fields[0]], values[fields[-2]] = amount, timestamp
                if bin_id:
                    if quality is None or quality < 0 or not quality.is_integer():
                        raise ValueError("expected a nonnegative integer radiance quality flag")
                    values["prior_radiance_quality"] = int(quality)
                inputs[key] = values
            except ValueError as error:
                raise ValueError(f"Daily inputs line {reader.line_num}: {error}") from error
    return inputs


def forecast_rows(buildings, night, daily_inputs, cache, *, now=None):
    if not buildings:
        return []
    now = datetime.now(UTC) if now is None else parse_utc(now)
    if now is None:
        raise ValueError("Current time must have a UTC offset")
    store = Cache(cache)
    natural, weather, issues = dict.fromkeys(ASTRONOMY_FIELDS), {}, []
    decision = None
    try:
        try:
            natural.update(usno.natural_light(store, night))
        except (RequestException, OSError, ValueError, KeyError):
            issues.append("USNO Sun and Moon data unavailable")
        if natural["sunset_utc"] and natural["sunrise_utc"]:
            decision = min(now, natural["sunset_utc"] - timedelta(hours=1))
            points = {b["bin"]: (b["latitude"], b["longitude"]) for b in buildings} | {REGION_KEY: NYC_POINT}
            try:
                weather = hrrr.weather_forecast(store, natural["sunset_utc"], natural["sunrise_utc"],
                                                points, decision_at=decision)
            except (RequestException, OSError, ValueError):
                issues.append("HRRR weather forecast unavailable")
    finally:
        store.session.close()

    migration = dict.fromkeys(MIGRATION_FIELDS) | daily_inputs.get((night, ""), {})
    if migration["forecast_migration_birds_per_km_h"] is None and REGION_KEY in weather:
        # No issued forecast: estimate regional traffic from tonight's HRRR weather and earlier seasons.
        try:
            estimated, reason = migration_forecast.estimate(night, weather[REGION_KEY], decision, cache)
        except (RequestException, OSError, ValueError) as error:
            estimated, reason = None, str(error)
        if estimated:
            migration = estimated
        else:
            issues.append(f"No migration forecast supplied, and no estimate: {reason}")
    return [
        {**building, **natural, **migration, **weather.get(building["bin"], {}),
         **(dict.fromkeys(RADIANCE_FIELDS) | daily_inputs.get((night, building["bin"]), {})),
         "night_date": night, "decision_at_utc": decision,
         "season": season_for({"night_date": night}),
         "feature_location": "matched_footprint", "feature_latitude": building["latitude"],
         "feature_longitude": building["longitude"], "source_issues": tuple(issues)}
        for building in buildings
    ]
