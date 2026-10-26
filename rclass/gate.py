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
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

from . import scan as sc
from .classify import classify
from .model import Classification, RClass
from .registry import Registry
from .versions import pinned_version

LABEL = {"allow": "allow", "ask": "ask", "deny": "deny", None: "no decision"}


# --------------------------------------------------------------------------- policy

class GatePolicy:
    """Which decision each class gets. Defaults follow SPEC 8 without loosening the client:

    R0-R1 get no decision (the client's own permission rules apply), R2-R4 always ask.
    `allow_up_to` lets low classes run without a prompt; `deny_from` blocks high classes outright.
    """

    def __init__(self, allow_up_to: RClass | None = None, deny_from: RClass | None = None,
                 egress_allow: Iterable[str] = ()):
        self.egress_allow = [h.lower().lstrip(".") for h in egress_allow if h]
        if allow_up_to is not None and allow_up_to >= RClass.R2:
            raise ValueError("--allow-up-to can be R0 or R1: R2 and above need a human by default (SPEC 8)")
        if deny_from is not None and allow_up_to is not None and deny_from <= allow_up_to:
            raise ValueError("--deny-from must be above --allow-up-to")
        self.allow_up_to, self.deny_from = allow_up_to, deny_from

    def host_allowed(self, host: str | None) -> bool:
        """`--egress-allow example.com` allows example.com and its subdomains."""
        if not host:
            return False
        host = host.lower().rstrip(".")
        return any(host == h or host.endswith("." + h) for h in self.egress_allow)

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
            version, pinned = (None, False)
            if not cfg.remote and not cfg.config_error:
                target, _ = sc.run_target(cfg.command, cfg.args, cfg.env, cfg.project_root)
                version, pinned = pinned_version(target)
            msid, mver, mpinned, _ = (None, None, False, None) if cfg.config_error else \
                sc.identify_by_mapping(cfg, registry)
            if name in mapped and not cfg.project and not cfg.config_error:
                sid = mapped[name]
            elif cfg.config_error:
                sid = None
            elif msid:
                sid, version, pinned = msid, mver, mpinned  # the platform team's mapping (rclass/servers.py)
            elif cfg.remote:
                sid, _ = sc.identify_by_endpoint(cfg, registry)
            else:
                sid, _ = sc.identify_by_package(cfg, registry)
            out.setdefault(name, []).append((sid, version, pinned) if sid else None)
    if fingerprints is not None:
        for name, entries in seen.items():
            fingerprints[name] = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:16]
    return out


def config_fingerprints(configs: Iterable[Path], cwd: Path, home: Path) -> dict[str, str]:
    """The same per-name digests `server_records` computes, without loading the registry."""
    seen: dict[str, list] = {}
    for p in configs:
        for cfg in sc.load_config(p, cwd=cwd, home=home):
            if cfg.disabled:
                continue
            seen.setdefault(claude_name(cfg.key.split("/")[-1]), []).append(
                [p.name, cfg.command, cfg.args, cfg.url, sorted(cfg.env.items())])
    return {n: hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()[:16] for n, e in seen.items()}


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

    def __init__(self, session_id: str | None, root: Path | None = None, transcript: str | None = None):
        self.path = None
        self.degraded = False  # state cannot be kept: the guard then assumes the session has read data
        self.data: dict[str, Any] = {"identity": {}, "effective": None}
        if session_id:
            digest = hashlib.sha256(str(session_id).encode()).hexdigest()[:32]
            self.path = (root or state_dir()) / "sessions" / f"{digest}.json"
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.degraded = not os.access(self.path.parent, os.W_OK)
            except OSError:
                self.degraded = True
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except FileNotFoundError:
                # No state, but the session has already used tools: the file was deleted, so assume a read.
                if _transcript_has_tool_use(transcript):
                    self.data["read"] = True
            except (OSError, ValueError):
                self.degraded = True

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

    @property
    def has_read(self) -> bool:
        return self.degraded or bool(self.data.get("read"))

    def mark_read(self) -> None:
        self.data["read"] = True

    def raise_to(self, rclass: RClass) -> RClass:
        prev = self.data.get("effective")
        eff = max(rclass, RClass.parse(prev)) if prev else rclass
        self.data["effective"] = str(eff)
        return eff

    def save(self) -> None:
        """Merge into the file under a lock, so parallel hook runs never undo each other: `read` only ever
        turns on, the effective class only rises, and the first fingerprint seen for a name stays."""
        if self.path is None:
            return
        try:
            import fcntl
        except ImportError:  # Windows: no advisory lock; last writer wins
            fcntl = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.with_suffix(".lock"), "a+") as lock:
                if fcntl:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    disk = json.loads(self.path.read_text(encoding="utf-8"))
                    disk = disk if isinstance(disk, dict) else {}
                except (OSError, ValueError):
                    disk = {}
                merged = dict(self.data)
                merged["read"] = bool(disk.get("read")) or bool(self.data.get("read"))
                effs = [RClass.parse(x) for x in (disk.get("effective"), self.data.get("effective")) if x]
                merged["effective"] = str(max(effs)) if effs else None
                ident = dict(self.data.get("identity") or {})
                ident.update(disk.get("identity") or {})  # what was seen first wins
                merged["identity"] = ident
                merged["snapshot"] = bool(disk.get("snapshot")) or bool(self.data.get("snapshot"))
                tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(merged), encoding="utf-8")
                tmp.replace(self.path)
                self.data = merged
        except OSError:
            self.degraded = True


