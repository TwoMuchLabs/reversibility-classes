"""Two things every file the hook trusts needs: a lock nobody can hold forever, and a source a repository can't set.

**Locks with a deadline.** The hook's state, budget ledger and decision log are shared between parallel hook
runs, so each is changed under a file lock. A lock taken with no deadline lets anything that holds it (an agent
running `flock … sleep 600`) stall the hook until Claude Code's hook timeout, and on a timeout Claude Code runs
the call under its normal permission rules, without rclass. `locked()` gives up after a short deadline and the
caller then takes the strict path: no budget, assume the session has read data, or log without the lock.

**Not from the project.** Claude Code passes the `env` of a project's own `.claude/settings.json` to hooks
(checked in a live session), so a repository can set `RCLASS_SERVERS`, `RCLASS_BUDGETS` or `RCLASS_STATE_DIR`.
A file those name that sits inside the project folder is refused; a state folder inside it is ignored.
`CLAUDE_PROJECT_DIR` itself is set by Claude Code after the settings `env` is applied, so a repository can't
move it (also checked live).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_DEADLINE = 2.0  # seconds; well inside Claude Code's hook timeout


@contextmanager
def locked(lock_path: Path, deadline: float | None = None) -> Iterator[bool]:
    """Hold an exclusive lock on `lock_path`; yields False if it couldn't be had in time (or on Windows)."""
    try:
        import fcntl
    except ImportError:  # no advisory locks: callers treat this as "not locked"
        yield False
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as f:
        end = time.monotonic() + (LOCK_DEADLINE if deadline is None else deadline)
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    yield False
                    return
                time.sleep(0.02)
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def project_root() -> Path | None:
    """The project Claude Code runs in: CLAUDE_PROJECT_DIR, which Claude Code sets for hooks. Outside a hook
    (a person running `rclass scan` from their home folder) there is no project to distrust."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    try:
        return Path(env).resolve() if env else None
    except OSError:
        return None


def inside_project(path: Path) -> bool:
    root = project_root()
    if root is None or root == Path(root.anchor):
        return False
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return True  # can't tell: treat as untrusted
    return resolved == root or root in resolved.parents
