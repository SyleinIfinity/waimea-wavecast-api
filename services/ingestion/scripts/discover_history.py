"""Bounded, read-only NOAA 51201 archive coverage audit.

Only links present on NDBC station history are fetched. No GCS or DB writes.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from packages.noaa.normalize import normalize_row
from packages.noaa.parser import CANONICAL_FIELDS, parse_stdmet

HISTORY = "https://www.ndbc.noaa.gov/station_history.php?station=51201"
MAX_COMPRESSED = 5 * 1024 * 1024
MAX_DECOMPRESSED = 50 * 1024 * 1024


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href")


def discover(session, max_files: int) -> list[tuple[str, str]]:
    response = session.get(HISTORY, timeout=(5, 20))
    response.raise_for_status()
    parser = Links()
    parser.feed(response.text)
    found = []
    for link in parser.links:
        parsed = urlparse(urljoin(HISTORY, link))
        if parsed.path != "/download_data.php":
            continue
        query = parse_qs(parsed.query)
        filename = query.get("filename", [""])[0]
        directory = query.get("dir", [""])[0]
        if not re.fullmatch(r"51201(?:h\d{4}|[1-9abc]\d{4})\.txt\.gz", filename):
            continue
        if directory not in ("data/historical/stdmet/", *(f"data/stdmet/{m}/" for m in
                               ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))):
            continue
        found.append((filename, f"https://www.ndbc.noaa.gov/{directory}{filename}"))
    return found[:max_files]


def inspect(session, filename, url):
    with session.get(url, timeout=(5, 30), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_COMPRESSED:
                raise ValueError("compressed file exceeds 5 MiB audit cap")
            chunks.append(chunk)
    raw = b"".join(chunks)
    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            if len(stream.read(MAX_DECOMPRESSED + 1)) > MAX_DECOMPRESSED:
                raise ValueError("decompressed file exceeds 50 MiB audit cap")
    parsed = parse_stdmet(raw, filename=url)
    counts = Counter()
    for row in parsed.rows:
        values = normalize_row(row.raw_values)
        counts.update(name for name, value in values.items() if name in CANONICAL_FIELDS and value is not None)
    return {"filename": filename, "url": url, "compressed_bytes": size,
            "rows": len(parsed.rows), "parse_errors": parsed.parse_error_count,
            "columns": parsed.columns_in_source,
            "nonmissing_by_variable": {key: counts.get(key, 0) for key in CANONICAL_FIELDS},
            "first_observed_at": min((row.observed_at for row in parsed.rows), default=None),
            "last_observed_at": max((row.observed_at for row in parsed.rows), default=None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files", type=int, default=28)
    args = parser.parse_args()
    if not 1 <= args.max_files <= 28:
        parser.error("max-files must be 1..28")
    session = requests.Session()
    session.headers["User-Agent"] = "WaimeaWaveCast/1.0 (bounded read-only archive audit)"
    files = discover(session, args.max_files)
    results = []
    for filename, url in files:
        try:
            results.append(inspect(session, filename, url))
        except Exception as exc:
            results.append({"filename": filename, "url": url, "error": type(exc).__name__})
        time.sleep(0.25)
    print(json.dumps({"checked_at": datetime.now(timezone.utc), "source": HISTORY,
                      "listed_files": len(files), "files": results}, default=str, indent=2))


if __name__ == "__main__":
    main()
