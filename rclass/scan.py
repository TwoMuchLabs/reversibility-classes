"""`rclass scan`: audit the MCP servers configured on this machine.

Reads a client configuration (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, or any file
with an `mcpServers` / `servers` map), starts each local server the way the client would, asks it
for its tools, and reports what an agent connected to it could do, by Reversibility Class.

Design rules, in order of importance:

1. Never print secrets. Server arguments and environment variables often carry tokens and
   connection strings, so neither is ever printed; a server is described by its config key and
   the package it was recognised by.
2. Fail closed. A tool the registry does not classify is counted as R4, exactly as SPEC section 6
   requires of a classifier. A server's own annotations are shown as a hint, never trusted.
3. Report drift honestly. Registry records are pinned to a commit; the server you run may be newer.
   Tools the server offers that the record lacks, and tools the record lists that the server no
   longer offers, are both reported.
"""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .classify import classify, from_mcp_annotations
from .model import RClass
from .registry import Registry

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "rclass-scan", "version": "0.3.0.dev0"}
MIN_TOOL_OVERLAP = 0.6  # Jaccard similarity needed to recognise a server by its tool names alone


# --------------------------------------------------------------------------- config discovery

@dataclass
class ServerConfig:
    key: str                      # the name the user gave it in their config
    source: str                   # the config file it came from
    command: str | None = None    # local (stdio) servers
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None        # remote (http / sse) servers
    disabled: bool = False

    @property
    def remote(self) -> bool:
        return self.url is not None and not self.command


def known_config_paths(home: Path | None = None, cwd: Path | None = None) -> list[Path]:
    """Where common MCP clients keep their configuration, for this OS. Only existing files return."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    system = platform.system()
    if system == "Darwin":
        desktop = home / "Library/Application Support/Claude/claude_desktop_config.json"
    elif system == "Windows":
        desktop = Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Claude/claude_desktop_config.json"
    else:
        desktop = home / ".config/Claude/claude_desktop_config.json"
    candidates = [
        desktop,                                   # Claude Desktop
        home / ".claude.json",                     # Claude Code (user and per-project servers)
        cwd / ".mcp.json",                         # Claude Code (project)
        home / ".cursor/mcp.json",                 # Cursor (global)
        cwd / ".cursor/mcp.json",                  # Cursor (project)
        cwd / ".vscode/mcp.json",                  # VS Code (workspace)
        home / ".codeium/windsurf/mcp_config.json",  # Windsurf
    ]
    seen, found = set(), []
    for c in candidates:
        r = c.resolve() if c.exists() else c
        if c.is_file() and r not in seen:
            seen.add(r)
            found.append(c)
    return found


def _server_maps(doc: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Yield (prefix, {name: server}) maps from any supported config shape."""
    if not isinstance(doc, Mapping):
        return
    for key in ("mcpServers", "servers"):  # most clients / VS Code
        if isinstance(doc.get(key), Mapping):
            yield "", doc[key]
    projects = doc.get("projects")  # Claude Code's ~/.claude.json keeps per-project servers here
    if isinstance(projects, Mapping):
        for proj, cfg in projects.items():
            if isinstance(cfg, Mapping) and isinstance(cfg.get("mcpServers"), Mapping) and cfg["mcpServers"]:
                yield f"{Path(str(proj)).name}/", cfg["mcpServers"]


def load_config(path: Path) -> list[ServerConfig]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    out = []
    for prefix, servers in _server_maps(doc):
        for name, s in servers.items():
            if not isinstance(s, Mapping):
                continue
            args = s.get("args") or []
            env = s.get("env") or {}
            out.append(ServerConfig(
                key=f"{prefix}{name}",
                source=str(path),
                command=s.get("command"),
                args=[str(a) for a in args] if isinstance(args, list) else [],
                env={str(k): str(v) for k, v in env.items()} if isinstance(env, Mapping) else {},
                cwd=s.get("cwd"),
                url=s.get("url") or s.get("serverUrl"),
                disabled=bool(s.get("disabled", False)),
            ))
    return out


# --------------------------------------------------------------------------- identification

