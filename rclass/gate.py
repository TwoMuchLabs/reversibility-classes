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

import hashlib
import json
import os
import re
import shlex
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
                   mapping: Mapping[str, str] | None = None,
                   fingerprints: dict[str, str] | None = None) -> dict[str, list[str | None]]:
    """Client server name -> the registry record(s) its config entries run (None = unrecognised).

    A name can appear in several scopes (user, project, local); every entry is kept and the caller
    takes the strictest, because the hook cannot know which scope the client chose.

    `mapping` (`--map NAME=RECORD`) vouches only for entries in the user's own configuration. An entry a
    repository defines (`.mcp.json`) keeps its own identification, so a repository cannot borrow a name the
    user vouched for. `fingerprints`, when given, is filled with a digest of each name's config entries.
    """
    mapped = {claude_name(k): v for k, v in (mapping or {}).items()}
    out: dict[str, list[str | None]] = {}
    seen: dict[str, list] = {}
    for p in configs:
        for cfg in sc.load_config(p, cwd=cwd, home=home):
            name = claude_name(cfg.key.split("/")[-1])
            if cfg.disabled:
                continue
            seen.setdefault(name, []).append([p.name, cfg.command, cfg.args, cfg.url, sorted(cfg.env.items())])
            if name in mapped and not cfg.project and not cfg.config_error:
                sid = mapped[name]
            elif cfg.config_error:
                sid = None
            elif cfg.remote:
                sid, _ = sc.identify_by_endpoint(cfg, registry)
            else:
                sid, _ = sc.identify_by_package(cfg, registry)
            out.setdefault(name, []).append(sid)
    if fingerprints is not None:
        for name, entries in seen.items():
            fingerprints[name] = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:16]
    return out


