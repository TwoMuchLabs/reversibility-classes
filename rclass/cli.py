"""Command line: `rclass lookup`, `rclass validate`, `rclass stats`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .classify import classify
from .policy import DEFAULT_POLICY
from .registry import RecordError, Registry, read_record_file, stats, validate_record


def _registry(paths: list[str] | None) -> Registry:
    return Registry.from_paths(paths) if paths else Registry.bundled()


def cmd_lookup(ns: argparse.Namespace) -> int:
    try:
        args = json.loads(ns.args) if ns.args else None
    except json.JSONDecodeError as exc:
        print(f"--args is not valid JSON: {exc}", file=sys.stderr)
        return 2
    c = classify(ns.server, ns.tool, args, registry=_registry(ns.registry))
    out = c.to_dict() | {"default_policy": DEFAULT_POLICY[c.rclass].value}
    if ns.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"{c.rclass}  {c.rclass.label}  ({c.source})")
        print(f"  {c.rationale}")
        print(f"  default policy: {out['default_policy']}")
    return 0


def cmd_validate(ns: argparse.Namespace) -> int:
    targets = [Path(p) for p in (ns.paths or ["registry"])]
    files = []
    for t in targets:
        files += sorted(f for f in t.rglob("*") if f.suffix in (".yaml", ".yml", ".json")) if t.is_dir() else [t]
    failed = 0
    for f in files:
        try:
            validate_record(read_record_file(f), str(f))
        except (RecordError, ValueError) as exc:
            failed += 1
            print(f"FAIL {exc}", file=sys.stderr)
        else:
            print(f"ok   {f}")
    print(f"{len(files) - failed}/{len(files)} records valid")
    return 1 if failed else 0


def cmd_stats(ns: argparse.Namespace) -> int:
    print(json.dumps(stats(_registry(ns.registry)), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rclass", description="Reversibility Classes for agent actions")
    sub = p.add_subparsers(dest="cmd", required=True)

    lk = sub.add_parser("lookup", help="classify one tool call")
    lk.add_argument("server")
    lk.add_argument("tool")
    lk.add_argument("--args", help="tool arguments as JSON")
    lk.add_argument("--registry", action="append", help="registry path (repeatable); default: bundled")
    lk.add_argument("--json", action="store_true")
    lk.set_defaults(fn=cmd_lookup)

    va = sub.add_parser("validate", help="validate classification records")
    va.add_argument("paths", nargs="*")
    va.set_defaults(fn=cmd_validate)

    st = sub.add_parser("stats", help="count tools per class")
    st.add_argument("--registry", action="append")
    st.set_defaults(fn=cmd_stats)

    ns = p.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