def _strip_version(token: str) -> str:
    t = token.split("==", 1)[0]
    if t.startswith("@"):
        scope_and_name, _, _ = t[1:].partition("@")
        t = "@" + scope_and_name
    else:
        t = t.partition("@")[0]
    head, sep, last = t.rpartition("/")
    if ":" in last:                      # image:tag, but leave host:port/image alone
        last = last.split(":", 1)[0]
    return f"{head}{sep}{last}"


def _package_index(registry: Registry) -> dict[str, str]:
    """Map every install identifier (and its last path segment, for installed binaries) to a server id."""
    index: dict[str, str] = {}
    for rec in registry.records:
        sid = rec["server"]["id"]
        for pkg in rec["server"].get("packages", []):
            index[pkg.lower()] = sid
            index.setdefault(pkg.rsplit("/", 1)[-1].lower(), sid)
    return index


def identify_by_package(cfg: ServerConfig, registry: Registry) -> str | None:
    index = _package_index(registry)
    tokens = ([Path(cfg.command).name] if cfg.command else []) + cfg.args
    for tok in tokens:
        if tok.startswith("-"):
            continue
        key = _strip_version(tok).lower()
        if key in index:
            return index[key]
    return None


def identify_by_endpoint(cfg: ServerConfig, registry: Registry) -> str | None:
    if not cfg.url:
        return None
    url = cfg.url.rstrip("/").lower()
    for rec in registry.records:
        for ep in rec["server"].get("endpoints", []):
            if url == ep.rstrip("/").lower() or url.startswith(ep.rstrip("/").lower() + "/"):
                return rec["server"]["id"]
    return None


def _tool_names(registry: Registry, sid: str) -> set[str]:
    return {t["name"] for r in registry.records if r["server"]["id"] == sid for t in r["tools"]}


def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def identify_by_tools(tools: set[str], registry: Registry) -> tuple[str | None, float]:
    best, score = None, 0.0
    for sid in registry.servers():
        s = overlap(tools, _tool_names(registry, sid))
        if s > score:
            best, score = sid, s
    return (best, score) if score >= MIN_TOOL_OVERLAP else (None, score)


# --------------------------------------------------------------------------- minimal MCP client

class McpError(RuntimeError):
    pass


def _reader(stream, q: "queue.Queue[bytes | None]") -> None:
    for line in iter(stream.readline, b""):
        q.put(line)
    q.put(None)


def _expand(value: str, env: Mapping[str, str]) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), value)


def list_tools_stdio(cfg: ServerConfig, timeout: float = 60.0) -> tuple[list[dict], dict]:
    """Start a stdio server, complete the MCP handshake, page through tools/list, and stop it.

    Returns (tools, serverInfo). Raises McpError with a message that never includes arguments
    or environment values.
    """
    if not cfg.command:
        raise McpError("no command configured")
    env = {**os.environ, **cfg.env}
    unresolved = [k for k, v in cfg.env.items() if "${input:" in v]
    if unresolved:
        raise McpError(f"needs values the client prompts for ({', '.join(unresolved)}); not started")
    exe = shutil.which(cfg.command, path=env.get("PATH")) or cfg.command
    argv = [exe] + [_expand(a, env) for a in cfg.args]
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cfg.cwd or None,
        )
    except OSError as exc:
        raise McpError(f"could not start `{Path(cfg.command).name}` ({exc.strerror or exc})") from None

    q: "queue.Queue[bytes | None]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, q), daemon=True).start()
    err_tail: list[bytes] = []
    threading.Thread(target=lambda: [err_tail.append(l) for l in iter(proc.stderr.readline, b"")], daemon=True).start()
    deadline = time.monotonic() + timeout
    next_id = 0

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    def request(method: str, params: dict) -> dict:
        nonlocal next_id
        next_id += 1
        rid = next_id
        send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"timed out after {timeout:.0f}s waiting for {method}")
            try:
                line = q.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                code = proc.poll()
                hint = _stderr_hint(err_tail)
                raise McpError(f"server exited (code {code}) before answering {method}{hint}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # servers sometimes log to stdout; skip non-JSON lines
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:  # a request from the server: decline politely
                send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported by rclass scan"}})
                continue
            if msg.get("id") != rid:
                continue  # a notification or a stale response
            if "error" in msg:
                e = msg["error"] or {}
                raise McpError(f"{method} failed: {e.get('message', 'unknown error')}")
            return msg.get("result") or {}

    try:
        init = request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[dict] = []
        cursor = None
        for _ in range(1000):  # pagination guard
            res = request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in res.get("tools", []) if isinstance(t, dict) and t.get("name"))
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools, init.get("serverInfo") or {}
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def _stderr_hint(lines: list[bytes]) -> str:
    """The last stderr line, only if it does not look like it carries a secret."""
    for raw in reversed(lines[-20:]):
        text = raw.decode(errors="replace").strip()
        if not text:
            continue
        if re.search(r"(token|secret|password|passwd|key|bearer|://[^/\s]*@)", text, re.IGNORECASE):
            return ""
        return f": {text[:160]}"
    return ""


