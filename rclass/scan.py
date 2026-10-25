"""`rclass scan`: audit the MCP servers configured on this machine.

Reads a client configuration (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, or any file
with an `mcpServers` / `servers` map), optionally starts each local server, asks it for its tools,
and reports what an agent connected to it could do, by Reversibility Class.

Rules this module keeps, in order of importance:

1. Report the worst case. A tool is reported at the highest class it can reach, never at the class
   it happens to get with no arguments. "Lower for some arguments" is a note, not a discount.
2. Fail closed. Unclassified tools, unknown servers and servers that could not be scanned count
   against a `--fail-on` gate; a server's own annotations are a hint, never trusted. A server is
   only matched to a record by how it is installed (or by an explicit --assume), never by tool
   names alone, because tool names are chosen by the server.
3. Never print secrets. Arguments, environment values and text the server writes (stderr, error
   messages) are not printed by default. With --show-server-errors, server text is shown with the
   config's own argument and environment values and common token shapes redacted.
4. Survive hostile servers. Every reply is shape-checked; lines, stderr, pages and tools are capped;
   each server runs in its own process group under a hard deadline and is killed with its children.
5. Do not run what the user has not approved. Project-scoped configs (.mcp.json, .vscode/,
   .cursor/ in a repository) are not started without consent, servers get the minimal environment
   the official MCP SDKs pass by default, and Claude Code's per-project approvals are honoured.
"""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .classify import from_mcp_annotations
from .model import RClass
from .registry import Registry, ToolEntry

REPORT_VERSION = 1
PROTOCOL_VERSION = "2025-06-18"
MIN_TOOL_OVERLAP = 0.6         # name overlap at which a record is *suggested* for an unrecognised server
MAX_LINE = 8 * 1024 * 1024     # bytes per JSON-RPC line
MAX_TOOLS = 5000
MAX_PAGES = 200
STDERR_KEEP = 30               # last stderr lines kept, each capped
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-/]{1,128}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# What the official MCP SDKs (TypeScript and Python) pass to a server by default, so a scan sees
# the server the way a client does; plus proxy and certificate settings so `npx`/`uvx` downloads
# work behind a corporate proxy. Everything else in the shell (tokens, cloud keys) stays out.
SDK_DEFAULT_ENV = (
    ["APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE",
     "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "USERNAME", "USERPROFILE", "PROGRAMFILES"]
    if sys.platform == "win32" else ["HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"]
)
NETWORK_ENV = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE"]

PROJECT_CONFIG_NAMES = {".mcp.json"}
PROJECT_CONFIG_DIRS = {".vscode", ".cursor"}


def rclass_version() -> str:
    try:
        from importlib.metadata import version
        return version("rclass")
    except Exception:  # noqa: BLE001 - running from a source checkout
        from . import __version__
        return __version__


# --------------------------------------------------------------------------- text safety

def clean(text: Any, limit: int = 120) -> str:
    """Make server-supplied text safe to print: no control characters, bounded length."""
    s = CONTROL_RE.sub("?", str(text))
    return s if len(s) <= limit else s[: limit - 1] + "…"


TOKEN_SHAPES = re.compile(
    r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|sk-|sk_live_|sk_test_|rk_live_|xox[abprs]-|npm_|AIza|AKIA|ASIA|eyJ)"
    r"[A-Za-z0-9_\-./+=]{6,}"
    r"|https://hooks\.slack\.com/\S+"
    r"|[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:[^@\s]+@"          # user:pass@ in a URL
    r"|\b[\w.\-]+:[^\s/@]{4,}"                                 # user:pass, key:value pairs
)
# Text after one of these words, to the end of the line, is redacted whole.
SECRET_WORDS = re.compile(r"(?i)\b(password|passwd|pwd|passphrase|secret|token|api[ _-]?key|apikey|authorization|"
                          r"credential|bearer|private[ _-]key|access[ _-]key|key)\b[^\n]*")


def redact(text: str, secrets: Iterable[str]) -> str:
    """Best-effort redaction of server text: the config's own values, anything after a secret-sounding
    word, common token shapes, and user:password pairs. Over-redaction is the intended failure."""
    out = re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", " ", text)
    for s in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(s, "[redacted]")
    out = SECRET_WORDS.sub(lambda m: m.group(1) + " [redacted]", out)
    return TOKEN_SHAPES.sub("[redacted]", out)


def _last_lines(text: str, n: int = 3) -> str:
    """The last few meaningful lines (the final error, not the middle of a stack trace)."""
    noise = re.compile(r"^(Node\.js v\d|warning: |npm (WARN|notice)|\s*at |\s*\^+\s*$)", re.IGNORECASE)
    lines = [l.strip() for l in text.splitlines() if l.strip() and not noise.match(l.strip())]
    return " | ".join(lines[-n:])


# --------------------------------------------------------------------------- config

@dataclass
class ServerConfig:
    key: str
    source: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    disabled: bool = False
    disabled_reason: str | None = None
    project: bool = False         # defined by a repository's own config: needs consent to start
    approved: bool = False        # the user already approved it in their client
    project_root: str | None = None
    config_error: str | None = None
    needs: list[str] = field(default_factory=list)

    @property
    def remote(self) -> bool:
        return self.url is not None and not self.command

    def fingerprint(self) -> tuple:
        return (self.command, tuple(self.args), tuple(sorted(self.env.items())), self.url, self.cwd)

    def secrets(self) -> list[str]:
        """Values from the config that must never be printed."""
        vals = list(self.env.values()) + list(self.args) + [self.cwd or "", self.url or ""]
        return [v for v in vals if v]


