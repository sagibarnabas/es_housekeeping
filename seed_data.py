#!/usr/bin/env python3
"""Seed the local Elasticsearch cluster with realistic-ish sample data.

Creates a mix of indices so the housekeeping tool has something to work with:

  * date-suffixed "log" indices spanning old -> recent (logs-YYYY.MM.DD)
  * a couple of non-dated indices (e.g. app config / reference data)
  * varying document counts and therefore varying sizes

We try to backdate each index's `index.creation_date` so tools that key off the
real creation date see genuine age. Some Elasticsearch versions treat that as a
private setting and reject it; if so we fall back to relying on the date in the
index name (and print a note). Either signal is a legitimate basis for your
"age" logic — document which one you chose.

Usage:
    python seed_data.py                      # against http://localhost:9200
    ES_URL=http://localhost:9200 python seed_data.py
    python seed_data.py --reset              # delete previously seeded indices first

This helper is intentionally simple and dependency-light (just `requests`).
You may adapt or ignore it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys

import requests

ES_URL = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")

# (offset_in_days_ago, approx_doc_count)
LOG_INDICES = [
    (400, 500),   # very old
    (200, 800),
    (95, 1200),
    (40, 1500),
    (10, 2000),
    (2, 900),     # fresh
    (0, 300),     # today
]

STATIC_INDICES = {
    "app-config": 12,
    "reference-countries": 250,
}

SERVICES = ["auth", "billing", "gateway", "worker", "frontend"]
LEVELS = ["INFO", "INFO", "INFO", "WARN", "ERROR"]


def _check_cluster() -> None:
    try:
        r = requests.get(f"{ES_URL}/_cluster/health", timeout=10)
        r.raise_for_status()
    except requests.RequestException as exc:
        sys.exit(f"Cannot reach Elasticsearch at {ES_URL}: {exc}\n"
                 f"Is it running?  docker compose up -d")


def _seeded_indices() -> list[str]:
    r = requests.get(f"{ES_URL}/_cat/indices/logs-*,app-config,reference-countries",
                     params={"format": "json", "h": "index"}, timeout=10)
    if r.status_code == 404 or not r.text.strip():
        return []
    return [row["index"] for row in r.json()]


def reset() -> None:
    idx = _seeded_indices()
    if not idx:
        print("Nothing to reset.")
        return
    requests.delete(f"{ES_URL}/{','.join(idx)}", timeout=30)
    print(f"Deleted: {', '.join(idx)}")


def _create_index(name: str, days_ago: int) -> None:
    created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    creation_ms = int(created.timestamp() * 1000)

    # Attempt to backdate creation_date; fall back if the version rejects it.
    body = {"settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0,
                                   "creation_date": creation_ms}}}
    r = requests.put(f"{ES_URL}/{name}", json=body, timeout=30)
    if not r.ok:
        r = requests.put(f"{ES_URL}/{name}",
                         json={"settings": {"index": {"number_of_shards": 1,
                                                      "number_of_replicas": 0}}},
                         timeout=30)
        if not r.ok:
            print(f"  ! failed to create {name}: {r.status_code} {r.text}")
            return
        print(f"  (note: could not backdate creation_date for {name}; "
              f"rely on the name date instead)")


def _bulk_index(name: str, count: int, base_day: dt.datetime) -> None:
    lines = []
    for _ in range(count):
        ts = base_day + dt.timedelta(seconds=random.randint(0, 86399))
        doc = {
            "@timestamp": ts.isoformat(),
            "service": random.choice(SERVICES),
            "level": random.choice(LEVELS),
            "message": "sample log line",
            "latency_ms": random.randint(1, 900),
        }
        lines.append('{"index":{}}')
        import json as _json
        lines.append(_json.dumps(doc))
    payload = "\n".join(lines) + "\n"
    r = requests.post(f"{ES_URL}/{name}/_bulk",
                      data=payload,
                      headers={"Content-Type": "application/x-ndjson"},
                      timeout=120)
    r.raise_for_status()


def seed() -> None:
    _check_cluster()
    now = dt.datetime.now(dt.timezone.utc)

    for days_ago, count in LOG_INDICES:
        day = now - dt.timedelta(days=days_ago)
        name = f"logs-{day:%Y.%m.%d}"
        print(f"Creating {name} (~{count} docs, {days_ago}d old)")
        _create_index(name, days_ago)
        _bulk_index(name, count, day.replace(hour=0, minute=0, second=0, microsecond=0))

    for name, count in STATIC_INDICES.items():
        print(f"Creating {name} (~{count} docs)")
        _create_index(name, days_ago=0)
        _bulk_index(name, count, now)

    requests.post(f"{ES_URL}/_refresh", timeout=30)
    print("\nDone. Try:  curl 'http://localhost:9200/_cat/indices?v'")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed sample data for the exercise.")
    p.add_argument("--reset", action="store_true",
                   help="delete previously seeded indices, then exit")
    args = p.parse_args()

    if args.reset:
        _check_cluster()
        reset()
        return
    seed()


if __name__ == "__main__":
    main()
