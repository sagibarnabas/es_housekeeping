import argparse


def cleanup(args):
    return

def report(args):
    return

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