# es-housekeeping

A small CLI tool for reporting on and cleaning up stale Elasticsearch indices.

## Install & run (fresh machine)
  
```bash
git clone https://github.com/sagibarnabas/es_housekeeping.git
cd es_housekeeping

python3 -m venv venv
source venv/bin/activate     

pip install -r requirements.txt
```

Set connection details via environment variables (no secrets hard-coded, nothing printed):

```bash
export ELASTIC_URL=http://localhost:9200
# optional, only if your cluster has auth enabled:
export ELASTIC_USER=elastic
export ELASTIC_PASS=changeme
# optional, TLS verification (defaults to on/verified; only relevant if
# ELASTIC_URL is https:// — has no effect over plain http://, like the local # docker-compose cluster in this exercise):
export ELASTIC_VERIFY_TLS=false        # e.g. false: an https cluster with a self-signed cert
export ELASTIC_CA_CERT=/path/to/ca.pem # or: verify against a custom/internal CA bundle
```

If you want a to fill up a cluster with realistic sample data:

```bash
python3 seed_data.py
```

Run the tool (examples):

```bash
python3 es_housekeeping.py report --pattern "logs-*"
python3 es_housekeeping.py cleanup --pattern "logs-*" --older-than-days 30
```

## Example commands and output

**Report, table (default):**

```bash
$ python3 es_housekeeping.py report --pattern "logs-*"
index            health      docs_count  store_size      age_in_days  ilm_managed
---------------  --------  ------------  ------------  -------------  -------------
logs-2025.06.20  green              500  1.2mb                   400  False
logs-2026.01.06  green              800  2.1mb                   200  False
logs-2026.04.21  yellow            1200  3.4mb                    95  False
logs-2026.06.15  green             1500  4.8mb                    40  False
logs-2026.07.15  green             2000  6.2mb                    10  False
logs-2026.07.23  green              900  2.9mb                     2  True
logs-2026.07.25  green              300  1.0mb                     0  True
```

**Report, JSON:**

```bash
$ python3 es_housekeeping.py report --pattern "logs-*" --json
[
  {
    "index": "logs-2026.07.25",
    "health": "green",
    "docs_count": "300",
    "store_size": "1.0mb",
    "age_in_days": 0,
    "ilm_managed": true
  }
]
```

**Cleanup, dry-run (default — nothing is ever deleted this way):**

```bash
$ python3 es_housekeeping.py cleanup --pattern "logs-*" --older-than-days 30
2 old index(es) matching 'logs-*' older than 30 days:
index            health      docs_count  store_size      age_in_days  ilm_managed
---------------  --------  ------------  ------------  -------------  -------------
logs-2025.06.20  green              500  1.2mb                   400  False
logs-2026.01.06  green              800  2.1mb                   200  False

DRY-RUN: nothing deleted. Re-run with --apply to actually delete these.
```

**Cleanup, apply (requires typing "yes" to confirm — deletion cannot be undone):**

```bash
$ python3 es_housekeeping.py cleanup --pattern "logs-*" --older-than-days 30 --apply
2 old index(es) matching 'logs-*' older than 30 days:
index            health      docs_count  store_size      age_in_days  ilm_managed
---------------  --------  ------------  ------------  -------------  -------------
logs-2025.06.20  green              500  1.2mb                   400  False
logs-2026.01.06  green              800  2.1mb                   200  False

This will PERMANENTLY delete 2 index(es) listed above. This cannot be undone. Type 'yes' to confirm: yes

APPLY: deleting...
  deleted logs-2025.06.20
  deleted logs-2026.01.06
```

## Running the tests

No live cluster required — every test mocks `es_request`.

```bash
pytest tests/ -v
```

## Design notes
- **CLI shape**: `argparse` subparsers (`report`, `cleanup`) wired via `set_defaults(func=...)`, so `main()` just calls `args.func(args)` instead of an `if/elif` chain.
- **Dry-run safety**: `--dry-run`/`--apply` are mutually exclusive with `apply=False` as the default, and `--apply` still requires typing `yes` at a confirmation prompt (shown after the stale list) before anything is deleted.
- **Age source**: prefers the date embedded in `logs-YYYY.MM.DD` names over `creation.date`, because this ES version silently rejects backdating `creation_date` (confirmed against the real local cluster); falls back to `creation.date` only for non-dated indices.
- **Fixed regex over a date-finder library** for name parsing, avoids misreading an unrelated number in some future index name as a date.
- **Age is floored, never rounded**, so a 29.9-day-old index doesn't cross a 30-day threshold early. That makes deletion logic safer.
- **ILM status via a second call** to `/_ilm/explain` (not exposed by `_cat/indices`), merged by index name; an index missing from that response defaults to `managed: False`.
- **`tabulate` for table output** instead of hand-rolled column alignment.
- **Kept the provided api reference file as the client** (with some touch-ups).
- **TLS verification defaults to on**; added `ELASTIC_VERIFY_TLS` (opt-out, only matters for `https://`) and `ELASTIC_CA_CERT` (custom CA bundle, takes precedence).

## Known limitations / what I'd do with more time
- **Error handling**: catch and distinguish specific failure modes (e.g. 404 vs auth errors) and handle malformed/unexpected API responses per-index instead of one blanket `ValueError` aborting everything.
- **Config file support**: add an optional config file (e.g. YAML).
- **Broader date parsing for index names**: support multiple calendar/date formats in index names, not just `logs-YYYY.MM.DD`, falling back to a date-finding library when the fixed pattern doesn't match.
- **Smarter creation-date trust**: prefer the cluster's real `creation.date` when it's genuinely trustworthy.
