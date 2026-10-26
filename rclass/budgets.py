"""R2 budgets: let compensable calls run without a prompt, up to a limit the platform team sets (SPEC 8).

SPEC 8 says an R2 call needs approval *or* must fall within a pre-approved budget. Without budgets every R2
call asks, and people who are asked dozens of times a day stop reading the prompt. A budget file names which
R2 tools may run unprompted, how often, and for how much:

```yaml
budgets:
  - name: supabase-branches            # shown in the reason and the audit log
    server: supabase                   # a registry record id (not the name in your MCP config)
    tools: [create_branch]             # optional; default: every R2 tool of the server
    max_calls: 5                       # calls allowed in each window
    window: 1d                         # s, m, h or d; omit for "per session"
    scope: user                        # user (all sessions of this OS user) or session (default)
  - name: small-credits
    server: corp-billing
    tools: [issue_credit]
    max_calls: 20
    window: 1d
    scope: user
    where: {currency: usd}             # only calls whose arguments equal these
    amount:                            # optional: limit an argument's value, in the tool's own units
      arg: amount_cents                # a dotted path into the arguments
      max_each: 5000
      max_total: 20000
```

What a budget never does:
- It never applies to R3 or R4. Those are approved one by one (SPEC 8).
- It never applies to an unrecognised server, a call the session guard raised to R4, or a server name that
  could be more than one record.
- It never applies when the amount argument is missing, not a number (strings, NaN and infinity included), or
  negative.
- The first budget that matches a call (server, tool and `where`) decides it. If that budget is spent, or the
  amount doesn't fit, the call asks; a broader budget further down never picks it up.

A call within budget is allowed and its spend recorded before it runs, under a lock, so parallel calls can't
both take the last unit. A call over budget asks, as before. Keep the budget file where agents can't write
(`/etc/rclass/budgets.yaml`, read by default; or `RCLASS_BUDGETS`, or `--budgets FILE`). The ledger sits with
the session state and has the same limit: an agent that can write anywhere on disk can reset it.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from . import trust
from .model import RClass

DEFAULT_PATH = Path("/etc/rclass/budgets.yaml")
_WINDOW = re.compile(r"^(\d+)\s*([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class BudgetError(ValueError):
    """A budget file is malformed or names a record the registry doesn't have."""


@dataclass(frozen=True)
class Amount:
    arg: str
    max_each: float | None = None
    max_total: float | None = None


@dataclass(frozen=True)
class Budget:
    name: str
    server: str
    max_calls: int
    tools: tuple[str, ...] | None = None
    window: int | None = None  # seconds; None means the whole session
    scope: str = "session"
    where: Mapping[str, Any] = field(default_factory=dict)
    amount: Amount | None = None
    origin: str = "<budgets>"

    def describe_window(self) -> str:
        if self.window is None:
            return "this session"
        for unit, secs in (("d", 86400), ("h", 3600), ("m", 60)):
            if self.window % secs == 0:
                n = self.window // secs
                word = {"d": "day", "h": "hour", "m": "minute"}[unit]
                return f"per {word}" if n == 1 else f"per {n} {word}s"
        return f"per {self.window} s"