def _project_root_of(path: Path, home: Path | None = None) -> Path | None:
    """The repository a config belongs to, or None for a user's own (global) config."""
    if path.name in PROJECT_CONFIG_NAMES:
        root = path.parent
    elif path.parent.name in PROJECT_CONFIG_DIRS:
        root = path.parent.parent
    else:
        return None
    home = (home or Path.home()).resolve()
    if root.resolve() == home:
        return None  # ~/.cursor/mcp.json, ~/.mcp.json: the user's own global config
    return root


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
        desktop,                                     # Claude Desktop
        home / ".claude.json",                       # Claude Code (user scope, and this project's local scope)
        cwd / ".mcp.json",                           # Claude Code (project scope)
        home / ".cursor/mcp.json",                   # Cursor (global)
        cwd / ".cursor/mcp.json",                    # Cursor (project)
        cwd / ".vscode/mcp.json",                    # VS Code (workspace)
        home / ".codeium/windsurf/mcp_config.json",  # Windsurf
    ]
    seen, found = set(), []
    for c in candidates:
        if c.is_file():
            r = c.resolve()
            if r not in seen:
                seen.add(r)
                found.append(c)
    return found


_VAR = re.compile(r"\$\{([^}]*)\}")


def _expand(value: str, root: Path | None, needs: list[str]) -> str:
    """Expand the variable syntaxes MCP clients support. Anything the client would prompt for, or a
    variable that is not set and has no default, is recorded in `needs` and left as written."""
    def sub(m: re.Match) -> str:
        expr = m.group(1)
        if expr.startswith("input:"):
            needs.append(expr)
            return m.group(0)
        if expr in ("workspaceFolder", "workspaceRoot"):
            if root is None:
                needs.append(expr)
                return m.group(0)
            return str(root)
        if expr == "userHome":
            return str(Path.home())
        if expr.startswith("env:"):                 # VS Code
            return os.environ.get(expr[4:], "")
        name, sep, default = expr.partition(":-")   # Claude Code: ${VAR} and ${VAR:-default}
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            if name in os.environ:
                return os.environ[name]
            if sep:
                return default
            needs.append(name)
        return m.group(0)
    return _VAR.sub(sub, value)


def _claude_code_approvals(home: Path, project_root: Path) -> Mapping[str, Any]:
    try:
        doc = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(doc, Mapping) or not isinstance(doc.get("projects"), Mapping):
        return {}
    proj = doc["projects"].get(str(project_root.resolve()))
    return proj if isinstance(proj, Mapping) else {}


def load_config(path: Path, cwd: Path | None = None, home: Path | None = None,
                all_projects: bool = False) -> list[ServerConfig]:
    """Parse one client config. Raises ValueError only when the file itself cannot be read."""
    cwd = (cwd or Path.cwd()).resolve()
    home = home or Path.home()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError(f"could not read {path}: {exc.__class__.__name__}") from None
    if not isinstance(doc, Mapping):
        raise ValueError(f"could not read {path}: not a JSON object")

    root = _project_root_of(path, home)
    approvals = _claude_code_approvals(home, root) if (root and path.name == ".mcp.json") else {}
    disabled = set(approvals.get("disabledMcpjsonServers") or [])
    enabled = set(approvals.get("enabledMcpjsonServers") or [])
    enable_all = bool(approvals.get("enableAllProjectMcpServers"))

    maps: list[tuple[str, Mapping, bool, Path | None]] = []
    for key in ("mcpServers", "servers"):
        if isinstance(doc.get(key), Mapping):
            maps.append(("", doc[key], root is not None, root))
    projects = doc.get("projects")
    if path.name == ".claude.json" and isinstance(projects, Mapping):
        for proj, cfg in projects.items():
            if not (isinstance(cfg, Mapping) and isinstance(cfg.get("mcpServers"), Mapping) and cfg["mcpServers"]):
                continue
            here = Path(str(proj))
            if all_projects or here.resolve() == cwd:
                label = f"{proj}/" if all_projects else f"{here.name}/"
                maps.append((label, cfg["mcpServers"], False, here))  # local scope: added by the user

    out = []
    for prefix, servers, project, proot in maps:
        for name, s in servers.items():
            cfg = ServerConfig(key=f"{prefix}{name}", source=str(path), project=project,
                               project_root=str(proot) if proot else None)
            out.append(cfg)
            if not isinstance(s, Mapping):
                cfg.config_error = "entry is not an object"
                continue
            command, args, env = s.get("command"), s.get("args", []), s.get("env", {})
            url, wd = s.get("url") or s.get("serverUrl"), s.get("cwd")
            if command is not None and not isinstance(command, str):
                cfg.config_error = "`command` must be a string"
            elif not isinstance(args, list) or not all(isinstance(a, (str, int, float)) for a in args):
                cfg.config_error = "`args` must be a list of strings"
            elif not isinstance(env, Mapping) or not all(isinstance(v, (str, int, float)) for v in env.values()):
                cfg.config_error = "`env` must map names to strings"
            elif url is not None and not isinstance(url, str):
                cfg.config_error = "`url` must be a string"
            elif wd is not None and not isinstance(wd, str):
                cfg.config_error = "`cwd` must be a string"
            elif command is None and url is None:
                cfg.config_error = "neither `command` nor `url` is set"
            if cfg.config_error:
                continue
            needs: list[str] = []
            cfg.command = _expand(command, proot, needs) if command else None
            cfg.args = [_expand(str(a), proot, needs) for a in args]
            cfg.env = {str(k): _expand(str(v), proot, needs) for k, v in env.items()}
            cfg.url = _expand(url, proot, needs) if url else None
            wd_x = _expand(wd, proot, needs) if wd else None
            if wd_x and proot and not Path(wd_x).is_absolute():
                wd_x = str(proot / wd_x)
            cfg.cwd = wd_x or (str(proot) if proot else None)
            cfg.needs = needs
            if s.get("disabled") is True or s.get("enabled") is False:
                cfg.disabled, cfg.disabled_reason = True, "disabled in the client config"
            if project and path.name == ".mcp.json":
                if name in disabled:
                    cfg.disabled, cfg.disabled_reason = True, "disabled in Claude Code for this project"
                elif name in enabled or enable_all:
                    cfg.approved = True
    return out


# --------------------------------------------------------------------------- identification

def _strip_version(token: str) -> str:
    t = re.split(r"==|>=|<=|~=|!=", token, maxsplit=1)[0]
    if t.startswith("@"):
        t = "@" + t[1:].partition("@")[0]
    else:
        t = t.partition("@")[0]
    head, sep, last = t.rpartition("/")
    if ":" in last:
        last = last.split(":", 1)[0]
    t = f"{head}{sep}{last}"
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t


