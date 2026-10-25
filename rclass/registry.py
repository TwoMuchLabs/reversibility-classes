"""Load, validate and index classification records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator

from .model import RClass


def _load_schema() -> dict:
    # Packaged copy first (pip install), then the repository copy (source checkout).
    try:
        return json.loads(resources.files("rclass").joinpath("rclass.schema.json").read_text())
    except (FileNotFoundError, ModuleNotFoundError):
        repo_copy = Path(__file__).resolve().parent.parent / "schema" / "rclass.schema.json"
        return json.loads(repo_copy.read_text())


SCHEMA = _load_schema()
_VALIDATOR = Draft202012Validator(SCHEMA)


class RecordError(ValueError):
    """A classification record failed validation."""


def validate_record(record: dict, origin: str = "<record>") -> None:
    """Raise RecordError listing every schema and consistency problem in a record."""
    problems = [
        f"{'/'.join(str(p) for p in err.absolute_path) or '(root)'}: {err.message}"
        for err in sorted(_VALIDATOR.iter_errors(record), key=lambda e: list(e.absolute_path))
    ]
    if not problems:
        for pkg in record["server"].get("packages", []):
            if _has_version(pkg):
                problems.append(f"server/packages: {pkg!r} carries a version; list the bare package or image name")
        seen: set[str] = set()
        for tool in record["tools"]:
            if tool["name"] in seen:
                problems.append(f"tools: duplicate tool name {tool['name']!r}")
            seen.add(tool["name"])
            if tool.get("disclosure") and tool["class"] != "R4":
                problems.append(f"tools/{tool['name']}: disclosure tools must have base class R4")
            if "preimage" in tool:
                # Pre-image capture is a claim of restorability (SPEC 5.6): it makes a tool R1,
                # never lower, and it cannot reverse a disclosure.
                if tool["class"] != "R1" or any(r.get("class") != "R1" for r in tool.get("when", [])):
                    problems.append(f"tools/{tool['name']}: an entry with preimage must be R1, with no other classes in its rules")
                if tool.get("disclosure"):
                    problems.append(f"tools/{tool['name']}: preimage cannot apply to a disclosure; capturing state does not un-disclose it")
    if problems:
        raise RecordError(f"{origin}:\n  " + "\n  ".join(problems))


def _has_version(pkg: str) -> bool:
    """True for "pkg@1.2", "@scope/pkg@1.2", "pkg==1.2" or "image:tag" (but not "host:port/image")."""
    if "==" in pkg:
        return True
    name = pkg[1:] if pkg.startswith("@") else pkg
    if "@" in name:
        return True
    last = pkg.rsplit("/", 1)[-1]
    return ":" in last


def read_record_file(path: Path) -> dict:
    """Read one record file. Every way this can fail raises RecordError with a one-line reason."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RecordError(f"{path}: file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise RecordError(f"{path}: could not read ({exc.__class__.__name__})") from None
    try:
        data = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        raise RecordError(f"{path}: not valid YAML{where}") from None
    except json.JSONDecodeError as exc:
        raise RecordError(f"{path}: not valid JSON at line {exc.lineno}") from None
    if not isinstance(data, dict):
        raise RecordError(f"{path}: a record must be a mapping with spec_version, server and tools")
    return data


@dataclass
class ToolEntry:
    server: str
    raw: dict

    @property
    def name(self) -> str:
        return self.raw["name"]

    @property
    def base(self) -> RClass:
        return RClass.parse(self.raw["class"])

    @property
    def disclosure(self) -> bool:
        return bool(self.raw.get("disclosure", False))


@dataclass
class Registry:
    """An index of classification records, plus optional deployment overrides."""

    records: list[dict] = field(default_factory=list)
    _tools: dict[tuple[str, str], list[ToolEntry]] = field(default_factory=dict)
    _defaults: dict[str, list[RClass]] = field(default_factory=dict)
    _overrides: dict[tuple[str, str], ToolEntry] = field(default_factory=dict)
    _override_reasons: dict[tuple[str, str], str] = field(default_factory=dict)

    # -- loading ---------------------------------------------------------
    def add(self, record: dict, origin: str = "<record>") -> None:
        validate_record(record, origin)
        self.records.append(record)
        server = record["server"]["id"]
        if "default_class" in record:
            self._defaults.setdefault(server, []).append(RClass.parse(record["default_class"]))
        for tool in record["tools"]:
            self._tools.setdefault((server, tool["name"]), []).append(ToolEntry(server, tool))

    def add_override(self, record: dict, reason: str, origin: str = "<override>") -> None:
        """Register a deployment override (SPEC 5.5). `reason` is logged with every decision."""
        validate_record(record, origin)
        server = record["server"]["id"]
        for tool in record["tools"]:
            key = (server, tool["name"])
            self._overrides[key] = ToolEntry(server, tool)
            self._override_reasons[key] = reason

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "Registry":
        reg = cls()
        for p in paths:
            p = Path(p)
            files = sorted(p.rglob("*")) if p.is_dir() else [p]
            for f in files:
                if f.suffix in (".yaml", ".yml", ".json") and f.is_file():
                    reg.add(read_record_file(f), str(f))
        return reg

    @staticmethod
    def bundled_path() -> Path:
        """Where the open registry is: inside the installed package, or the repository's registry/ folder."""
        here = Path(__file__).resolve().parent
        for candidate in (here / "registry", here.parent / "registry"):
            if candidate.is_dir():
                return candidate
        raise FileNotFoundError("bundled registry not found")

    @classmethod
    def bundled(cls) -> "Registry":
        """The open registry shipped with rclass."""
        return cls.from_paths([cls.bundled_path()])

    # -- lookup ----------------------------------------------------------
    def entries(self, server: str, tool: str) -> list[ToolEntry]:
        return self._tools.get((server, tool), [])

    def default_for(self, server: str) -> RClass | None:
        defaults = self._defaults.get(server)
        return max(defaults) if defaults else None

    def override(self, server: str, tool: str) -> tuple[ToolEntry, str] | None:
        key = (server, tool)
        if key in self._overrides:
            return self._overrides[key], self._override_reasons[key]
        return None

    def servers(self) -> list[str]:
        return sorted({r["server"]["id"] for r in self.records})

    def tools_of(self, server: str) -> list[str]:
        return sorted({t for (s, t) in self._tools if s == server})

    def all_entries(self) -> list[ToolEntry]:
        return [e for group in self._tools.values() for e in group]


def stats(registry: Registry) -> dict[str, Any]:
    counts = {str(c): 0 for c in RClass}
    for entry in registry.all_entries():
        counts[str(entry.base)] += 1
    return {"servers": len(registry.servers()), "tools": len(registry.all_entries()), "by_class": counts}