# --------------------------------------------------------------------------- report

@dataclass
class ToolReport:
    name: str
    rclass: RClass
    source: str                  # registry / rule / fail-closed / override
    rationale: str
    has_rules: bool = False      # some arguments put it in a lower class
    hint: RClass | None = None   # what the server's own annotations would suggest (untrusted)


@dataclass
class ServerReport:
    key: str
    config: str
    status: str                  # scanned / remote / skipped / error
    record: str | None = None
    record_name: str | None = None
    matched_by: str | None = None  # package / endpoint / tools
    match_score: float | None = None
    server_info: dict = field(default_factory=dict)
    tools: list[ToolReport] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    not_offered: list[str] = field(default_factory=list)  # in the record, not offered by this server
    message: str | None = None

    def counts(self) -> dict[str, int]:
        c = {str(k): 0 for k in RClass}
        for t in self.tools:
            c[str(t.rclass)] += 1
        return c


def _report_tools(sid: str | None, tools: list[dict], registry: Registry) -> tuple[list[ToolReport], list[str]]:
    out, unclassified = [], []
    for t in tools:
        name = t["name"]
        ann = t.get("annotations") if isinstance(t.get("annotations"), Mapping) else None
        entries = registry.entries(sid, name) if sid else []
        c = classify(sid or "", name, None, registry=registry)
        if c.source == "fail-closed":
            unclassified.append(name)
        out.append(ToolReport(
            name=name, rclass=c.rclass, source=c.source, rationale=c.rationale,
            has_rules=any(e.raw.get("when") for e in entries),
            hint=from_mcp_annotations(ann) if (c.source == "fail-closed" and ann is not None) else None,
        ))
    out.sort(key=lambda r: (-int(r.rclass), r.name))
    return out, unclassified


def scan_server(cfg: ServerConfig, registry: Registry, timeout: float = 60.0, start: bool = True) -> ServerReport:
    rep = ServerReport(key=cfg.key, config=cfg.source, status="scanned")
    names = {r["server"]["id"]: r["server"]["name"] for r in registry.records}

    if cfg.disabled:
        rep.status, rep.message = "skipped", "disabled in the client config"
        return rep

    if cfg.remote:
        sid = identify_by_endpoint(cfg, registry)
        rep.status = "remote"
        if sid:
            rep.record, rep.record_name, rep.matched_by = sid, names[sid], "endpoint"
            listed = [{"name": n} for n in sorted(_tool_names(registry, sid))]
            rep.tools, _ = _report_tools(sid, listed, registry)
            rep.message = "hosted server: not started; classes shown are the registry's for this endpoint"
        else:
            rep.message = "hosted server not in the registry: not started; every tool it offers is R4 until classified"
        return rep

    sid = identify_by_package(cfg, registry)
    if sid:
        rep.matched_by = "package"
    if not start:
        rep.status = "listed"
        if sid:
            rep.record, rep.record_name = sid, names[sid]
        rep.message = "not started (--list)"
        return rep

    try:
        tools, info = list_tools_stdio(cfg, timeout=timeout)
    except McpError as exc:
        rep.status, rep.message = "error", str(exc)
        if sid:
            rep.record, rep.record_name = sid, names[sid]
        return rep

    rep.server_info = {k: info[k] for k in ("name", "version") if k in info}
    offered = {t["name"] for t in tools}
    if sid:
        rep.match_score = overlap(offered, _tool_names(registry, sid))
    else:
        sid, score = identify_by_tools(offered, registry)
        rep.match_score = score if sid else None
        if sid:
            rep.matched_by = "tools"
    if sid:
        rep.record, rep.record_name = sid, names[sid]
        rep.not_offered = sorted(_tool_names(registry, sid) - offered)
    rep.tools, rep.unclassified = _report_tools(sid, tools, registry)
    return rep