_NPX_VALUE_FLAGS = {"-p", "--package", "-c", "--call", "--registry", "--cache", "--userconfig"}
_UVX_VALUE_FLAGS = {"--with", "--with-editable", "--with-requirements", "--python", "-p", "--index", "--index-url",
                    "--extra-index-url", "--default-index", "--find-links", "-f", "--constraints", "--overrides",
                    "-c", "--directory", "--project", "--cache-dir", "--refresh-package", "--reinstall-package",
                    "--upgrade-package", "--env-file", "--config-file", "--python-preference", "-w",
                    "--from", "--spec"}
_DOCKER_VALUE_FLAGS = {"-e", "--env", "--env-file", "-v", "--volume", "--mount", "-p", "--publish", "--name", "--network",
                       "--net", "-w", "--workdir", "-u", "--user", "--entrypoint", "--platform", "-l", "--label",
                       "--add-host", "--cpus", "-m", "--memory", "--pull", "--restart", "--log-driver", "--log-opt",
                       "--dns", "--hostname", "-h", "--cap-add", "--cap-drop", "--device", "--gpus", "--ipc", "--pid",
                       "--security-opt", "--tmpfs", "--ulimit", "--runtime", "--cidfile", "--stop-signal",
                       "--stop-timeout", "--group-add", "--label-file", "--link", "--expose", "--ip", "--shm-size",
                       "--userns", "--uts", "--volumes-from", "-a", "--attach"}


def _first_positional(args: list[str], value_flags: set[str]) -> tuple[str | None, dict[str, str]]:
    seen: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return (args[i + 1] if i + 1 < len(args) else None), seen
        if a.startswith("-"):
            flag, eq, val = a.partition("=")
            if eq:
                seen[flag] = val
            elif flag in value_flags and i + 1 < len(args):
                seen[flag] = args[i + 1]
                i += 1
            i += 1
            continue
        return a, seen
    return None, seen


def _custom_source(spec: str) -> bool:
    s = spec.lower()
    if s.startswith(("git+", "http://", "https://", "file:", ".", "/", "~")) or re.match(r"^[a-z]:\\", s):
        return True
    return "/" in s and not s.startswith("@")  # an npm "user/repo" GitHub shorthand


# Environment variables that change which code a launcher runs, or where it downloads it from.
_CODE_CHANGING_ENV = re.compile(
    r"^(PATH|NODE_OPTIONS|NODE_PATH|NODE_EXTRA_CA_CERTS_FILE|PYTHONPATH|PYTHONSTARTUP|PYTHONHOME|PYTHONUSERBASE|"
    r"LD_PRELOAD|LD_LIBRARY_PATH|LD_AUDIT|DYLD_.*|BASH_ENV|ENV|PERL5OPT|RUBYOPT|JAVA_TOOL_OPTIONS|"
    r"NPM_CONFIG_.*|YARN_.*|PNPM_.*|BUN_.*|UV_.*|PIP_.*|PIPX_.*|DOCKER_HOST|DOCKER_CONFIG|COREPACK_.*)$",
    re.IGNORECASE,
)
_REGISTRY_FLAGS = {"--registry", "--index", "--index-url", "--extra-index-url", "--default-index", "--find-links", "-f",
                   "--with", "--with-editable", "--with-requirements", "--userconfig"}


def run_target(command: str | None, args: list[str], env: Mapping[str, str] | None = None,
               project_root: str | None = None) -> tuple[str | None, str | None]:
    """The package or image a launcher runs *as itself*: (target, note).

    Returns (None, reason) whenever what actually executes might not be that package's own entry
    point: a command run from inside a package (`npx -p`, `uvx --from X other-cmd`), a docker
    entrypoint or command override, a custom registry or source, an environment that changes what
    code runs, or a launcher that is not the system's own.
    """
    if not command:
        return None, None
    path = Path(command)
    exe = path.name.lower()
    for ext in (".exe", ".cmd", ".bat"):
        exe = exe.removesuffix(ext)
    bad_env = sorted(k for k in (env or {}) if _CODE_CHANGING_ENV.match(k))
    if bad_env:
        return None, f"its config sets {', '.join(bad_env[:3])}, which can change what code runs"
    if command != path.name:  # a path rather than a launcher found on PATH
        if not path.is_absolute():
            return None, "runs a launcher by relative path, which may not be the system's own"
        if project_root and str(path.resolve()).startswith(str(Path(project_root).resolve()) + os.sep):
            return None, "runs a launcher from inside the project, which may not be the system's own"
    first = args[:1]
    if exe in ("npx", "bunx", "pnpx") or (exe in ("pnpm", "yarn") and first == ["dlx"]) or \
            (exe == "bun" and first == ["x"]) or (exe == "npm" and first in (["exec"], ["x"])):
        rest = args if exe in ("npx", "bunx", "pnpx") else args[1:]
        target, flags = _first_positional(rest, _NPX_VALUE_FLAGS)
        if "-p" in flags or "--package" in flags or "-c" in flags or "--call" in flags:
            return None, "runs a command from inside a package (-p/--package/-c), not the package itself"
        if _REGISTRY_FLAGS & set(flags):
            return None, "installs from a custom registry"
        if target and _custom_source(target):
            return None, "installed from a custom source (a path, URL or git repository), so it may be a fork"
        return target, None
    if exe == "uvx" or (exe == "uv" and args[:2] == ["tool", "run"]) or (exe == "pipx" and first == ["run"]):
        rest = args[2:] if exe == "uv" else (args[1:] if exe == "pipx" else args)
        target, flags = _first_positional(rest, _UVX_VALUE_FLAGS)
        if _REGISTRY_FLAGS & set(flags):
            return None, "installs from a custom index or adds extra packages"
        src = flags.get("--from") or flags.get("--spec")
        if src:
            if _custom_source(src):
                return None, "installed from a custom source (--from/--spec), so it may be a fork"
            if target is None or _strip_version(target).lower() != _strip_version(src).lower():
                return None, "runs a different command from the package it installs (--from/--spec)"
            return src, None
        return target, None
    if exe in ("docker", "podman") and first == ["run"]:
        rest = args[1:]
        target, flags = _first_positional(rest, _DOCKER_VALUE_FLAGS)
        if "--entrypoint" in flags:
            return None, "overrides the image's entrypoint"
        if target is not None and rest.index(target) < len(rest) - 1:
            return None, "passes a command or arguments after the image, which can replace what it runs"
        return target, None
    return None, None