def project_dir(event: Mapping[str, Any]) -> Path:
    """The project Claude Code is running in. Claude Code sets CLAUDE_PROJECT_DIR for hooks; the event's
    `cwd` follows the agent's working directory, which a prompt-injected agent can move to a folder holding
    a decoy `.mcp.json`."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(event.get("cwd") or Path.cwd())


# --------------------------------------------------------------------------- session state

def state_dir() -> Path:
    base = os.environ.get("RCLASS_STATE_DIR") or os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "rclass"


class SessionState:
    """What the hook remembers about one Claude Code session, in `<state dir>/sessions/<id>.json`.

    - **Identity at first sight.** The config entries behind each server name are fingerprinted the
      first time the session uses that name. If they change later (the agent rewrote `.mcp.json`
      while the server it started keeps running), the name is treated as unrecognised for the rest
      of the session.
    - **Effective class** (SPEC 9): the highest class of any call so far.

    This raises the bar; it is not a boundary against an agent that can write anywhere on disk, which
    could delete the file. Enforce at a central gateway where that matters.
    """

    def __init__(self, session_id: str | None, root: Path | None = None):
        self.path = None
        self.data: dict[str, Any] = {"identity": {}, "effective": None}
        if session_id:
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id))[:128]
            self.path = (root or state_dir()) / "sessions" / f"{safe}.json"
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, ValueError):
                pass

    def check_identity(self, records: dict[str, list[str | None]], fingerprints: Mapping[str, str]) -> set[str]:
        """Names whose config changed since the session first used them; records become unrecognised."""
        changed = set()
        ident = self.data.setdefault("identity", {})
        for name, fp in fingerprints.items():
            if name not in ident:
                ident[name] = fp
            elif ident[name] != fp:
                changed.add(name)
                records[name] = [None]
        return changed

    def raise_to(self, rclass: RClass) -> RClass:
        prev = self.data.get("effective")
        eff = max(rclass, RClass.parse(prev)) if prev else rclass
        self.data["effective"] = str(eff)
        return eff

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # state is best effort; classification never depends on being able to write it


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
                  records: Mapping[str, list[str | None]], registry: Registry,
                  changed: set[str] = frozenset()) -> Classification:
    """The strictest class across every record this server name could be. Unrecognised means R4."""
    candidates = records.get(server_name)
    if not candidates or any(c is None for c in candidates):
        why = ("its config changed during this session" if server_name in changed
               else "not in any Claude Code config" if not candidates
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


def ask_on_error(stdout: TextIO, what: str) -> int:
    """The one thing a gate must not do is fail open: any problem becomes a prompt with the reason."""
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                      "permissionDecisionReason": f"rclass could not classify this call ({what}); "
                                      "asking instead."}}, stdout)
    stdout.write("\n")
    return 0


def run_hook(stdin: TextIO, stdout: TextIO, policy: GatePolicy, registry_factory,
             mapping: Mapping[str, str] | None = None, log_path: Path | None = None,
             home: Path | None = None, state_root: Path | None = None) -> int:
    """Claude Code PreToolUse hook. Always exits 0: a failure asks instead of silently allowing."""
    try:
        event = json.load(stdin)
        tool_name = str(event.get("tool_name", ""))
        if not tool_name.startswith("mcp__"):
            return 0  # not an MCP tool: no decision, the client's normal rules apply
        args = event.get("tool_input")
        args = args if isinstance(args, Mapping) else None
        cwd = project_dir(event)
        home = home or Path.home()
        registry = registry_factory()
        fps: dict[str, str] = {}
        records = server_records(claude_code_configs(cwd, home), registry, cwd, home, mapping, fps)
        state = SessionState(event.get("session_id"), state_root)
        changed = state.check_identity(records, fps)
        parsed = parse_mcp_tool(tool_name, records)
        if parsed is None:
            raise ValueError("malformed MCP tool name")
        server_name, tool = parsed
        c = classify_call(server_name, tool, args, records, registry, changed)
        decision = policy.decide(c.rclass)
        effective = state.raise_to(c.rclass)
        state.save()
        if log_path is not None:
            _log(log_path, event, server_name, tool, c, decision, effective)
        if decision is None:
            return 0
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                          "permissionDecisionReason": _reason(c, server_name, decision)}}, stdout)
        stdout.write("\n")
        return 0
    except Exception as exc:  # noqa: BLE001 - the one thing a gate must not do is fail open
        return ask_on_error(stdout, exc.__class__.__name__)


def _log(path: Path, event: Mapping[str, Any], server: str, tool: str, c: Classification, decision: str | None,
         effective: RClass | None = None) -> None:
    """One JSON line per decision (SPEC 9 and 10: the class, the rule behind it, the session's effective class;
    an override's reason). Arguments are not logged. Logging is best effort: a full disk never blocks a call."""
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": event.get("session_id"),
             "server": server, "tool": tool, "class": str(c.rclass), "source": c.source, "rule_index": c.rule_index,
             "effective": str(effective) if effective else None, "decision": decision or "client"}
    if c.source == "override":
        entry["rationale"] = c.rationale  # carries the override's logged reason (SPEC 5.5)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


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


def claude_hook_settings(extra_args: list[str], python: str | None = None) -> str:
    """Hook settings that run this exact interpreter (`python -m rclass`), not whatever `rclass` is on PATH:
    an IDE, terminal or container with a different PATH would otherwise run no hook at all."""
    # `python -m` puts the current folder first on sys.path, and Claude Code runs hooks in the project
    # folder, so a repository shipping its own `rclass/` package would run instead. -P (Python 3.11+)
    # leaves the current folder out; older versions drop it before importing.
    py = python or sys.executable
    if sys.version_info >= (3, 11):
        argv = [py, "-P", "-m", "rclass"]
    else:
        argv = [py, "-c", "import sys; del sys.path[0]; from rclass.cli import main; sys.exit(main())"]
    command = " ".join(shlex.quote(a) for a in [*argv, "hook", "claude-code", *extra_args])
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "mcp__.*",
                                                 "hooks": [{"type": "command", "command": command, "timeout": 30}]}]}},
                      indent=2) + "\n"


def main_hook(argv_policy: GatePolicy, mapping: Mapping[str, str], log: str | None, registry_factory) -> int:
    return run_hook(sys.stdin, sys.stdout, argv_policy, registry_factory, mapping, Path(log) if log else None)
