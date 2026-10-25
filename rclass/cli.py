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
            rec = read_record_file(f)
            validate_record(rec, str(f))
            if ns.strict:
                from .scan import DRAFT_MARKERS
                text = f.read_text(encoding="utf-8")
                found = [m for m in DRAFT_MARKERS if m in text]
                if found:
                    raise RecordError(f"{f}:\n  unfinished draft: still contains {', '.join(found)}")
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


EXIT_OK, EXIT_THRESHOLD, EXIT_USAGE, EXIT_INCOMPLETE = 0, 1, 2, 3


def _parse_assume(values: list[str] | None) -> dict[str, str]:
    out = {}
    for v in values or []:
        key, sep, rec = v.partition("=")
        if not sep or not key or not rec:
            raise SystemExit(f"--assume takes SERVER=RECORD, e.g. --assume my-git=git (got {v!r})")
        out[key] = rec
    return out


def cmd_scan(ns: argparse.Namespace) -> int:
    """Exit codes: 0 ok; 1 a tool (or an unidentifiable server) is at or above --fail-on;
    2 usage or config error; 3 the scan was incomplete (a server could not be scanned) and
    --fail-on was given, or every server failed."""
    from . import scan as sc

    explicit = bool(ns.configs)
    configs = [Path(p).expanduser() for p in ns.configs] or sc.known_config_paths()
    missing = [str(p) for p in configs if not p.is_file()]
    if missing:
        print(f"config not found: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    if not configs:
        print("No MCP client configuration found. Looked for Claude Desktop, Claude Code (~/.claude.json, "
              ".mcp.json), Cursor, VS Code (.vscode/mcp.json) and Windsurf.\n"
              "Pass a config file explicitly: rclass scan path/to/mcp.json", file=sys.stderr)
        return EXIT_USAGE
    registry = _registry(ns.registry, ns.override)
    assume = _parse_assume(ns.assume)
    try:
        servers = sc.collect(configs, all_projects=ns.all_projects)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    if not servers:
        print(f"No MCP servers configured in {', '.join(map(str, configs))}.", file=sys.stderr)
        return EXIT_USAGE
    if ns.server:
        chosen = [s for s in servers if s.key in ns.server or s.key.split("/")[-1] in ns.server]
        if not chosen:
            names = ", ".join(sc.clean(s.key, 40) for s in servers)
            print(f"No server named {', '.join(ns.server)}. Configured: {names}.", file=sys.stderr)
            return EXIT_USAGE
        servers = chosen

    start = not ns.list
    consent = ns.yes
    pending = [s for s in servers if s.project and not s.approved and not s.disabled and not s.remote]
    if start and pending and not consent and sys.stdin.isatty() and sys.stderr.isatty() and not ns.json:
        cfgs = sorted({s.source for s in pending})
        print(f"{', '.join(cfgs)} {'is a project config' if len(cfgs) == 1 else 'are project configs'}: "
              f"{len(pending)} server{'s' if len(pending) != 1 else ''} defined by the repository "
              f"({', '.join(sc.clean(s.key, 30) for s in pending)}).", file=sys.stderr)
        try:
            consent = input("Start them to list their tools? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            consent = False

    if not ns.json:
        local = [s for s in servers if not s.remote and not s.disabled and not s.config_error]
        if start and local:
            print(f"Starting {len(local)} local server{'s' if len(local) != 1 else ''} to list their tools. "
                  "Each gets only the environment MCP clients pass by default"
                  + (" (--inherit-env: your whole environment)" if ns.inherit_env else "")
                  + ", plus its own config. --list shows them without starting anything.\n", file=sys.stderr)
        elif not start:
            print("Not starting anything (--list): recognised servers are shown with the registry's classes.\n",
                  file=sys.stderr)

    result = sc.scan_servers(servers, [str(p) for p in configs], registry, timeout=ns.timeout, start=start,
                             inherit_env=ns.inherit_env, assume=assume,
                             show_server_errors=ns.show_server_errors, consent=consent)

    drafts = 0
    if ns.write_drafts:
        out = Path(ns.write_drafts)
        out.mkdir(parents=True, exist_ok=True)
        for r in result.servers:
            todo = [t for t in r.tools if t.source == "fail-closed"]
            if r.status == "scanned" and todo:
                target = sc.draft_path(out, r)
                target.write_text(sc.draft_record(r, todo), encoding="utf-8")
                drafts += 1
                print(f"draft record: {target} ({len(todo)} tool{'s' if len(todo) != 1 else ''} to classify)", file=sys.stderr)

    if ns.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(sc.render_text(result, details=ns.details, drafts_written=drafts > 0,
                             show_server_errors=ns.show_server_errors), end="")

    reported = [s for s in result.servers if s.status in ("scanned", "static", "remote")]
    if not reported:
        return EXIT_INCOMPLETE
    if ns.fail_on:
        threshold = RClass.parse(ns.fail_on)
        worst = result.worst()
        if worst is not None and worst >= threshold:
            if not ns.json:
                print(f"\nfailing: something reachable is {worst} (threshold {threshold})", file=sys.stderr)
            return EXIT_THRESHOLD
        if result.incomplete():
            if not ns.json:
                print(f"\nincomplete: {len(result.incomplete())} server(s) not scanned, so the gate cannot pass",
                      file=sys.stderr)
            return EXIT_INCOMPLETE
    return EXIT_OK


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
    p = argparse.ArgumentParser(prog="rclass", description="Reversibility Classes for agent actions",
                                epilog="rclass scan exit codes: 0 ok, 1 threshold reached, 2 usage or config error, "
                                       "3 incomplete scan")
    from .scan import rclass_version
    p.add_argument("--version", action="version", version=f"rclass {rclass_version()}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser(
        "scan",
        help="audit the MCP servers in a client config: what could an agent do, by class?",
        description="Start each MCP server in a client configuration, list its tools, and classify them. "
        "With no config file, looks in the usual places for Claude Desktop, Claude Code, Cursor, VS Code and Windsurf.",
    )
    sc.add_argument("configs", nargs="*", help="client config files (default: discover)")
    sc.add_argument("--server", action="append", help="only this server (config key; repeatable)")
    sc.add_argument("--list", action="store_true",
                    help="start nothing: show recognised servers with the registry's classes (safe for CI)")
    sc.add_argument("--yes", action="store_true", help="start servers defined by a project's own config without asking")
    sc.add_argument("--assume", action="append", metavar="SERVER=RECORD",
                    help="treat a server as a registry record, e.g. --assume my-git=git (repeatable)")
    sc.add_argument("--inherit-env", action="store_true",
                    help="give servers your whole environment, not just what MCP clients pass by default")
    sc.add_argument("--show-server-errors", action="store_true",
                    help="show what failing servers reported, with config values and token shapes redacted (best effort)")
    sc.add_argument("--all-projects", action="store_true",
                    help="include every project in ~/.claude.json, not just the current folder's")
    sc.add_argument("--details", action="store_true", help="list every tool with its class")
    sc.add_argument("--json", action="store_true", help="machine-readable output")
    sc.add_argument("--timeout", type=float, default=60.0, help="seconds per server (default 60)")
    sc.add_argument("--fail-on", metavar="CLASS", choices=[f"R{i}" for i in range(5)],
                    help="exit 1 if anything reachable is at or above CLASS (R0-R4); exit 3 if the scan is incomplete")
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
    va.add_argument("--strict", action="store_true", help="also reject unfinished drafts written by `rclass scan --write-drafts`")
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
