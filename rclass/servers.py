"""Recognise a company's own MCP servers: a mapping file the platform team maintains.

Public servers are recognised from the package or image their config runs (`server.packages`, `server.endpoints`).
An enterprise runs its own: an absolute launcher, an image from an internal registry, a package from an internal
npm or PyPI index, `python -m` with a managed interpreter, or a gateway URL. None of those match a public record,
so without a mapping every call to them is unrecognised (R4) and asks.

A mapping file says which of those corresponds to which registry record (public, or private via `--registry`):

```yaml
servers:
  - record: github                                    # a mirrored public image
    image: registry.corp.example/mcp/github-mcp-server
  - record: corp-billing                              # an internal launcher, with its exact arguments
    command: /opt/mcp/billing/bin/server
    args: [--stdio]
  - record: corp-db                                   # an internal npm package from an internal registry
    package: "@corp/db-mcp"
    registry: https://npm.corp.example
  - record: corp-tools                                # python -m with a managed interpreter
    python: /opt/corp/python3.12/bin/python3
    module: corp_tools.mcp
  - record: github                                    # a gateway that fronts a public server
    url: https://mcp-gw.corp.example/github
```

The trust sits in the mapping, so keep it where agents and repositories can't write: `/etc/rclass/servers.yaml`
(read by default), a path in `RCLASS_SERVERS`, or `--servers FILE`. The usual protections still apply: an `env`
that changes which code runs, a launcher inside the project, or two mappings naming different records leaves the
server unrecognised.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from . import trust

DEFAULT_PATH = Path("/etc/rclass/servers.yaml")
KINDS = ("image", "command", "package", "module", "url")


class MappingError(ValueError):
    """A server mapping file is malformed or names a record the registry doesn't have."""


@dataclass(frozen=True)
class ServerMapping:
    record: str
    kind: str
    value: str
    args: tuple[str, ...] | None = None
    registry: str | None = None
    python: str | None = None
    version: str | None = None
    origin: str = "<mapping>"


def _norm_url(u: str) -> str:
    return u.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()


def parse(doc: Any, origin: str) -> list[ServerMapping]:
    if not isinstance(doc, Mapping) or not isinstance(doc.get("servers"), list):
        raise MappingError(f"{origin}: expected a mapping with a `servers` list")
    out = []
    for i, e in enumerate(doc["servers"]):
        where = f"{origin}: servers[{i}]"
        if not isinstance(e, Mapping) or not isinstance(e.get("record"), str) or not e["record"]:
            raise MappingError(f"{where}: needs `record`, the registry record it runs")
        kinds = [k for k in KINDS if k in e]
        if len(kinds) != 1:
            raise MappingError(f"{where}: needs exactly one of {', '.join(KINDS)}")
        kind = kinds[0]
        value = e[kind]
        if not isinstance(value, str) or not value:
            raise MappingError(f"{where}: `{kind}` must be a non-empty string")
        if kind == "command" and not Path(value).is_absolute():
            raise MappingError(f"{where}: `command` must be an absolute path")
        if kind == "module" and not (isinstance(e.get("python"), str) and Path(e["python"]).is_absolute()):
            raise MappingError(f"{where}: `module` needs `python`, the absolute path of the interpreter")
        if kind == "package" and not isinstance(e.get("registry"), str):
            raise MappingError(f"{where}: `package` needs `registry`, the internal index it installs from")
        if kind == "url" and not value.lower().startswith("https://"):
            raise MappingError(f"{where}: `url` must be https")
        args = e.get("args")
        if args is not None and not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
            raise MappingError(f"{where}: `args` must be a list of strings")
        out.append(ServerMapping(e["record"], kind, value, tuple(args) if args is not None else None,
                                 e.get("registry"), e.get("python"), e.get("version"), origin))
    return out


def load(paths: Iterable[str | Path]) -> list[ServerMapping]:
    out: list[ServerMapping] = []
    for p in paths:
        p = trust.expand(p)
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise MappingError(f"{p}: server mapping file not found") from None
        except (OSError, yaml.YAMLError) as exc:
            raise MappingError(f"{p}: could not read ({exc.__class__.__name__})") from None
        out += parse(doc, str(p))
    return out


def default_paths(explicit: Iterable[str] | None = None) -> list[Path]:
    """`--servers` files, else RCLASS_SERVERS, else /etc/rclass/servers.yaml if it exists."""
    if explicit:
        return [Path(p) for p in explicit]
    env = trust.env("RCLASS_SERVERS")  # ignored inside a hook, where a repository could set it
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    return [DEFAULT_PATH] if DEFAULT_PATH.is_file() else []


def refuse_project_files(paths: Iterable[Path]) -> None:
    bad = [str(p) for p in paths if trust.inside_project(p)]
    if bad:
        raise MappingError(f"server mapping file {', '.join(bad)} is inside the project folder, where a repository "
                           "could supply it (for example through RCLASS_SERVERS in its .claude/settings.json); keep "
                           "it where agents and repositories can't write")


def check(mappings: Iterable[ServerMapping], records: Iterable[str]) -> None:
    known = set(records)
    bad = sorted({m.record for m in mappings if m.record not in known})
    if bad:
        raise MappingError(f"server mappings name records the registry doesn't have: {', '.join(bad)}")


def writable(paths: Iterable[Path]) -> list[Path]:
    """Mapping files the current user (and so an agent running as them) could edit."""
    return [p for p in paths if p.exists() and os.access(p, os.W_OK)]