def _transcript_has_tool_use(path: str | None) -> bool:
    """Whether a Claude Code transcript already records a tool call (read only when state is missing)."""
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            return any(b'"tool_use"' in line for line in f)
    except OSError:
        return False


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


def _ident(c):
    """A candidate is a record id, or (record id, deployed version, pinned); None is unrecognised."""
    if c is None:
        return None
    return (c, None, False) if isinstance(c, str) else tuple(c)


def classify_call(server_name: str, tool: str, args: Mapping[str, Any] | None,
                  records: Mapping[str, list], registry: Registry,
                  changed: set[str] = frozenset()) -> Classification:
    """The strictest class across every record this server name could be. Unrecognised means R4.
    A deployment pinned to a release older than a record's `min_version` gets no class-lowering rules."""
    candidates = records.get(server_name)
    if not candidates or any(c is None for c in candidates):
        why = ("its config changed during this session" if server_name in changed
               else "not in any Claude Code config" if not candidates
               else "its config does not run a registry server as itself")
        return Classification(RClass.R4, "fail-closed",
                              f"Server '{server_name}' is unrecognised ({why}); failing closed.", server_name, tool)
    results = []
    for sid, version, pinned in dict.fromkeys(_ident(c) for c in candidates):
        results.append(classify(sid, tool, args, registry=registry, server_version=version,
                                version_pinned=pinned).unconfirmed())
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


# Claude Code's own tools that send caller-chosen data to a caller-chosen place. Search tools send queries
# only to their provider, which is not a channel an attacker can read, so they are not egress.
BUILTIN_EGRESS = {"WebFetch"}
# Claude Code's own housekeeping: these bring no outside data into the session, so they don't count as a read
# for the session guard. Found in a live run: every session starts with ToolSearch (loading deferred tool
# definitions), which otherwise made every later WebFetch ask.
BUILTIN_NO_DATA = {"ToolSearch", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskStop",
                   "EnterPlanMode", "ExitPlanMode"}
URL_KEYS = ("url", "uri", "href", "link", "endpoint")


_HOST_OK = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")


def _strings(value: Any, depth: int = 0):
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value[:100]:
            yield from _strings(v, depth + 1)


