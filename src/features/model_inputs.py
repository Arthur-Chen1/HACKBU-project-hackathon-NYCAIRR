"""Validate the shared building-night feature values and their availability."""

from datetime import UTC, date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

NYC = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)


def number(value, low=None, high=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def parse_utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value.astimezone(UTC)
    return None


def local_night(value):
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return None


def season_for(row):
    season = row.get("season")
    if season in ("spring", "fall"):
        return season
    if season not in (None, ""):
        return None
    night = local_night(row.get("night_date"))
    if night is None:
        return None
    # Dryad assigns the season using the morning survey date.
    return "spring" if (night + timedelta(days=1)).month <= 6 else "fall"


def natural_light_features(row):
    night = local_night(row.get("night_date"))
    sunset = parse_utc(row.get("sunset_utc"))
    sunrise = parse_utc(row.get("sunrise_utc"))
    night_hours = None
    if night and sunset and sunrise and sunset < sunrise:
        if sunset.astimezone(NYC).date() == night and sunrise.astimezone(NYC).date() == night + timedelta(days=1):
            night_hours = (sunrise - sunset) / HOUR
    fraction = number(row.get("moon_illum_fraction"), 0, 1)
    minutes = number(row.get("moon_above_horizon_minutes"), 0)
    exposure = None
    if night_hours is not None and fraction is not None and minutes is not None and minutes <= 60 * night_hours:
        exposure = fraction * minutes / 60
    return {"night_hours": night_hours, "potential_moonlit_hours": exposure}


def usable_radiance(row, mode):
    if mode == "observed":
        if number(row.get("radiance_quality")) == 0:
            return number(row.get("observed_neighborhood_radiance"), 0)
        return None
    decision = parse_utc(row.get("decision_at_utc"))
    available = parse_utc(row.get("prior_radiance_available_at_utc"))
    if decision and available and available <= decision and number(row.get("prior_radiance_quality")) == 0:
        return number(row.get("prior_neighborhood_radiance"), 0)
    return None


def forecast_issues(row):
    """Report decision-time lineage or HRRR coverage that prevents forecast scoring."""
    issues = []
    sunset = parse_utc(row.get("sunset_utc"))
    decision = parse_utc(row.get("decision_at_utc"))
    run = parse_utc(row.get("hrrr_run_utc"))
    migration_issue = parse_utc(row.get("forecast_migration_issue_utc"))
    if sunset is None:
        issues.append("sunset_utc")
    if decision is None or (sunset and decision > sunset - HOUR):
        issues.append("decision_at_utc")
    if migration_issue is None or (decision and migration_issue > decision):
        issues.append("forecast_migration_issue_utc")
    if run is None or (decision and run > decision - 2 * HOUR):
        issues.append("hrrr_run_utc")
    expected = number(row.get("hrrr_expected_hours"), 1)
    covered = number(row.get("hrrr_required_field_hours"), 0)
    if expected is None or not expected.is_integer() or covered != expected:
        issues.append("hrrr_required_field_hours")
    return issues
