"""Cache API responses and their retrieval metadata in data/raw."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


class Cache:
    def __init__(self, root: Path):
        self.root = root
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "NYCAIRR-research-ingest/0.1"
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=3, backoff_factor=0.75, status_forcelist=(429, 500, 502, 503, 504),
            raise_on_status=False,
        )))

    def get(self, source, url, *, params=None, byte_range=None, token=None, signature=None):
        request_url = requests.Request("GET", url, params=params).prepare().url
        key = hashlib.sha256(repr((request_url, byte_range)).encode()).hexdigest()[:24]
        folder = self.root / source
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / key
        if path.is_file():
            return path

        headers = {}
        if byte_range is not None:
            start, stop = byte_range
            headers["Range"] = f"bytes={start}-{stop if stop is not None else ''}"
        if token:
            headers["Authorization"] = f"Bearer {token}"

        with self.session.get(request_url, headers=headers, timeout=(15, 90), stream=True) as response:
            response.raise_for_status()
            if byte_range is not None:
                content_range = response.headers.get("Content-Range", "")
                expected_range = headers["Range"].replace("=", " ")
                if response.status_code != 206 or not content_range.startswith(expected_range):
                    raise ValueError(f"{source} did not return the requested byte range")
                if stop is not None and not content_range.startswith(expected_range + "/"):
                    raise ValueError(f"{source} returned an unexpected byte range")

            with NamedTemporaryFile(dir=folder, delete=False) as output:
                temp = Path(output.name)
                try:
                    chunks = response.iter_content(chunk_size=128 * 1024)
                    first = next(chunks, b"")
                    if not first or (signature is not None and not first.startswith(signature)):
                        raise ValueError(f"{source} returned an unexpected file type or empty response")
                    output.write(first)
                    for chunk in chunks:
                        output.write(chunk)
                    output.close()
                    path.with_suffix(".json").write_text(json.dumps({
                        "url": request_url,
                        "byte_range": byte_range,
                        "retrieved_at_utc": datetime.now(UTC).isoformat(),
                        "last_modified": response.headers.get("Last-Modified"),
                    }, indent=2) + "\n")
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
        return path

    def json(self, source, url, *, params=None):
        return json.loads(self.get(source, url, params=params).read_text())