def destinations(args: Mapping[str, Any] | None) -> list[str | None]:
    """Every destination host an egress call names: the URL-named arguments, and any argument value that
    looks like a URL. A host that parsers could disagree about (userinfo, backslashes, non-ASCII, an empty
    host) comes back as None, which is never allow-listed."""
    values = [v for k in URL_KEYS for v in [(args or {}).get(k)] if isinstance(v, str) and v]
    values += [s for s in _strings(args) if "://" in s and s not in values]
    out: list[str | None] = []
    for v in values:
        v = v.strip()
        if "\\" in v or any(ord(ch) < 33 or ord(ch) > 126 for ch in v):
            out.append(None)
            continue
        try:
            parts = urlsplit(v if "//" in v else "//" + v)
            host = (parts.hostname or "").rstrip(".")
        except ValueError:
            out.append(None)
            continue
        out.append(host if host and "@" not in parts.netloc and _HOST_OK.match(host) else None)
    return out


def destination_host(args: Mapping[str, Any] | None) -> str | None:
    d = destinations(args)
    return d[0] if d else None


def session_guard(c: Classification, egress: bool, args: Mapping[str, Any] | None, state: "SessionState",
                  policy: GatePolicy) -> Classification:
    """SPEC 9: a call that can carry data to a destination the agent chooses is R4 once the session has read
    anything, unless the destination is allow-listed. Any other call counts as a read."""
    if not egress:
        state.mark_read()
        return c
    hosts = destinations(args)
    allowed = bool(hosts) and all(policy.host_allowed(h) for h in hosts)
    before = state.has_read
    state.mark_read()  # what an egress call brings back (a page, a response) is data the session has now read
    if before and not allowed and c.rclass < RClass.R4:
        named = sorted({h for h in hosts if h})
        where = f" ({', '.join(named)})" if named else ""
        return Classification(RClass.R4, "session", "Sends data to a destination the agent chooses" + where +
                              ", after this session has read data, so it could carry that data out (SPEC 9). "
                              "Allow trusted hosts with --egress-allow.", c.server, c.tool)
    return c


# Built-in tools that could rewrite the hook's own state or the settings that load it.
_WRITERS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def touches_guard(tool_name: str, args: Mapping[str, Any] | None) -> bool:
    if tool_name not in _WRITERS:
        return False
    text = " ".join(_strings(args)).lower()
    marks = [str(state_dir()).lower(), "rclass/sessions", "rclass/audit", ".claude/settings", "managed-settings"]
    return any(m in text for m in marks)


def _emit(stdout: TextIO, decision: str, reason: str) -> None:
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                      "permissionDecisionReason": reason}}, stdout)
    stdout.write("\n")


