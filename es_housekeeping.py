import argparse
from tabulate import tabulate
import json
import re
import time
import datetime as dt

from elastic_api import es_request

def cleanup(args):
    return

def age_days(index_name: str, creation_date_ms: int) -> int:
    ## Safe with regex, as the format is kind of fix
    match = re.compile(r"^logs-(\d{4})\.(\d{2})\.(\d{2})$").match(index_name)
    if match:
        year, month, day = (int(part) for part in match.groups())
        created_date_seconds = dt.datetime(year, month, day, tzinfo=dt.timezone.utc).timestamp()
    else:
        created_date_seconds = creation_date_ms / 1000
    
    return int((time.time() - created_date_seconds) // 86_400)

def fetch_data(pattern: str):
    indices = es_request("GET", f"/_cat/indices/{pattern}", params={"format": "json","h": "index,health,docs.count,pri.store.size,creation.date"})
    ilm = es_request("GET", f"/{pattern}/_ilm/explain", params={"format": "json"})

    data = []
    for row in indices:
        name = row["index"]
        ilm_info = ilm.get("indices", {}).get(name, {})
        data.append(
            {
                "index": name,
                "health": row["health"],
                "docs_count": row["docs.count"],
                "primary_store_size": row["pri.store.size"],
                "age_in_days": age_days(name, int(row["creation.date"])),
                "ilm_managed": ilm_info.get("managed", False)
            }
        )
    return data

def report(args):
    data=fetch_data(args.pattern)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(tabulate(data, headers="keys", tablefmt="simple"))

def main():
    p = argparse.ArgumentParser(prog="es-housekeeping")
    sub = p.add_subparsers(dest="command", required=True) ## args.command stores the chosen one

    r = sub.add_parser("report")
    r.add_argument("--pattern", default="*")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=report) ## connect to function 

    c = sub.add_parser("cleanup")
    c.add_argument("--pattern", default="*")
    c.add_argument("--older-than-days", type=int, required=True)

    g = c.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="apply", action="store_false")
    g.add_argument("--apply", dest="apply", action="store_true")
    c.set_defaults(apply=False, func=cleanup) ## connect to function 

    args = p.parse_args()
    args.func(args) ## run the chosen function


if __name__ == "__main__":
    main()