def _number(v: Any) -> float | None:
    """A JSON number, finite. Strings are not amounts: "1e3", " 5" and "NaN" would all parse."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        n = float(v)
    except OverflowError:
        return None
    return n if math.isfinite(n) else None


def _limit(v: Any, where: str, key: str) -> float | None:
    if v is None:
        return None
    n = _number(v)
    if n is None or n < 0:
        raise BudgetError(f"{where}: `{key}` must be a finite, non-negative number")
    return n


def parse(doc: Any, origin: str) -> list[Budget]:
    if not isinstance(doc, Mapping) or not isinstance(doc.get("budgets"), list):
        raise BudgetError(f"{origin}: expected a mapping with a `budgets` list")
    out: list[Budget] = []
    names: set[str] = set()
    for i, e in enumerate(doc["budgets"]):
        where = f"{origin}: budgets[{i}]"
        if not isinstance(e, Mapping):
            raise BudgetError(f"{where}: expected a mapping")
        unknown = set(e) - {"name", "server", "tools", "max_calls", "window", "scope", "where", "amount"}
        if unknown:
            raise BudgetError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
        name, server = e.get("name"), e.get("server")
        if not isinstance(name, str) or not name:
            raise BudgetError(f"{where}: needs `name`")
        if name in names:
            raise BudgetError(f"{where}: two budgets are named '{name}'")
        names.add(name)
        if not isinstance(server, str) or not server:
            raise BudgetError(f"{where}: needs `server`, a registry record id")
        mc = e.get("max_calls")
        if isinstance(mc, bool) or not isinstance(mc, int) or mc < 0:
            raise BudgetError(f"{where}: `max_calls` must be a whole number, 0 or more")
        tools = e.get("tools")
        if tools is not None and not (isinstance(tools, list) and tools and all(isinstance(t, str) and t for t in tools)):
            raise BudgetError(f"{where}: `tools` must be a non-empty list of tool names")
        window = e.get("window")
        secs = None
        if window is not None:
            m = _WINDOW.match(str(window).strip())
            if not m or int(m.group(1)) == 0:
                raise BudgetError(f"{where}: `window` must look like 30m, 12h or 1d")
            secs = int(m.group(1)) * _UNITS[m.group(2)]
        scope = e.get("scope", "session")
        if scope not in ("session", "user"):
            raise BudgetError(f"{where}: `scope` must be session or user")
        if scope == "user" and secs is None:
            raise BudgetError(f"{where}: a user budget needs a `window`, or it never resets")
        cond = e.get("where") or {}
        if not isinstance(cond, Mapping) or not all(isinstance(k, str) for k in cond) or \
                not all(isinstance(v, (str, int, float, bool)) for v in cond.values()):
            raise BudgetError(f"{where}: `where` maps argument names to plain values")
        amount = None
        a = e.get("amount")
        if a is not None:
            if not isinstance(a, Mapping) or not isinstance(a.get("arg"), str) or not a["arg"]:
                raise BudgetError(f"{where}: `amount` needs `arg`, the argument that carries the amount")
            amount = Amount(a["arg"], _limit(a.get("max_each"), where, "amount.max_each"),
                            _limit(a.get("max_total"), where, "amount.max_total"))
            if amount.max_each is None and amount.max_total is None:
                raise BudgetError(f"{where}: `amount` needs `max_each`, `max_total` or both")
        out.append(Budget(name, server, mc, tuple(tools) if tools else None, secs, scope, dict(cond), amount, origin))
    return out


def load(paths: Iterable[str | Path]) -> list[Budget]:
    out: list[Budget] = []
    for p in paths:
        p = Path(p).expanduser()
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise BudgetError(f"{p}: budget file not found") from None
        except (OSError, yaml.YAMLError) as exc:
            raise BudgetError(f"{p}: could not read ({exc.__class__.__name__})") from None
        out += parse(doc, str(p))
    seen: set[str] = set()
    for b in out:
        if b.name in seen:
            raise BudgetError(f"two budgets are named '{b.name}' ({b.origin})")
        seen.add(b.name)
    return out


def refuse_project_files(paths: Iterable[Path]) -> None:
    bad = [str(p) for p in paths if trust.inside_project(p)]
    if bad:
        raise BudgetError(f"budget file {', '.join(bad)} is inside the project folder, where a repository could "
                          "supply it (for example through RCLASS_BUDGETS in its .claude/settings.json); keep it "
                          "where agents and repositories can't write")


def default_paths(explicit: Iterable[str] | None = None) -> list[Path]:
    """`--budgets` files, else RCLASS_BUDGETS, else /etc/rclass/budgets.yaml if it exists."""
    if explicit:
        return [Path(p) for p in explicit]
    env = trust.env("RCLASS_BUDGETS")  # ignored inside a hook, where a repository could set it
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    return [DEFAULT_PATH] if DEFAULT_PATH.is_file() else []


def check(budgets: Iterable[Budget], registry) -> None:
    """Every budget names a known record, and every listed tool exists there. (A tool that is never R2 is
    harmless in a budget: the budget simply never applies to it.)"""
    problems = []
    servers = set(registry.servers())
    for b in budgets:
        if b.server not in servers:
            problems.append(f"'{b.name}' names server '{b.server}', which the registry doesn't have")
            continue
        for t in b.tools or ():
            if t not in registry.tools_of(b.server):
                problems.append(f"'{b.name}' names tool '{t}', which '{b.server}' doesn't have")
    if problems:
        raise BudgetError("; ".join(problems))


def _get(args: Mapping[str, Any] | None, path: str) -> Any:
    cur: Any = args
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def matches(b: Budget, server: str, tool: str, args: Mapping[str, Any] | None) -> bool:
    if b.server != server or (b.tools is not None and tool not in b.tools):
        return False
    return all(_get(args, k) == v and type(_get(args, k)) is type(v) for k, v in b.where.items())


@dataclass
class Spend:
    budget: Budget
    calls_used: int  # including this call
    total_used: float | None = None

    def describe(self) -> str:
        b = self.budget
        s = f"within budget '{b.name}': call {self.calls_used} of {b.max_calls} {b.describe_window()}"
        if self.total_used is not None and b.amount and b.amount.max_total is not None:
            s += f", {b.amount.arg} {self.total_used:g} of {b.amount.max_total:g}"
        return s

    def to_log(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.budget.name, "calls": self.calls_used, "max_calls": self.budget.max_calls}
        if self.total_used is not None:
            out["total"] = self.total_used
        return out


def _stale(entries: Any, before: float) -> bool:
    """Every entry older than `before`. An unreadable one counts as stale, so it can't take every budget down."""
    if not isinstance(entries, list):
        return True
    for x in entries:
        try:
            if float(x[0]) >= before:
                return False
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return True


