"""The hook's decision log, kept so an auditor can use it (SPEC 10).

One JSON object per line. Each line is either a **decision** (`"event": "pre"`, written when Claude Code asks
the hook about a call) or an **outcome** (`"event": "post"`, written after the call actually ran). Joining
the two by `tool_use_id` answers "which irreversible actions did this agent take, and who approved them":

- decided `ask`, then ran: it was approved. In Claude Code's default mode that is a person (`user`) answering
  the prompt; `permission_mode` is logged because other modes may approve without one;
- decided `ask`, never ran: refused or cancelled;
- decided `deny`: blocked;
- no decision (`client`) or `allow`, then ran: ran without an rclass prompt.

Lines are **hash-chained**: each carries `prev`, the `hash` of the line before it, and its own `hash` over its
content. Editing, removing or reordering a line breaks the chain, and `rclass audit verify` reports where.
A chain on the same machine is tamper-*evident*, not tamper-*proof*: someone who can rewrite the whole file
can rebuild it, so ship the log somewhere the agent cannot reach if that matters. Arguments are never
logged, only a SHA-256 digest of them, so a reviewer holding the arguments can confirm which call it was.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
from pathlib import Path
from typing import Any, Iterable, Mapping

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def args_digest(args: Mapping[str, Any] | None) -> str | None:
    return None if args is None else hashlib.sha256(canonical(args)).hexdigest()


def who() -> dict[str, str | None]:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is a fact to log, not a reason to fail
        user = None
    try:
        host = socket.gethostname()
    except OSError:
        host = None
    return {"user": user, "host": host}


def _line_hash(entry: Mapping[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


def _last_hash(path: Path) -> str:
    """The `hash` of the file's last line, reading only its tail."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = b""
            pos = size
            while pos > 0 and chunk.count(b"\n") < 2:
                step = min(4096, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step) + chunk
            for line in reversed(chunk.splitlines()):
                if line.strip():
                    return json.loads(line).get("hash") or GENESIS
    except (OSError, ValueError):
        pass
    return GENESIS


def append(path: Path, entry: dict[str, Any]) -> None:
    """Add one line to the chain. Best effort: a full disk or a read-only folder never blocks a call."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(path.suffix + ".lock"), "a+") as lock:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            entry = dict(entry, prev=_last_hash(path))
            entry["hash"] = _line_hash(entry)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def verify(path: Path) -> tuple[int, list[tuple[int, str]]]:
    """(lines checked, problems as (line number, what)). An empty problem list means the chain is intact."""
    problems: list[tuple[int, str]] = []
    prev = GENESIS
    n = 0
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                problems.append((n, "not valid JSON"))
                prev = None
                continue
            if prev is not None and entry.get("prev") != prev:
                problems.append((n, "does not follow the line before it (a line was removed, added or reordered)"))
            if entry.get("hash") != _line_hash(entry):
                problems.append((n, "its content was changed after it was written"))
            prev = entry.get("hash")
    return n, problems


def report(entries: Iterable[Mapping[str, Any]], min_class: str = "R3") -> list[dict[str, Any]]:
    """Every decision at or above `min_class`, or that asked or blocked, with what happened next."""
    entries = list(entries)
    ran = {e.get("tool_use_id") for e in entries if e.get("event") == "post" and e.get("tool_use_id")}
    rows = []
    for e in entries:
        if e.get("event", "pre") != "pre":
            continue
        cls, decision = e.get("class") or "R4", e.get("decision")
        budget = e.get("budget") if isinstance(e.get("budget"), Mapping) else None
        if cls < min_class and decision not in ("ask", "deny") and budget is None:
            continue
        tid = e.get("tool_use_id")
        executed = tid in ran if tid else None
        if decision == "deny":
            outcome = "blocked"
        elif decision == "ask":
            mode = e.get("permission_mode")
            approver = (e.get("user") or "the user") if mode in (None, "default", "plan") else f"{mode} mode"
            outcome = (f"approved ({approver}) and ran") if executed else \
                      ("not run (refused or cancelled)" if executed is False else "unknown (no tool_use_id)")
        elif budget is not None:
            used = f"call {budget.get('calls')} of {budget.get('max_calls')}"
            outcome = (f"ran within budget '{budget.get('name')}' ({used})" if executed else
                       f"allowed within budget '{budget.get('name')}' ({used}), did not run" if executed is False
                       else f"allowed within budget '{budget.get('name')}' ({used})")
        else:
            outcome = "ran without an rclass prompt" if executed else ("did not run" if executed is False else "unknown")
        rows.append({"time": e.get("time"), "session": e.get("session"), "user": e.get("user"), "host": e.get("host"),
                     "action": f"{e.get('server')}/{e.get('tool')}", "class": cls, "source": e.get("source"),
                     "decision": decision, "outcome": outcome, "permission_mode": e.get("permission_mode"),
                     "args_sha256": e.get("args_sha256"), **({"budget": budget.get("name")} if budget else {})})
    return rows