def _package_index(registry: Registry) -> dict[str, str]:
    return {_strip_version(p).lower(): rec["server"]["id"]
            for rec in registry.records for p in rec["server"].get("packages", [])}


def identify_by_package(cfg: ServerConfig, registry: Registry) -> tuple[str | None, str | None]:
    target, note = run_target(cfg.command, cfg.args, cfg.env, cfg.project_root)
    if not target:
        return None, note
    return _package_index(registry).get(_strip_version(target).lower()), note


def identify_by_endpoint(cfg: ServerConfig, registry: Registry) -> tuple[str | None, bool]:
    """(record id, exact). Query strings are ignored; a longer path is a sub-endpoint that may
    expose fewer tools, so it matches but is reported as not exact."""
    if not cfg.url:
        return None, False
    url = cfg.url.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
    for rec in registry.records:
        for ep in rec["server"].get("endpoints", []):
            e = ep.rstrip("/").lower()
            if url == e:
                return rec["server"]["id"], True
            if url.startswith(e + "/"):
                return rec["server"]["id"], False
    return None, False


def _tool_names(registry: Registry, sid: str) -> set[str]:
    return {t["name"] for r in registry.records if r["server"]["id"] == sid for t in r["tools"]}


def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def suggest_by_tools(tools: set[str], registry: Registry) -> tuple[str | None, float]:
    """The record whose tool names best match. A suggestion only: tool names are chosen by the server."""
    best, score = None, 0.0
    for sid in registry.servers():
        s = overlap(tools, _tool_names(registry, sid))
        if s > score:
            best, score = sid, s
    return (best, score) if score >= MIN_TOOL_OVERLAP and len(tools) >= 3 else (None, score)


# --------------------------------------------------------------------------- minimal MCP client

class McpError(RuntimeError):
    def __init__(self, message: str, server_text: str | None = None):
        super().__init__(message)
        self.server_text = server_text  # shown only with --show-server-errors, and redacted


_OVERSIZE = object()


def _stdout_reader(stream, q: "queue.Queue") -> None:
    try:
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE and not line.endswith(b"\n"):
                while True:  # discard the rest of an oversized line without holding it
                    more = stream.readline(MAX_LINE)
                    if not more or more.endswith(b"\n"):
                        break
                q.put(_OVERSIZE)
                continue
            q.put(line)
    except (OSError, ValueError):
        pass
    q.put(None)


