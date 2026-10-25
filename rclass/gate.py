"""Enforce Reversibility Classes where agents act: a Claude Code hook, and policy exports for gateways.

`rclass hook claude-code` is a PreToolUse hook. Claude Code sends it each MCP tool call (`mcp__<server>__<tool>`
with its arguments) and it answers allow, ask or deny. Because it sees the arguments, it uses the class of *this*
call: `execute_sql` with a SELECT is R0, the same tool with DROP TABLE is R4.

`rclass policy --format ...` writes static policy for clients and gateways that cannot see arguments
(Claude Code permission rules, Docker's Cedar MCP policies, agentgateway's CEL rules). Those use each tool's
worst-case class, so they are never less strict than the hook.

Servers are identified the way `rclass scan --list` does it, from what the config runs, never from tool names.
An unrecognised server or tool is R4.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

from . import scan as sc
from .classify import classify
from .model import Classification, RClass
from .registry import Registry

LABEL = {"allow": "allow", "ask": "ask", "deny": "deny", None: "no decision"}


# --------------------------------------------------------------------------- policy

class GatePolicy:
    """Which decision each class gets. Defaults follow SPEC 8 without loosening the client:

    R0-R1 get no decision (the client's own permission rules apply), R2-R4 always ask.
    `allow_up_to` lets low classes run without a prompt; `deny_from` blocks high classes outright.
    """

    def __init__(self, allow_up_to: RClass | None = None, deny_from: RClass | None = None):
        if allow_up_to is not None and allow_up_to >= RClass.R2:
            raise ValueError("--allow-up-to can be R0 or R1: R2 and above need a human by default (SPEC 8)")
        if deny_from is not None and allow_up_to is not None and deny_from <= allow_up_to:
            raise ValueError("--deny-from must be above --allow-up-to")
        self.allow_up_to, self.deny_from = allow_up_to, deny_from

    def decide(self, rclass: RClass) -> str | None:
        if self.deny_from is not None and rclass >= self.deny_from:
            return "deny"
        if rclass >= RClass.R2:
            return "ask"
        if self.allow_up_to is not None and rclass <= self.allow_up_to:
            return "allow"
        return None


# --------------------------------------------------------------------------- identifying servers

def claude_code_configs(cwd: Path, home: Path) -> list[Path]:
    """Only Claude Code's own configs: another client's server with the same name is a different server."""
    return [p for p in (home / ".claude.json", cwd / ".mcp.json") if p.is_file()]


def server_records(configs: Iterable[Path], registry: Registry, cwd: Path, home: Path,
                   mapping: Mapping[str, str] | None = None) -> dict[str, list[str | None]]:
    """Client server name -> the registry record(s) its config entries run (None = unrecognised).

    A name can appear in several scopes (user, project, local); every entry is kept and the caller
    takes the strictest, because the hook cannot know which scope the client chose.
    """
    out: dict[str, list[str | None]] = {}
    for p in configs:
        for cfg in sc.load_config(p, cwd=cwd, home=home):
            name = claude_name(cfg.key.split("/")[-1])
            if cfg.disabled:
                continue
            if cfg.config_error:
                sid = None
            elif cfg.remote:
                sid, _ = sc.identify_by_endpoint(cfg, registry)
            else:
                sid, _ = sc.identify_by_package(cfg, registry)
            out.setdefault(name, []).append(sid)
    for name, sid in (mapping or {}).items():
        out[claude_name(name)] = [sid]  # an explicit operator mapping replaces identification for that name
    return out


def claude_name(server: str) -> str:
    """How Claude Code writes a configured server name in tool names: `.`, spaces and the like become `_`."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", server)


def parse_mcp_tool(tool_name: str, known: Iterable[str] = ()) -> tuple[str, str] | None:
    """`mcp__<server>__<tool>` -> (server, tool), in Claude Code's naming.

    A server name may itself contain `__`, so the longest configured name that fits wins; otherwise the
    first `__` splits. Plugin servers (`mcp__plugin_...`) simply come out unrecognised, which is R4.
    """
    if not tool_name.startswith("mcp__"):
        return None
    rest = tool_name[len("mcp__"):]
    for name in sorted(known, key=len, reverse=True):
        if rest.startswith(name + "__") and len(rest) > len(name) + 2:
            return name, rest[len(name) + 2:]
    server, sep, tool = rest.partition("__")
    if not sep or not server or not tool:
        return None
    return server, tool


