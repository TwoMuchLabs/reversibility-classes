"""Assign a Reversibility Class to one action (SPEC sections 4-7)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .model import Classification, RClass
from .registry import Registry, ToolEntry

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


def _evaluate(entry: ToolEntry, args: Mapping[str, Any] | None) -> tuple[RClass, str, int | None]:
    for i, rule in enumerate(entry.raw.get("when", [])):
        if _rule_holds(rule, args):
            return RClass.parse(rule["class"]), rule["rationale"], i
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


def classify(
    server: str,
    tool: str,
    args: Mapping[str, Any] | None = None,
    *,
    registry: Registry | None = None,
    annotations: Mapping[str, Any] | None = None,
    trusted_server: bool = False,
) -> Classification:
    """Classify one tool call.

    Precedence: registry records (highest class across records wins), then deployment
    overrides, then a record's default_class, then MCP annotations for trusted servers
    only, then fail closed at R4.
    """
    reg = registry if registry is not None else _default_registry()
    entries = reg.entries(server, tool)

    if entries:
        results = [(_evaluate(e, args), e) for e in entries]
        (rclass, rationale, rule_index), entry = max(results, key=lambda r: r[0][0])
        source = "rule" if rule_index is not None else "registry"
        result = Classification(rclass, source, rationale, server, tool, rule_index)

        ov = reg.override(server, tool)
        if ov is not None:
            ov_entry, reason = ov
            ov_class, ov_rationale, ov_rule = _evaluate(ov_entry, args)
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

    ov = reg.override(server, tool)
    if ov is not None:  # a deployment classifying a tool the open registry doesn't cover
        ov_entry, reason = ov
        ov_class, ov_rationale, ov_rule = _evaluate(ov_entry, args)
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