def _stderr_reader(stream, keep: deque) -> None:
    try:
        while True:
            line = stream.readline(4096)
            if not line:
                break
            keep.append(line[:500])
    except (OSError, ValueError):
        pass


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop the server and everything it started in its process group (npx, uvx, sh -c wrappers)."""
    try:
        if os.name == "posix":
            for sig, wait in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    proc.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    pass
        else:
            if proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    except OSError:
        pass
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def server_env(cfg: ServerConfig, inherit: bool) -> dict[str, str]:
    if inherit:
        return {**os.environ, **cfg.env}
    base = {k: v for k in SDK_DEFAULT_ENV + NETWORK_ENV
            if (v := os.environ.get(k)) is not None and not v.startswith("()")}
    return {**base, **cfg.env}


@dataclass
class Listing:
    tools: list[dict]
    server_info: dict
    truncated: bool = False
    duplicates: int = 0
    invalid_names: int = 0


def list_tools_stdio(cfg: ServerConfig, timeout: float = 60.0, inherit_env: bool = False) -> Listing:
    """Start a stdio server, complete the MCP handshake, page through tools/list, and stop it."""
    if not cfg.command:
        raise McpError("no command configured")
    if cfg.cwd and not Path(cfg.cwd).is_dir():
        raise McpError("its working directory (`cwd`) does not exist")
    env = server_env(cfg, inherit_env)
    exe = shutil.which(cfg.command, path=env.get("PATH")) or cfg.command
    kw: dict[str, Any] = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=cfg.cwd or None)
    if os.name == "posix":
        kw["start_new_session"] = True
    else:
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen([exe, *cfg.args], **kw)
    except OSError as exc:
        raise McpError(f"could not start `{clean(Path(cfg.command).name, 40)}` ({exc.strerror or 'not found'})") from None

    q: "queue.Queue" = queue.Queue(maxsize=256)
    err_tail: deque = deque(maxlen=STDERR_KEEP)
    threading.Thread(target=_stdout_reader, args=(proc.stdout, q), daemon=True).start()
    threading.Thread(target=_stderr_reader, args=(proc.stderr, err_tail), daemon=True).start()
    timed_out = threading.Event()

    def on_deadline() -> None:
        timed_out.set()
        _kill_group(proc)

    watchdog = threading.Timer(timeout, on_deadline)
    watchdog.daemon = True
    watchdog.start()
    launcher = Path(cfg.command).name in ("npx", "uvx", "bunx", "pipx")
    next_id = 0

    def stderr_text() -> str:
        return b"".join(err_tail).decode(errors="replace")

    def timeout_error(method: str) -> McpError:
        hint = " (the first `npx -y`/`uvx` run downloads the server; try a longer --timeout)" if launcher else ""
        return McpError(f"timed out after {timeout:.0f}s waiting for {method}{hint}", stderr_text())

    def send(msg: dict) -> None:
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            proc.stdin.flush()
        except (OSError, ValueError):
            if timed_out.is_set():
                raise timeout_error(msg.get("method", "a reply")) from None
            code = proc.poll()
            raise McpError(f"server exited{f' (code {code})' if code is not None else ''} before it could be asked for its tools",
                           stderr_text()) from None

    def request(method: str, params: dict) -> dict:
        nonlocal next_id
        next_id += 1
        rid = next_id
        send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                line = q.get(timeout=0.25)
            except queue.Empty:
                if timed_out.is_set():
                    raise timeout_error(method)
                continue
            if line is None:
                if timed_out.is_set():
                    raise timeout_error(method)
                try:
                    code = proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    code = None
                raise McpError(f"server exited{f' (code {code})' if code is not None else ''} before answering {method}",
                               stderr_text())
            if line is _OVERSIZE:
                continue
            try:
                msg = json.loads(line)
            except (ValueError, RecursionError):
                continue  # servers sometimes log to stdout; skip anything that is not JSON
            if not isinstance(msg, dict) or "method" in msg or msg.get("id") != rid:
                continue  # notifications, requests from the server (never answered), stale replies
            if "error" in msg:
                err = msg["error"]
                text = err.get("message") if isinstance(err, dict) else err
                raise McpError(f"{method} failed (the server returned an error)",
                               str(text) if text is not None else None)
            result = msg.get("result")
            if not isinstance(result, dict):
                raise McpError(f"{method} returned a malformed result")
            return result

    try:
        init = request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                      "clientInfo": {"name": "rclass-scan", "version": rclass_version()}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listing = Listing([], {})
        names: set[str] = set()
        cursors: set[str] = set()
        cursor = None
        for _ in range(MAX_PAGES):
            res = request("tools/list", {"cursor": cursor} if cursor else {})
            batch = res.get("tools", [])
            if not isinstance(batch, list):
                raise McpError("tools/list returned a malformed result")
            for t in batch:
                if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"]:
                    listing.invalid_names += 1
                    continue
                if t["name"] in names:
                    listing.duplicates += 1
                    continue
                if len(listing.tools) >= MAX_TOOLS:
                    listing.truncated = True
                    break
                names.add(t["name"])
                listing.tools.append(t)
            cursor = res.get("nextCursor")
            if listing.truncated or cursor in (None, ""):
                break
            if not isinstance(cursor, str) or cursor in cursors:
                listing.truncated = True  # a repeating or malformed cursor: stop rather than loop
                break
            cursors.add(cursor)
        else:
            listing.truncated = True
        info = init.get("serverInfo") if isinstance(init.get("serverInfo"), dict) else {}
        listing.server_info = {k: clean(info[k], 80) for k in ("name", "version")
                               if isinstance(info.get(k), (str, int, float))}
        return listing
    finally:
        watchdog.cancel()
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        _kill_group(proc)


# --------------------------------------------------------------------------- classification for a scan

_REVIEW_ORDER = ["draft", "reviewed", "verified"]


def _entry_worst(e: ToolEntry) -> RClass:
    return max([e.base] + [RClass.parse(r["class"]) for r in e.raw.get("when", [])])


def worst_case(registry: Registry, sid: str | None, tool: str) -> tuple[RClass, str, str, bool, str | None]:
    """(class, source, rationale, lower_for_some_arguments, review) over every argument a call could use.

    SPEC 4.1: a tool is classed at the highest level it can reach. Pre-image overrides are not
    applied: a scan cannot know whether a gateway's capture will succeed, so it reports the fallback.
    """
    entries = registry.entries(sid, tool) if sid else []
    ov = registry.override(sid, tool) if sid else None
    if ov is not None and "preimage" in ov[0].raw:
        ov = None
    if entries:
        worst_entry = max(entries, key=_entry_worst)
        cls = _entry_worst(worst_entry)
        review = min((e.raw.get("review", "draft") for e in entries), key=_REVIEW_ORDER.index)
        lower = any(RClass.parse(r["class"]) < cls for e in entries for r in e.raw.get("when", [])) \
            or any(e.base < cls for e in entries)
        if ov is not None:
            ov_entry, reason = ov
            ov_cls = _entry_worst(ov_entry)
            disclosure = any(e.disclosure for e in entries)
            if ov_cls > cls or (ov_cls < cls and (not disclosure or ov_entry.raw.get("disclosure") is False)):
                return ov_cls, "override", f"{ov_entry.raw['rationale']} (override: {reason})", \
                    any(RClass.parse(r["class"]) < ov_cls for r in ov_entry.raw.get("when", [])), review
        return cls, "registry", worst_entry.raw["rationale"], lower, review
    if ov is not None:
        return _entry_worst(ov[0]), "override", f"{ov[0].raw['rationale']} (override: {ov[1]})", False, None
    default = registry.default_for(sid) if sid else None
    if default is not None:
        return default, "registry", "Server default_class for unlisted tools.", False, None
    return RClass.R4, "fail-closed", "No classification record; failing closed.", False, None


# --------------------------------------------------------------------------- report

@dataclass
class ToolReport:
    name: str
    rclass: RClass
    source: str
    rationale: str
    lower_for_some_arguments: bool = False
    review: str | None = None
    hint: RClass | None = None


@dataclass
class ServerReport:
    key: str
    config: str
    status: str                  # scanned / static / remote / skipped / error / duplicate
    record: str | None = None
    record_name: str | None = None
    record_version: str | None = None
    matched_by: str | None = None      # package / endpoint / assumed
    endpoint_exact: bool = True
    suggested: str | None = None
    suggested_score: float | None = None
    source_note: str | None = None
    server_info: dict = field(default_factory=dict)
    tools: list[ToolReport] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    not_offered: list[str] = field(default_factory=list)
    truncated: bool = False
    duplicates: int = 0
    invalid_names: int = 0
    message: str | None = None
    server_text: str | None = None
    target: str | None = None
    needs_consent: bool = False
    started: bool = False

    @property
    def counted(self) -> bool:
        return self.status in ("scanned", "static", "remote") and bool(self.tools)

    @property
    def unknown(self) -> bool:
        """Known to exist, tools unknowable without starting it: counts as R4 against a gate (SPEC 6)."""
        return self.status in ("static", "remote") and not self.record

    @property
    def incomplete(self) -> bool:
        return self.status == "error" or self.truncated or self.invalid_names > 0 or self.needs_consent

    def counts(self) -> dict[str, int]:
        c = {str(k): 0 for k in RClass}
        for t in self.tools:
            c[str(t.rclass)] += 1
        return c


def _tool_reports(sid: str | None, tools: list[dict], registry: Registry) -> tuple[list[ToolReport], list[str]]:
    out, unclassified = [], []
    for t in tools:
        name = t["name"]
        shown = name if TOOL_NAME_RE.match(name) else "unprintable:" + name.encode("unicode_escape").decode()[:100]
        cls, source, rationale, lower, review = worst_case(registry, sid, name)
        ann = t.get("annotations")
        hint = from_mcp_annotations(ann) if (source == "fail-closed" and isinstance(ann, Mapping)) else None
        if source == "fail-closed":
            unclassified.append(shown)
        out.append(ToolReport(shown, cls, source, rationale, lower, review, hint))
    out.sort(key=lambda r: (-int(r.rclass), r.name))
    return out, unclassified


def _record_meta(registry: Registry, sid: str) -> tuple[str, str | None]:
    for r in registry.records:
        if r["server"]["id"] == sid:
            return r["server"]["name"], r["server"].get("version")
    return sid, None


def scan_server(cfg: ServerConfig, registry: Registry, timeout: float = 60.0, start: bool = True,
                inherit_env: bool = False, assume: Mapping[str, str] | None = None,
                show_server_errors: bool = False, consent: bool = False) -> ServerReport:
    """Report one server. Never raises: anything unexpected becomes status `error`."""
    try:
        return _scan_server(cfg, registry, timeout, start, inherit_env, assume or {}, show_server_errors, consent)
    except Exception as exc:  # noqa: BLE001 - one broken server must not lose the whole report
        return ServerReport(key=cfg.key, config=cfg.source, status="error",
                            message=f"unexpected problem while scanning ({exc.__class__.__name__})")


def _scan_server(cfg, registry, timeout, start, inherit_env, assume, show_server_errors, consent) -> ServerReport:
    rep = ServerReport(key=cfg.key, config=cfg.source, status="scanned")
    if cfg.config_error:
        rep.status, rep.message = "error", f"invalid config entry: {cfg.config_error}"
        return rep
    if cfg.disabled:
        rep.status, rep.message = "skipped", cfg.disabled_reason
        return rep

    assumed = assume.get(cfg.key) or assume.get(cfg.key.split("/")[-1])
    if assumed and assumed not in registry.servers():
        rep.status, rep.message = "error", f"--assume names {clean(assumed, 40)!r}, which is not a registry record"
        return rep
    if assumed and (cfg.remote or not start):
        # An assumption about a server that is not started cannot be checked against what it offers,
        # and a config change could make it describe something else entirely.
        rep.status = "error"
        rep.message = "--assume applies only to servers that are started and listed; it is ignored with --list and for hosted servers"
        return rep

    def use_record(sid: str, how: str) -> None:
        rep.record, rep.matched_by = sid, how
        rep.record_name, rep.record_version = _record_meta(registry, sid)

    def registry_tools(sid: str) -> None:
        rep.tools, _ = _tool_reports(sid, [{"name": n} for n in sorted(_tool_names(registry, sid))], registry)

    if cfg.remote:
        sid, exact = identify_by_endpoint(cfg, registry)
        rep.status = "remote"
        if sid:
            use_record(sid, "endpoint")
            rep.endpoint_exact = exact
            registry_tools(sid)
            rep.message = "hosted: not started; classes are the registry's for this endpoint" + (
                "" if exact else " (a sub-path, which may expose fewer tools)")
        else:
            rep.message = "hosted server, not in the registry: not started; counted as R4"
        return rep

    sid, note = identify_by_package(cfg, registry)
    rep.target, _ = run_target(cfg.command, cfg.args, cfg.env, cfg.project_root)
    rep.source_note = note
    if assumed:
        use_record(assumed, "assumed")
    elif sid:
        use_record(sid, "package")

    if not start:
        rep.status = "static"
        if rep.record:
            registry_tools(rep.record)
            rep.message = "not started: classes are the registry's for this server"
        else:
            rep.message = "not started, not recognised: counted as R4" + (f" ({note})" if note else "")
        return rep

    if cfg.project and not (cfg.approved or consent):
        rep.status, rep.needs_consent = "skipped", True
        rep.message = "defined by this project's config: not started without --yes (use --list to see it without starting)"
        return rep
    if cfg.needs:
        rep.status = "error"
        rep.message = "needs values the client would supply or prompt for: " + ", ".join(sorted({clean(n, 40) for n in cfg.needs}))
        return rep

    rep.started = True
    try:
        listing = list_tools_stdio(cfg, timeout=timeout, inherit_env=inherit_env)
    except McpError as exc:
        rep.status, rep.message = "error", str(exc)
        if show_server_errors and exc.server_text:
            secrets = cfg.secrets() + ([v for v in os.environ.values() if len(v) >= 8] if inherit_env else [])
            rep.server_text = redact(_last_lines(exc.server_text), secrets)[:400]
        return rep

    rep.server_info = listing.server_info
    rep.truncated, rep.duplicates, rep.invalid_names = listing.truncated, listing.duplicates, listing.invalid_names
    offered = {t["name"] for t in listing.tools}
    if rep.record:
        rep.not_offered = sorted(_tool_names(registry, rep.record) - offered)
    else:
        rep.suggested, rep.suggested_score = suggest_by_tools(offered, registry)
    rep.tools, rep.unclassified = _tool_reports(rep.record, listing.tools, registry)
    return rep


@dataclass
class ScanResult:
    configs: list[str]
    servers: list[ServerReport]
    started: bool = True

    def counted_tools(self) -> list[ToolReport]:
        return [t for s in self.servers if s.counted for t in s.tools]

    def worst(self) -> RClass | None:
        """The worst class across everything the agent could reach. Unknown servers count as R4."""
        classes = [t.rclass for t in self.counted_tools()]
        if any(s.unknown for s in self.servers):
            classes.append(RClass.R4)
        return max(classes) if classes else None

    def incomplete(self) -> list[ServerReport]:
        return [s for s in self.servers if s.incomplete]

    def totals(self) -> dict[str, Any]:
        tools = self.counted_tools()
        by = {str(k): sum(1 for t in tools if t.rclass is k) for k in RClass}
        reviews = [t.review for t in tools if t.review]
        return {
            "servers": len(self.servers),
            "servers_counted": sum(1 for s in self.servers if s.counted),
            "servers_unknown": sum(1 for s in self.servers if s.unknown),
            "servers_not_scanned": [s.key for s in self.incomplete()],
            "tools": len(tools),
            "by_class": by,
            "unclassified": sum(1 for t in tools if t.source == "fail-closed"),
            "autonomous_under_default_policy": by["R0"] + by["R1"],
            "registry_classified": len(reviews),
            "reviewed_by_a_person": sum(1 for r in reviews if r in ("reviewed", "verified")),
        }

    def to_dict(self) -> dict:
        worst = self.worst()
        return {
            "report_version": REPORT_VERSION,
            "rclass_version": rclass_version(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "started_servers": self.started,
            "configs": self.configs,
            "totals": self.totals(),
            "worst_class": str(worst) if worst is not None else None,
            "servers": [
                {
                    "key": s.key, "config": s.config, "status": s.status,
                    "record": s.record, "record_name": s.record_name, "record_version": s.record_version,
                    "matched_by": s.matched_by, "suggested_record": s.suggested,
                    "suggested_score": round(s.suggested_score, 3) if s.suggested_score is not None else None,
                    "source_note": s.source_note, "server_info": s.server_info, "message": s.message,
                    **({"server_text_redacted": s.server_text} if s.server_text else {}),
                    "truncated": s.truncated, "duplicate_tools": s.duplicates, "invalid_tool_names": s.invalid_names,
                    "counts": s.counts(), "unclassified": s.unclassified, "not_offered": s.not_offered,
                    "tools": [
                        {"name": t.name, "class": str(t.rclass), "source": t.source, "rationale": t.rationale,
                         "lower_for_some_arguments": t.lower_for_some_arguments, "review": t.review,
                         **({"annotation_hint": str(t.hint)} if t.hint is not None else {})}
                        for t in s.tools
                    ],
                }
                for s in self.servers
            ],
        }


def collect(configs: list[Path], all_projects: bool = False, cwd: Path | None = None,
            home: Path | None = None) -> list[ServerConfig]:
    servers: list[ServerConfig] = []
    for p in configs:
        servers.extend(load_config(p, cwd=cwd, home=home, all_projects=all_projects))
    return servers


def scan_servers(servers: list[ServerConfig], configs: list[str], registry: Registry, timeout: float = 60.0,
                 start: bool = True, **kw) -> ScanResult:
    """Scan each server once; an exact duplicate (same command, args, env, url, cwd) is counted once."""
    reports: list[ServerReport] = []
    first: dict[tuple, str] = {}
    for s in servers:
        fp = s.fingerprint()
        if not s.config_error and fp in first:
            reports.append(ServerReport(key=s.key, config=s.source, status="duplicate",
                                        message=f"same server as {clean(first[fp], 60)}: counted once"))
            continue
        r = scan_server(s, registry, timeout=timeout, start=start, **kw)
        if r.status in ("scanned", "static", "remote", "error"):
            first.setdefault(fp, s.key)  # a skipped or disabled copy never hides an active one
        reports.append(r)
    return ScanResult(configs, reports, started=start)


def scan(configs: list[Path], registry: Registry, timeout: float = 60.0, start: bool = True,
         only: list[str] | None = None, all_projects: bool = False, cwd: Path | None = None,
         home: Path | None = None, **kw) -> ScanResult:
    servers = collect(configs, all_projects=all_projects, cwd=cwd, home=home)
    if only:
        servers = [s for s in servers if s.key in only or s.key.split("/")[-1] in only]
    return scan_servers(servers, [str(p) for p in configs], registry, timeout=timeout, start=start, **kw)


# --------------------------------------------------------------------------- text output

def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _bar(counts: Mapping[str, int]) -> str:
    return "  ".join(f"{k} {v}" for k, v in counts.items())


def _names(items: list[str], limit: int = 6) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f", +{len(items) - limit} more" if len(items) > limit else "")


def render_text(result: ScanResult, details: bool = False, drafts_written: bool = False,
                show_server_errors: bool = False) -> str:
    out: list[str] = []
    multi = len(result.configs) > 1
    w = min(max([len(clean(s.key, 60)) for s in result.servers] + [8]), 40)
    current_cfg = None
    for s in result.servers:
        if multi and s.config != current_cfg:
            current_cfg = s.config
            out.append(f"── {s.config}")
        key = clean(s.key, 60)
        head, pad = (f"{key:<{w}}  ", " " * (w + 2)) if len(key) <= w else (f"{key}\n{' ' * (w + 2)}", " " * (w + 2))
        if s.status in ("skipped", "duplicate", "error") or (s.status in ("static", "remote") and not s.tools):
            what = f"{s.record_name} · " if s.record_name else ""
            verb = "could not list tools: " if s.status == "error" else ""
            out.append(f"{head}{what}{verb}{s.message}")
            if s.server_text:
                out.append(f"{pad}server said (redacted): {clean(s.server_text, 300)}")
            elif s.status == "error" and s.started and not show_server_errors:
                out.append(f"{pad}(add --show-server-errors to see what the server reported, with secrets redacted)")
            out.append("")
            continue
        if s.record:
            how = " · assumed with --assume" if s.matched_by == "assumed" else ""
            ver = f" · record checked at {s.record_version}" if (details and s.record_version) else ""
            out.append(f"{head}{s.record_name} · {_n(len(s.tools), 'tool')}{how}{ver}")
        else:
            out.append(f"{head}not in the registry · {_n(len(s.tools), 'tool')}, all counted as R4 (fail closed)")
        if s.message:
            out.append(f"{pad}{s.message}")
        if s.source_note:
            out.append(f"{pad}! {s.source_note}; not matched to a registry record")
        if s.tools:
            out.append(f"{pad}{_bar(s.counts())}")
        irreversible = [t.name for t in s.tools if t.rclass is RClass.R4 and t.source != "fail-closed"]
        hard = [t.name for t in s.tools if t.rclass is RClass.R3]
        if irreversible:
            out.append(f"{pad}irreversible (R4): {_names(irreversible)}")
        if hard:
            out.append(f"{pad}approval each time (R3): {_names(hard)}")
        if s.unclassified and s.record:
            out.append(f"{pad}! {len(s.unclassified)} not in the registry record, counted as R4: {_names(s.unclassified)}")
        if s.not_offered:
            out.append(f"{pad}{len(s.not_offered)} in the record not offered here (switched off in this setup, "
                       f"or a different version): {_names(s.not_offered, 4)}")
        if s.suggested:
            out.append(f"{pad}its tool names look like {s.suggested} ({s.suggested_score:.0%} match). If it is, rerun with "
                       f"--assume {clean(s.key.split('/')[-1], 40)}={s.suggested}")
        hints = [t for t in s.tools if t.hint is not None]
        if hints and not s.record:
            ro = sum(1 for t in hints if t.hint is RClass.R0)
            out.append(f"{pad}its own annotations call {ro} of them read-only; annotations are not trusted (SPEC 7)")
        if s.truncated:
            out.append(f"{pad}! the tool list was cut short (repeating cursor, or over {MAX_TOOLS} tools): scan incomplete")
        if s.duplicates:
            out.append(f"{pad}! the server listed {_n(s.duplicates, 'tool name')} more than once; each counted once")
        if s.invalid_names:
            out.append(f"{pad}! {_n(s.invalid_names, 'tool')} had no valid name: scan incomplete")
        if details:
            for t in s.tools:
                note = "  (lower for some arguments)" if t.lower_for_some_arguments else ""
                rev = "" if t.review in (None, "draft") else f"  [{t.review}]"
                out.append(f"{pad}  {t.rclass}  {t.name}{note}{rev}")
        out.append("")

    tot = result.totals()
    tools = result.counted_tools()
    if tools:
        from_registry = sum(len(s.tools) for s in result.servers if s.status in ("remote", "static") and s.counted)
        line = f"{_n(len(tools), 'tool')} across {_n(tot['servers_counted'], 'server')}"
        if from_registry:
            line += f" ({from_registry} as the registry lists them, not from a running server)"
        unc = tot["unclassified"]
        line += f". {tot['by_class']['R4']} irreversible (R4)" + (
            f", {unc} of them only because nobody has classified {'it' if unc == 1 else 'them'} yet." if unc else ".")
        out.append(line)
        alone, rest = tot["autonomous_under_default_policy"], len(tools) - tot["autonomous_under_default_policy"]
        out.append(f"Under the default policy an agent may act alone on {alone} (R0–R1)"
                   + (f"; {'the other one needs' if rest == 1 else f'the other {rest} need'} a human." if rest else "."))
        out.append("Each tool is counted at the worst class its arguments can reach.")
        if tot["registry_classified"]:
            rev = tot["reviewed_by_a_person"]
            out.append(("Registry classes are drafts" if rev == 0 else f"Registry classes are drafts except {rev}")
                       + ": checked against source by their author, not yet reviewed by a second person.")
    if tot["servers_unknown"]:
        n = tot["servers_unknown"]
        out.append(f"{_n(n, 'server')} could not be identified and {'counts' if n == 1 else 'count'} as R4.")
    if tot["servers_not_scanned"]:
        out.append(f"Not scanned: {', '.join(clean(k, 40) for k in tot['servers_not_scanned'])}.")
    if tot["unclassified"] and not drafts_written:
        out.append("Help classify the missing tools: rerun with --write-drafts DIR, then see "
                   "https://github.com/TwoMuchLabs/reversibility-classes/blob/main/CONTRIBUTING.md")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- draft records

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "server"


DRAFT_MARKERS = ("example.invalid", "UNREVIEWED:")


def draft_record(rep: ServerReport, tools: list[ToolReport]) -> str:
    """A starting point for a contribution: every unclassified tool, each at R4 until checked."""
    import yaml

    additions = rep.record is not None
    sid = rep.record or _slug(rep.server_info.get("name") or rep.key.split("/")[-1])
    server: dict[str, Any] = {
        "id": sid,
        "name": rep.record_name or rep.server_info.get("name") or clean(rep.key, 60),
        "source": "https://example.invalid/REPLACE-with-the-server-repository",
        "evidence": ["https://example.invalid/REPLACE-with-the-file-where-tools-are-registered"],
    }
    if rep.server_info.get("version"):
        server["version"] = str(rep.server_info["version"])
    if rep.target and not additions and not rep.source_note:
        server["packages"] = [_strip_version(rep.target)]
    doc = {"spec_version": "0.2", "server": server, "tools": [
        {"name": t.name, "class": "R4",
         "rationale": "UNREVIEWED: starts at R4 (fail closed). Replace with the fact that decides its class."
         + (f" The server's own annotations suggest {t.hint}, which is not evidence." if t.hint is not None else ""),
         "review": "draft"}
        for t in tools if not t.name.startswith("unprintable:")
    ]}
    head = ("# DRAFT generated by `rclass scan`. Not ready to submit: `rclass validate --strict` rejects it until finished.\n"
            + (f"# These are tools the {sid} record does not cover yet. Add them to registry/{sid}.yaml;\n"
               "# do not replace the existing record with this file.\n" if additions else "")
            + "# 1. Fill in server.source and server.evidence" + ("" if additions else ", and check server.packages") + ".\n"
            "# 2. Check every tool against the server's code and give it a real class and rationale.\n"
            "# 3. Name the undo (`compensation`) for anything you put at R1 or R2.\n"
            "# See CONTRIBUTING.md.\n")
    return head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def draft_path(out: Path, rep: ServerReport) -> Path:
    base = _slug(rep.record or rep.server_info.get("name") or rep.key.split("/")[-1])
    name = f"{base}.additions" if rep.record else base
    p, i = out / f"{name}.yaml", 2
    while p.exists():
        p, i = out / f"{name}-{i}.yaml", i + 1
    return p
