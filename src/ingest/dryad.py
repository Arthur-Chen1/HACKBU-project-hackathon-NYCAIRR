"""Read the pinned NYC collision, migration, and LaGuardia weather release."""

from csv import DictReader
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
from zipfile import ZipFile

VERSION = "272593"
URL = f"https://datadryad.org/api/v2/versions/{VERSION}/download"
CSV_FILE = "collisions_migration_weather_spring_fall_dataset.csv"
CSV_DIGEST = "7fdcf00b78fe2025014462be96ce15a67ffa7b2f64a800eee594a777f18a38c0"
# Map the project's descriptive column names to the original study headers.
OBSERVED_COLUMNS = {
    "observed_migration_birds_per_km_h": "amt",
    "observed_visibility_m": "visibility_distance",
    "observed_cloud_ceiling_m": "ceiling_height",
    "observed_wind_u_m_s": "uwind",
    "observed_wind_v_m_s": "vwind",
    "observed_temperature_c": "temperature",
}


def surveys(cache):
    """Download or reuse the pinned study release and read its survey records."""
    with ZipFile(cache.get("dryad", URL, signature=b"PK\x03\x04")) as archive:
        data = archive.read(CSV_FILE)
    if sha256(data).hexdigest() != CSV_DIGEST:
        raise ValueError("Dryad CSV checksum does not match the pinned release")

    for record in DictReader(StringIO(data.decode("utf-8-sig"))):
        # The survey happened in the morning. Its conditions belong to the preceding night.
        morning = datetime.strptime(record["date"], "%m/%d/%Y").date()
        yield {
            "study_building_id": record["building"],
            "night_date": morning - timedelta(days=1),
            "season": "spring" if morning.month <= 6 else "fall",
            "observed_collision_count": int(record["count"]),
            **{name: float(record[column]) for name, column in OBSERVED_COLUMNS.items()},
        }