def shadowed(budgets: list[Budget]) -> list[tuple[Budget, Budget]]:
    """(earlier, later) pairs where every call the later budget matches, the earlier one matches first, so the
    later one never applies. Limits don't stack: the first match decides."""
    out = []
    for i, a in enumerate(budgets):
        for b in budgets[i + 1:]:
            if a.server != b.server:
                continue
            if a.tools is not None and (b.tools is None or not set(b.tools) <= set(a.tools)):
                continue
            if all(k in b.where and b.where[k] == v and type(b.where[k]) is type(v) for k, v in a.where.items()):
                out.append((a, b))
    return out


class Ledger:
    """What each budget has spent, in `<state dir>/budgets.json`. Check-and-spend happens under one lock."""

    def __init__(self, root: Path):
        self.path = root / "budgets.json"

    KEEP = 30 * 86400  # per-session counts are dropped this long after their last call

    def try_spend(self, b: Budget, session_key: str | None, amount: float | None,
                  now: float | None = None) -> Spend | None:
        """Record this call against the budget if it fits; None if it doesn't (or can't be recorded)."""
        now = time.time() if now is None else now
        if b.scope == "session" and not session_key:
            return None
        key = b.name if b.scope == "user" else f"{b.name}@{session_key}"
        try:
            with trust.locked(self.path.with_suffix(".lock")) as ok:
                if not ok:
                    return None  # someone is holding the lock (or no locks on this OS): ask
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    data = data if isinstance(data, dict) else {}
                except FileNotFoundError:
                    data = {}
                except (OSError, ValueError):
                    return None  # an unreadable ledger spends nothing: the call asks
                spent = [x for x in data.get(key, []) if isinstance(x, list) and len(x) == 2]
                if b.window is not None:
                    spent = [x for x in spent if now - float(x[0]) < b.window]
                if len(spent) + 1 > b.max_calls:
                    return None
                total = None
                if b.amount is not None:
                    total = sum(float(x[1] or 0) for x in spent) + (amount or 0)
                    if b.amount.max_each is not None and (amount or 0) > b.amount.max_each:
                        return None
                    if b.amount.max_total is not None and total > b.amount.max_total:
                        return None
                spent.append([now, amount])
                data[key] = spent
                for k in [k for k, v in data.items() if "@" in k and k != key and _stale(v, now - self.KEEP)]:
                    del data[k]
                tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(self.path)
                return Spend(b, len(spent), total)
        except OSError:
            return None


def spend(budgets: Iterable[Budget], ledger: Ledger, rclass: RClass, source: str, servers: list[str], tool: str,
          args: Mapping[str, Any] | None, session_key: str | None) -> Spend | None:
    """The first matching budget with room, spent; None means the call asks as usual."""
    if rclass != RClass.R2 or source in ("fail-closed", "session", "preimage-unconfirmed"):
        return None
    ids = set(servers)
    if len(ids) != 1:
        return None
    (server,) = ids
    for b in budgets:
        if not matches(b, server, tool, args):
            continue
        # The first matching budget decides: a broader budget below must not pick up a call this one refused.
        amount = None
        if b.amount is not None:
            amount = _number(_get(args, b.amount.arg))
            if amount is None or amount < 0:
                return None
        return ledger.try_spend(b, session_key, amount)
    return None


def writable(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p.exists() and os.access(p, os.W_OK)]