def run_hook(stdin: TextIO, stdout: TextIO, policy: GatePolicy, registry_factory,
             mapping: Mapping[str, str] | None = None, log_path: Path | None = None,
             home: Path | None = None, state_root: Path | None = None) -> int:
    """Claude Code PreToolUse hook. Always exits 0: a failure asks instead of silently allowing."""
    try:
        event = json.load(stdin)
        tool_name = str(event.get("tool_name", ""))
        if event.get("hook_event_name") == "PostToolUse":
            # Record that a call ran, for the tools the hook makes decisions about; never a decision itself.
            if log_path is not None and (tool_name.startswith("mcp__") or tool_name in BUILTIN_EGRESS | _WRITERS):
                _log_outcome(log_path, event)
            return 0
        args = event.get("tool_input")
        args = args if isinstance(args, Mapping) else None
        if not tool_name.startswith("mcp__"):
            # Claude Code's own tools: no class, but they count for the session guard. No registry is loaded.
            state = SessionState(event.get("session_id"), state_root, event.get("transcript_path"))
            if not state.data.get("snapshot"):
                # Fingerprint every server at the session's first tool call, before the agent can edit configs.
                cwd = project_dir(event)
                home = home or Path.home()
                state.check_identity({}, config_fingerprints(claude_code_configs(cwd, home), cwd, home))
                state.data["snapshot"] = True
            if touches_guard(tool_name, args):
                state.save()
                c = Classification(RClass.R4, "session", "Changes the files that hold rclass's session state, "
                                   "decision log or Claude Code settings, which would turn the guard off.",
                                   "claude-code", tool_name)
                if log_path is not None:
                    _log(log_path, event, "claude-code", tool_name, c, "ask", state.raise_to(c.rclass))
                    state.save()
                _emit(stdout, "ask", _reason(c, "claude-code", "ask"))
                return 0
            if tool_name in BUILTIN_NO_DATA:
                state.save()  # keeps the identity snapshot taken above; not a read
                return 0
            c = session_guard(Classification(RClass.R0, "builtin", "", "claude-code", tool_name),
                              tool_name in BUILTIN_EGRESS, args, state, policy)
            state.save()
            if c.source == "session":
                decision = policy.decide(c.rclass)
                if log_path is not None:
                    _log(log_path, event, "claude-code", tool_name, c, decision, state.raise_to(c.rclass))
                    state.save()
                _emit(stdout, decision or "ask", _reason(c, "claude-code", decision or "ask"))
            return 0
        cwd = project_dir(event)
        home = home or Path.home()
        registry = registry_factory()
        fps: dict[str, str] = {}
        records = server_records(claude_code_configs(cwd, home), registry, cwd, home, mapping, fps)
        state = SessionState(event.get("session_id"), state_root, event.get("transcript_path"))
        changed = state.check_identity(records, fps)
        state.data["snapshot"] = True
        parsed = parse_mcp_tool(tool_name, records)
        if parsed is None:
            raise ValueError("malformed MCP tool name")
        server_name, tool = parsed
        c = classify_call(server_name, tool, args, records, registry, changed)
        sids = [_ident(x)[0] for x in records.get(server_name) or [] if x]
        egress = any(e.raw.get("egress") for sid in sids for e in registry.entries(sid, tool))
        c = session_guard(c, egress, args, state, policy)
        decision = policy.decide(c.rclass)
        effective = state.raise_to(c.rclass)
        state.save()
        if log_path is not None:
            _log(log_path, event, server_name, tool, c, decision, effective)
        if decision is None:
            return 0
        _emit(stdout, decision, _reason(c, server_name, decision))
        return 0
    except Exception as exc:  # noqa: BLE001 - the one thing a gate must not do is fail open
        what = exc.__class__.__name__
        if exc.__class__.__name__ == "UsageError":
            what = f"rclass hook is misconfigured: {exc}"
        return ask_on_error(stdout, what)


def _log(path: Path, event: Mapping[str, Any], server: str, tool: str, c: Classification, decision: str | None,
         effective: RClass | None = None) -> None:
    """One hash-chained line per decision (SPEC 9 and 10; see rclass/audit.py): the class, the rule behind it,
    the session's effective class, an override's reason, who and where, and a digest of the arguments, never
    the arguments themselves."""
    from . import audit
    args = event.get("tool_input")
    entry = {"event": "pre", "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "session": event.get("session_id"), "tool_use_id": event.get("tool_use_id"), **audit.who(),
             "permission_mode": event.get("permission_mode"), "server": server, "tool": tool, "class": str(c.rclass), "source": c.source, "rule_index": c.rule_index,
             "effective": str(effective) if effective is not None else None, "decision": decision or "client",
             "args_sha256": audit.args_digest(args if isinstance(args, Mapping) else None)}
    if c.source == "override":
        entry["rationale"] = c.rationale  # carries the override's logged reason (SPEC 5.5)
    audit.append(path, entry)


def _log_outcome(path: Path, event: Mapping[str, Any]) -> None:
    """PostToolUse: the call ran. Joined to its decision by tool_use_id."""
    from . import audit
    audit.append(path, {"event": "post", "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "session": event.get("session_id"), "tool_use_id": event.get("tool_use_id"), **audit.who(),
                        "tool": str(event.get("tool_name", ""))})


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
    # Every tool, not only MCP ones: the session guard must see Read, Bash and the like to know the session
    # has read data. Non-MCP calls get no decision, except WebFetch after a read.
    # PostToolUse records that a call actually ran, so the log shows which prompts were approved.
    entry = [{"matcher": ".*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}]
    return json.dumps({"hooks": {"PreToolUse": entry, "PostToolUse": entry}}, indent=2) + "\n"


def main_hook(argv_policy: GatePolicy, mapping: Mapping[str, str], log: str | None, registry_factory) -> int:
    return run_hook(sys.stdin, sys.stdout, argv_policy, registry_factory, mapping, Path(log) if log else None)