def classify_call(server_name: str, tool: str, args: Mapping[str, Any] | None,
                  records: Mapping[str, list[str | None]], registry: Registry) -> Classification:
    """The strictest class across every record this server name could be. Unrecognised means R4."""
    candidates = records.get(server_name)
    if not candidates or any(c is None for c in candidates):
        why = ("not in any Claude Code config" if not candidates
               else "its config does not run a registry server as itself")
        return Classification(RClass.R4, "fail-closed",
                              f"Server '{server_name}' is unrecognised ({why}); failing closed.", server_name, tool)
    results = [classify(sid, tool, args, registry=registry).unconfirmed() for sid in dict.fromkeys(candidates)]
    return max(results, key=lambda c: c.rclass)


# --------------------------------------------------------------------------- the hook

def _reason(c: Classification, server_name: str, decision: str | None) -> str:
    rule = f", rule {c.rule_index}" if c.rule_index is not None else ""
    head = f"rclass: {c.rclass} {c.rclass.label} ({c.source}{rule})"
    tail = {"deny": " Blocked by policy.", "ask": " Needs your approval."}.get(decision or "", "")
    return f"{head}. {c.rationale}{tail}"


def run_hook(stdin: TextIO, stdout: TextIO, policy: GatePolicy, registry_factory,
             mapping: Mapping[str, str] | None = None, log_path: Path | None = None,
             home: Path | None = None) -> int:
    """Claude Code PreToolUse hook. Always exits 0: a failure asks instead of silently allowing."""
    try:
        event = json.load(stdin)
        tool_name = str(event.get("tool_name", ""))
        if not tool_name.startswith("mcp__"):
            return 0  # not an MCP tool: no decision, the client's normal rules apply
        args = event.get("tool_input")
        args = args if isinstance(args, Mapping) else None
        cwd = Path(event.get("cwd") or Path.cwd())
        home = home or Path.home()
        registry = registry_factory()
        records = server_records(claude_code_configs(cwd, home), registry, cwd, home, mapping)
        parsed = parse_mcp_tool(tool_name, records)
        if parsed is None:
            raise ValueError("malformed MCP tool name")
        server_name, tool = parsed
        c = classify_call(server_name, tool, args, records, registry)
        decision = policy.decide(c.rclass)
        if log_path is not None:
            _log(log_path, event, server_name, tool, c, decision)
        if decision is None:
            return 0
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                          "permissionDecisionReason": _reason(c, server_name, decision)}}, stdout)
        stdout.write("\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - the one thing a gate must not do is fail open
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                          "permissionDecisionReason": f"rclass could not classify this call "
                                          f"({exc.__class__.__name__}); asking instead."}}, stdout)
        stdout.write("\n")
        return 0


def _log(path: Path, event: Mapping[str, Any], server: str, tool: str, c: Classification, decision: str | None) -> None:
    """One JSON line per decision (SPEC 10: log the class and the rule behind it). Arguments are not logged."""
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": event.get("session_id"),
             "server": server, "tool": tool, "class": str(c.rclass), "source": c.source, "rule_index": c.rule_index,
             "decision": decision or "client"}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- static policy exports

def static_scan(configs: list[Path], registry: Registry, cwd: Path | None = None,
                home: Path | None = None) -> "sc.ScanResult":
    """Identify every server entry without starting it. Unlike a scan report, identical entries are kept:
    a client exposes each under its own name, so each needs its own policy."""
    servers = sc.collect(configs, cwd=cwd, home=home)
    return sc.ScanResult([str(p) for p in configs],
                         [sc.scan_server(s, registry, start=False) for s in servers], started=False)


def static_classes(result: "sc.ScanResult") -> tuple[dict[str, dict[str, RClass]], list[str]]:
    """Per server name, each tool's worst-case class; plus the names that could not be identified."""
    known: dict[str, dict[str, RClass]] = {}
    unknown: list[str] = []
    for rep in result.servers:
        name = rep.key.split("/")[-1]
        if rep.status == "skipped":
            continue
        if not rep.record:
            unknown.append(name)
            continue
        tools = known.setdefault(name, {})
        for t in rep.tools:
            tools[t.name] = max(tools.get(t.name, RClass.R0), t.rclass)
    return known, sorted(set(unknown))


