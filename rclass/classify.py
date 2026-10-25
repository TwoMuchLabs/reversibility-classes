"""Assign a Reversibility Class to one action (SPEC sections 4-7)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .model import Classification, RClass
from .registry import Registry, ToolEntry
from .versions import rules_apply

_MISSING = object()


def _get_arg(args: Mapping[str, Any] | None, path: str) -> Any:
    """Look up an argument; dots address nested mappings (e.g. "options.force")."""
    current: Any = args if args is not None else {}
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _condition_holds(cond: Mapping[str, Any], args: Mapping[str, Any] | None) -> bool:
    """Evaluate one condition. Anything missing or malformed counts as *not* matching,
    so evaluation falls back to the tool's base class (SPEC 5.3 safety requirement)."""
    value = _get_arg(args, cond["arg"])
    if "absent" in cond:
        return (value is _MISSING) == bool(cond["absent"])
    if value is _MISSING:
        return False
    if "equals" in cond:
        return type(value) is type(cond["equals"]) and value == cond["equals"]
    if "in" in cond:
        return any(type(value) is type(v) and value == v for v in cond["in"])
    if "matches" in cond:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return False
        try:
            return re.search(cond["matches"], str(value), re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _rule_holds(rule: Mapping[str, Any], args: Mapping[str, Any] | None) -> bool:
    if "all" in rule:
        return all(_condition_holds(c, args) for c in rule["all"])
    return _condition_holds(rule, args)


def _evaluate(entry: ToolEntry, args: Mapping[str, Any] | None,
              lowering: bool = True) -> tuple[RClass, str, int | None]:
    """The first matching `when` rule, else the base class. With `lowering=False`, a rule that would
    lower the class is skipped (the deployed version is older than the record was checked against)."""
    for i, rule in enumerate(entry.raw.get("when", [])):
        if _rule_holds(rule, args):
            cls = RClass.parse(rule["class"])
            if not lowering and cls < entry.base:
                return entry.base, (f"{entry.raw['rationale']} (Rules that lower this class were checked against "
                                    f"release {entry.min_version} or later; this server runs an older or unknown "
                                    f"release.)"), None
            return cls, rule["rationale"], i
    return entry.base, entry.raw["rationale"], None


def from_mcp_annotations(annotations: Mapping[str, Any] | None) -> RClass:
    """Provisional class from MCP tool annotations (SPEC section 7).

    Only call this for servers the deployment has explicitly marked as trusted.
    Absent hints take MCP defaults: readOnly=false, destructive=true, openWorld=true.
    """
    a = annotations or {}
    read_only = a.get("readOnlyHint", False) is True
    destructive = a.get("destructiveHint", True) is not False
    open_world = a.get("openWorldHint", True) is not False
    if read_only:
        return RClass.R0
    if open_world:
        return RClass.R4
    return RClass.R3 if destructive else RClass.R1


def _strictest_override(reg: Registry, server: str, tool: str, args):
    """When several overrides cover a tool, the one giving this call the highest class applies (SPEC 5.4)."""
    best = None
    for entry, reason in reg.overrides(server, tool):
        ev = _evaluate(entry, args)
        if best is None or ev[0] > best[2][0]:
            best = (entry, reason, ev)
    return best


def classify(
    server: str,
    tool: str,
    args: Mapping[str, Any] | None = None,
    *,
    registry: Registry | None = None,
    annotations: Mapping[str, Any] | None = None,
    trusted_server: bool = False,
    server_version: str | None = None,
    version_pinned: bool = False,
) -> Classification:
    """Classify one tool call.

    Precedence: registry records (highest class across records wins), then deployment
    overrides, then a record's default_class, then MCP annotations for trusted servers
    only, then fail closed at R4.
    """
    reg = registry if registry is not None else _default_registry()
    entries = reg.entries(server, tool)

    if entries:
        results = [(_evaluate(e, args, rules_apply(e.min_version, server_version, version_pinned)), e)
                   for e in entries]
        (rclass, rationale, rule_index), entry = max(results, key=lambda r: r[0][0])
        source = "rule" if rule_index is not None else "registry"
        result = Classification(rclass, source, rationale, server, tool, rule_index)

        ov = _strictest_override(reg, server, tool, args)
        if ov is not None:
            ov_entry, reason, (ov_class, ov_rationale, ov_rule) = ov
            disclosure = any(e.disclosure for e in entries)
            # A disclosure tool may be lowered only when the override explicitly asserts
            # that, in this deployment, it reaches no one outside the principal (SPEC 5.5).
            asserts_private = ov_entry.raw.get("disclosure") is False
            if ov_class > rclass or (ov_class < rclass and (not disclosure or asserts_private)):
                lowered_by_capture = ov_class < rclass and "preimage" in ov_entry.raw
                result = Classification(
                    ov_class, "override", f"{ov_rationale} (override: {reason})", server, tool, ov_rule,
                    preimage=ov_entry.raw["preimage"] if lowered_by_capture else None,
                    fallback=rclass if lowered_by_capture else None,
                )
        return result

    ov = _strictest_override(reg, server, tool, args)
    if ov is not None:  # a deployment classifying a tool the open registry doesn't cover
        ov_entry, reason, (ov_class, ov_rationale, ov_rule) = ov
        cap = ov_entry.raw.get("preimage")
        return Classification(
            ov_class, "override", f"{ov_rationale} (override: {reason})", server, tool, ov_rule,
            preimage=cap, fallback=RClass.R4 if cap else None,  # unconfirmed capture fails closed
        )

    default = reg.default_for(server)
    if default is not None:
        return Classification(default, "registry", "Server default_class for unlisted tools.", server, tool)

    if trusted_server and annotations is not None:
        return Classification(
            from_mcp_annotations(annotations),
            "annotations",
            "Provisional class from MCP annotations of a trusted server.",
            server,
            tool,
        )

    return Classification(RClass.R4, "fail-closed", "No classification record; failing closed.", server, tool)


_DEFAULT: Registry | None = None


def _default_registry() -> Registry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Registry.bundled()
    return _DEFAULT
