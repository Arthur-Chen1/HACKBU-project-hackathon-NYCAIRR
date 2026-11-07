"""Build a one-row-per-study-building-night dataset without inventing labels."""

import csv
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

DAY = timedelta(days=1)


def building_nights(surveys):
    groups = defaultdict(list)
    for survey in surveys:
        groups[survey["night_date"], survey["study_building_id"]].append(survey)

    for group in sorted(groups):
        records = groups[group]
        row = records[0].copy()
        counts = {record["observed_collision_count"] for record in records}
        row["survey_records"] = len(records)
        # Repeated visits may count the same bird. Retain agreed counts only.
        if len(counts) > 1:
            row["observed_collision_count"] = None
        yield row


def date_coverage(dates):
    days = sorted(set(dates))
    runs = []
    for day in days:
        if not runs or day != runs[-1]["end"] + DAY:
            runs.append({"start": day, "end": day, "nights": 1})
        else:
            runs[-1]["end"] = day
            runs[-1]["nights"] += 1
    return {
        "nights": len(days),
        "start": days[0] if days else None,
        "end": days[-1] if days else None,
        "longest_contiguous": max(runs, key=lambda run: run["nights"], default=None),
        "contiguous_windows": runs,
    }


def _atomic_write(path, write):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            write(file)
            file.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def write_csv(path, rows, fields=None):
    def write(file):
        columns = list(fields) if fields is not None else list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    _atomic_write(path, write)


def write_json(path, data):
    _atomic_write(path, lambda file: json.dump(data, file, indent=2, default=str, allow_nan=False))