def _groups(tools: Mapping[str, RClass]) -> dict[RClass, list[str]]:
    g: dict[RClass, list[str]] = {}
    for name, cls in sorted(tools.items()):
        g.setdefault(cls, []).append(name)
    return g


def _header(comment: str, source: str) -> list[str]:
    return [f"{comment} Generated by rclass {sc.rclass_version()} from {source}.",
            f"{comment} Classes are each tool's worst case: this policy cannot see arguments.",
            f"{comment} For per-call classes in Claude Code, use the hook: rclass policy --format claude-hook"]


def to_cedar(known: Mapping[str, Mapping[str, RClass]], unknown: list[str], policy: GatePolicy, source: str) -> str:
    """Docker MCP governance (Cedar). Default deny: unlisted tools and unrecognised servers stay denied."""
    q = json.dumps  # Cedar string literals use the same escapes for what tool and server names contain
    out = _header("//", source) + [
        "// Docker denies by default, so tools not listed here, and unrecognised servers, stay denied.",
        "// This covers tool calls only; server registration, resources and prompts need their own policies."]
    for u in unknown:
        out.append(f"// {u}: not recognised from its config, so none of its tools are permitted (R4).")
    for server, tools in sorted(known.items()):
        out.append("")
        out.append(f"// {server}")
        for cls, names in sorted(_groups(tools).items()):
            cond = (f"resource in MCP::Server::{q(server)} && "
                    f"[{', '.join(q(n) for n in names)}].contains(resource.name)")
            decision = policy.decide(cls)
            if decision == "deny":
                out.append(f"// {cls} {cls.label}: blocked")
                out.append(f'forbid (principal, action == MCP::Action::"invokeTool", resource)\nwhen {{ {cond} }};')
                continue
            if decision == "ask":
                out.append(f"@requireApproval({q(f'{cls} {cls.label} (rclass): needs a human')})")
            else:
                out.append(f"// {cls} {cls.label}")
            out.append(f'permit (principal, action == MCP::Action::"invokeTool", resource)\nwhen {{ {cond} }};')
    return "\n".join(out) + "\n"


def to_cel(known: Mapping[str, Mapping[str, RClass]], unknown: list[str], policy: GatePolicy, source: str) -> str:
    """agentgateway mcpAuthorization rules (CEL). No approval step exists, so only allowed classes are listed."""
    import yaml
    up_to = policy.allow_up_to if policy.allow_up_to is not None else RClass.R1
    rules = []
    for server, tools in sorted(known.items()):
        names = sorted(n for n, c in tools.items() if c <= up_to)
        if names:
            rules.append(f"mcp.tool.target == {json.dumps(server)} && mcp.tool.name in [{', '.join(json.dumps(n) for n in names)}]")
    head = _header("#", source) + [
        f"# agentgateway has no approval step: only tools at {up_to} or below are allowed;",
        "# every other tool is filtered out of tools/list and refused. Target names must match the config's server names."]
    for u in unknown:
        head.append(f"# {u}: not recognised from its config, so none of its tools are allowed (R4).")
    body = yaml.safe_dump({"mcpAuthorization": {"rules": rules}}, sort_keys=False, width=10_000, allow_unicode=True)
    return "\n".join(head) + "\n" + body


def to_claude_settings(known: Mapping[str, Mapping[str, RClass]], unknown: list[str], policy: GatePolicy) -> str:
    """Claude Code permission rules. ask and deny take precedence over a user's allow rules."""
    perms: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
    for server, tools in sorted(known.items()):
        for name, cls in sorted(tools.items()):
            d = policy.decide(cls)
            if d:
                perms[d].append(f"mcp__{claude_name(server)}__{name}")
    for u in unknown:  # the wildcard form is the one that matches every tool of any server name
        perms["deny" if policy.deny_from is not None else "ask"].append(f"mcp__{claude_name(u)}__*")
    return json.dumps({"permissions": {k: v for k, v in perms.items() if v}}, indent=2) + "\n"


def claude_hook_settings(extra_args: list[str]) -> str:
    command = " ".join(["rclass", "hook", "claude-code", *extra_args])
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "mcp__.*",
                                                 "hooks": [{"type": "command", "command": command}]}]}}, indent=2) + "\n"


def main_hook(argv_policy: GatePolicy, mapping: Mapping[str, str], log: str | None, registry_factory) -> int:
    return run_hook(sys.stdin, sys.stdout, argv_policy, registry_factory, mapping, Path(log) if log else None)