@dataclass
class ScanResult:
    configs: list[str]
    servers: list[ServerReport]

    def totals(self) -> dict[str, Any]:
        tools = [t for s in self.servers for t in s.tools]
        by = {str(k): sum(1 for t in tools if t.rclass is k) for k in RClass}
        return {
            "servers": len(self.servers),
            "servers_scanned": sum(1 for s in self.servers if s.status == "scanned"),
            "tools": len(tools),
            "by_class": by,
            "unclassified": sum(len(s.unclassified) for s in self.servers),
            "autonomous_under_default_policy": by["R0"] + by["R1"],
        }

    def worst(self) -> RClass | None:
        tools = [t.rclass for s in self.servers for t in s.tools]
        return max(tools) if tools else None

    def to_dict(self) -> dict:
        return {
            "configs": self.configs,
            "totals": self.totals(),
            "servers": [
                {
                    "key": s.key, "config": s.config, "status": s.status,
                    "record": s.record, "record_name": s.record_name,
                    "matched_by": s.matched_by,
                    "match_score": round(s.match_score, 3) if s.match_score is not None else None,
                    "server_info": s.server_info, "message": s.message,
                    "counts": s.counts(),
                    "unclassified": s.unclassified, "not_offered": s.not_offered,
                    "tools": [
                        {"name": t.name, "class": str(t.rclass), "source": t.source,
                         "rationale": t.rationale, "lower_for_some_arguments": t.has_rules,
                         **({"annotation_hint": str(t.hint)} if t.hint is not None else {})}
                        for t in s.tools
                    ],
                }
                for s in self.servers
            ],
        }


def scan(configs: list[Path], registry: Registry, timeout: float = 60.0, start: bool = True,
         only: list[str] | None = None) -> ScanResult:
    servers: list[ServerConfig] = []
    for p in configs:
        servers.extend(load_config(p))
    if only:
        servers = [s for s in servers if s.key in only or s.key.split("/")[-1] in only]
    return ScanResult([str(p) for p in configs], [scan_server(s, registry, timeout, start) for s in servers])


# --------------------------------------------------------------------------- text output

def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _bar(counts: Mapping[str, int]) -> str:
    return "  ".join(f"{k} {v}" for k, v in counts.items())


def _names(items: list[str], limit: int = 6) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f", +{len(items) - limit} more" if len(items) > limit else "")


