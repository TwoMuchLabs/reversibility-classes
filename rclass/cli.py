"""Command line: `rclass scan`, `rclass lookup`, `rclass validate`, `rclass stats`, `rclass export`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .classify import classify
from .policy import DEFAULT_POLICY
from .model import RClass
from .registry import RecordError, Registry, read_record_file, stats, validate_record


def _registry(paths: list[str] | None, overrides: list[str] | None = None, extend: bool = True) -> Registry:
    """The bundled registry plus any extra record paths (SPEC 5.4: the highest class across them wins).
    With extend=False, the given paths replace the bundled registry."""
    if paths and not extend:
        return Registry.from_paths(paths)
    reg = Registry.bundled()
    for p in paths or []:
        extra = Registry.from_paths([p])
        for rec in extra.records:
            reg.add(rec, p)
    for p in overrides or []:
        rec = read_record_file(Path(p))
        reason = (rec.get("server") or {}).get("notes")
        if not reason:
            raise SystemExit(f"{p}: an override must say why in server.notes; it is logged with every decision (SPEC 5.5)")
        reg.add_override(rec, reason, p)
    return reg


def cmd_lookup(ns: argparse.Namespace) -> int:
    try:
        args = json.loads(ns.args) if ns.args else None
    except json.JSONDecodeError as exc:
        print(f"--args is not valid JSON: {exc}", file=sys.stderr)
        return 2
    c = classify(ns.server, ns.tool, args, registry=_registry(ns.registry, ns.override))
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
    print(json.dumps(stats(_registry(ns.registry, extend=False)), indent=2))
    return 0


def cmd_scan(ns: argparse.Namespace) -> int:
    from . import scan as sc

    configs = [Path(p).expanduser() for p in ns.configs] or sc.known_config_paths()
    missing = [str(p) for p in configs if not p.is_file()]
    if missing:
        print(f"config not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not configs:
        print(
            "No MCP client configuration found. Looked for Claude Desktop, Claude Code (~/.claude.json, "
            ".mcp.json), Cursor, VS Code (.vscode/mcp.json) and Windsurf.\n"
            "Pass a config file explicitly: rclass scan path/to/mcp.json",
            file=sys.stderr,
        )
        return 2
    registry = _registry(ns.registry, ns.override)
    try:
        servers = [s for p in configs for s in sc.load_config(p)]
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if ns.server:
        servers = [s for s in servers if s.key in ns.server or s.key.split("/")[-1] in ns.server]
    if not servers:
        print(f"No MCP servers configured in {', '.join(map(str, configs))}.", file=sys.stderr)
        return 2

    local = [s for s in servers if not s.remote and not s.disabled]
    if not ns.json:
        verb = "Listing" if ns.list else f"Starting {len(local)} local server{'s' if len(local) != 1 else ''} to list their tools"
        print(f"{verb} from {', '.join(map(str, configs))}.", file=sys.stderr)
        if local and not ns.list:
            print("They run with the same command and environment your client uses. "
                  "Use --list to see them without starting anything.\n", file=sys.stderr)

    reports = [sc.scan_server(s, registry, timeout=ns.timeout, start=not ns.list) for s in servers]
    result = sc.ScanResult([str(p) for p in configs], reports)

    if ns.write_drafts:
        out = Path(ns.write_drafts)
        out.mkdir(parents=True, exist_ok=True)
        for r in reports:
            todo = [t for t in r.tools if t.source == "fail-closed"]
            if r.status == "scanned" and todo:
                target = out / f"{sc._slug(r.record or r.server_info.get('name') or r.key.split('/')[-1])}.yaml"
                target.write_text(sc.draft_record(r, todo), encoding="utf-8")
                print(f"draft record: {target} ({len(todo)} tools to classify)", file=sys.stderr)

    print(json.dumps(result.to_dict(), indent=2) if ns.json else sc.render_text(result, details=ns.details), end="" if not ns.json else "\n")

    if not any(r.status in ("scanned", "remote") for r in reports) and not ns.list:
        return 2
    if ns.fail_on:
        threshold = RClass.parse(ns.fail_on)
        worst = result.worst()
        if worst is not None and worst >= threshold:
            if not ns.json:
                print(f"\nfailing: at least one tool is {worst} (threshold {threshold})", file=sys.stderr)
            return 1
    return 0


def cmd_export(ns: argparse.Namespace) -> int:
    reg = _registry(ns.registry, extend=False)
    doc = {
        "records": reg.records,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=None if ns.compact else 2)
    if ns.out:
        Path(ns.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {ns.out}: {len(reg.records)} records, {len(reg.all_entries())} tools", file=sys.stderr)
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rclass", description="Reversibility Classes for agent actions")
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser(
        "scan",
        help="audit the MCP servers in a client config: what could an agent do, by class?",
        description="Start each MCP server in a client configuration, list its tools, and classify them. "
        "With no config file, looks in the usual places for Claude Desktop, Claude Code, Cursor, VS Code and Windsurf.",
    )
    sc.add_argument("configs", nargs="*", help="client config files (default: discover)")
    sc.add_argument("--server", action="append", help="only this server (config key; repeatable)")
    sc.add_argument("--list", action="store_true", help="show configured servers without starting them")
    sc.add_argument("--details", action="store_true", help="list every tool with its class")
    sc.add_argument("--json", action="store_true", help="machine-readable output")
    sc.add_argument("--timeout", type=float, default=60.0, help="seconds per server (default 60)")
    sc.add_argument("--fail-on", metavar="CLASS", help="exit 1 if any tool is at or above CLASS, e.g. R3 (for CI)")
    sc.add_argument("--write-drafts", metavar="DIR", help="write draft records for unclassified tools, to contribute")
    sc.add_argument("--registry", action="append", help="extra registry path (repeatable)")
    sc.add_argument("--override", action="append", help="deployment override record (repeatable; reason in server.notes)")
    sc.set_defaults(fn=cmd_scan)

    lk = sub.add_parser("lookup", help="classify one tool call")
    lk.add_argument("server")
    lk.add_argument("tool")
    lk.add_argument("--args", help="tool arguments as JSON")
    lk.add_argument("--registry", action="append", help="registry path (repeatable); default: bundled")
    lk.add_argument("--override", action="append", help="deployment override record (repeatable)")
    lk.add_argument("--json", action="store_true")
    lk.set_defaults(fn=cmd_lookup)

    va = sub.add_parser("validate", help="validate classification records")
    va.add_argument("paths", nargs="*")
    va.set_defaults(fn=cmd_validate)

    st = sub.add_parser("stats", help="count tools per class")
    st.add_argument("--registry", action="append")
    st.set_defaults(fn=cmd_stats)

    ex = sub.add_parser("export", help="the registry as one JSON document, for other tools and languages")
    ex.add_argument("--out", help="write to this file instead of stdout")
    ex.add_argument("--compact", action="store_true")
    ex.add_argument("--registry", action="append")
    ex.set_defaults(fn=cmd_export)

    ns = p.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