def render_text(result: ScanResult, details: bool = False) -> str:
    out: list[str] = []
    w = max([len(s.key) for s in result.servers] + [8])
    for s in result.servers:
        head = f"{s.key:<{w}}  "
        pad = " " * (w + 2)
        if s.status in ("skipped", "listed"):
            what = f"{s.record_name}" if s.record_name else "not in the registry"
            out.append(f"{head}{what} · {s.message}")
            out.append("")
            continue
        if s.status == "error":
            what = f"{s.record_name} · " if s.record_name else ""
            out.append(f"{head}{what}could not list tools: {s.message}")
            out.append("")
            continue
        if s.status == "remote" and not s.record:
            out.append(f"{head}hosted server, not in the registry · not started; treat every tool it offers as R4")
            out.append("")
            continue
        if s.record:
            tail = "hosted, not started: classes are the registry's for this endpoint" if s.status == "remote" else (
                "recognised by its tool names" if s.matched_by == "tools" else "")
            out.append(f"{head}{s.record_name} · {_n(len(s.tools), 'tool')}" + (f" · {tail}" if tail else ""))
        else:
            out.append(f"{head}not in the registry · {_n(len(s.tools), 'tool')}, all counted as R4 (fail closed)")
        if s.tools:
            out.append(f"{pad}{_bar(s.counts())}")
        irreversible = [t.name for t in s.tools if t.rclass is RClass.R4 and t.source != "fail-closed"]
        if irreversible:
            out.append(f"{pad}irreversible: {_names(irreversible)}")
        if s.unclassified and s.record:
            out.append(f"{pad}! {len(s.unclassified)} not in the registry record, counted as R4: {_names(s.unclassified)}")
        if s.not_offered:
            out.append(f"{pad}! {len(s.not_offered)} in the record but not offered by this server: {_names(s.not_offered)}")
            out.append(f"{pad}  your version may differ from the one the record was checked against ({s.record})")
        hints = [t for t in s.tools if t.hint is not None]
        if hints and not s.record:
            ro = sum(1 for t in hints if t.hint is RClass.R0)
            out.append(f"{pad}its own annotations call {ro} of them read-only; annotations are not trusted (SPEC 7)")
        if s.matched_by == "package" and s.match_score is not None and s.match_score < MIN_TOOL_OVERLAP:
            out.append(f"{pad}! tool names overlap the record only {s.match_score:.0%}; the record may describe a different version")
        if details:
            for t in s.tools:
                note = "  (lower for some arguments)" if t.has_rules else ""
                out.append(f"{pad}  {t.rclass}  {t.name}{note}")
        out.append("")

    scanned = [t for s in result.servers if s.status == "scanned" for t in s.tools]
    hosted = [t for s in result.servers if s.status == "remote" for t in s.tools]
    tools = scanned + hosted
    if tools:
        r4 = sum(1 for t in tools if t.rclass is RClass.R4)
        unc = sum(1 for t in tools if t.source == "fail-closed")
        alone = sum(1 for t in tools if t.rclass <= RClass.R1)
        n_servers = sum(1 for s in result.servers if s.tools)
        line = f"{_n(len(tools), 'tool')} across {_n(n_servers, 'server')}"
        if hosted:
            line += f" ({len(hosted)} from hosted servers, as the registry lists them)"
        line += f". {r4} irreversible (R4)"
        line += (f", {unc} of them only because nobody has classified {'it' if unc == 1 else 'them'} yet." if unc else ".")
        out.append(line)
        rest = len(tools) - alone
        out.append(f"Under the default policy an agent may act alone on {alone} (R0–R1)"
                   + (f"; {'the other one needs' if rest == 1 else f'the other {rest} need'} a human." if rest else "."))
    unreachable = [s.key for s in result.servers if s.status == "error" or (s.status == "remote" and not s.record)]
    if unreachable:
        out.append(f"Not counted: {', '.join(unreachable)}.")
    if any(t.source == "fail-closed" for t in tools):
        out.append("Help classify the missing tools: rclass scan --write-drafts DIR, then see "
                   "https://github.com/TwoMuchLabs/reversibility-classes/blob/main/CONTRIBUTING.md")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- draft records

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "server"


def draft_record(rep: ServerReport, tools: list[ToolReport]) -> str:
    """A starting point for a contribution: every tool listed, every tool at R4 until someone checks it."""
    import yaml

    sid = rep.record or _slug(rep.server_info.get("name") or rep.key.split("/")[-1])
    doc = {
        "spec_version": "0.2",
        "server": {
            "id": sid,
            "name": rep.record_name or rep.server_info.get("name") or rep.key,
            "source": "https://example.invalid/REPLACE-with-the-server-repository",
            "evidence": ["https://example.invalid/REPLACE-with-the-file-where-tools-are-registered"],
            **({"version": rep.server_info["version"]} if rep.server_info.get("version") else {}),
        },
        "tools": [
            {
                "name": t.name,
                "class": "R4",
                "rationale": "UNREVIEWED: starts at R4 (fail closed). Replace with the fact that decides its class."
                + (f" The server's own annotations suggest {t.hint}, which is not evidence." if t.hint is not None else ""),
                "review": "draft",
            }
            for t in tools
        ],
    }
    head = (
        "# DRAFT generated by `rclass scan`. Not ready to submit.\n"
        "# 1. Fill in server.source, server.evidence and server.packages.\n"
        "# 2. Check every tool against the server's code and give it a real class and rationale.\n"
        "# 3. Name the undo (`compensation`) for anything you put at R1 or R2.\n"
        "# See CONTRIBUTING.md.\n"
    )
    return head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
