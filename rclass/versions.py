"""Which release of a server a config runs, and whether a record's rules were checked against it (SPEC 5.1)."""

from __future__ import annotations

import re

_SEMVER = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse(version: str | None) -> tuple[int, int, int] | None:
    """`v1.2.3`, `1.2`, `2` -> a comparable tuple; anything else (a digest, `latest`, a branch) -> None."""
    if not version:
        return None
    m = _SEMVER.match(version.strip())
    if not m:
        return None
    return tuple(int(g) if g else 0 for g in m.groups())  # type: ignore[return-value]


def pinned_version(target: str | None) -> tuple[str | None, bool]:
    """(version, pinned) for what a config runs: an npm or PyPI package, or a container image.

    Unpinned (`pkg`, `pkg@latest`, `image`, `image:latest`) runs the newest release, so it is not "older
    than" anything a record was checked against. A digest pin (`image@sha256:…`) is pinned to a version
    that cannot be read from the config.
    """
    if not target:
        return None, False
    t = target.strip()
    if "==" in t:
        v = t.split("==", 1)[1].strip()
        return (v or None), bool(v)
    if "@sha256:" in t:
        return None, True
    name = t[1:] if t.startswith("@") else t
    if "@" in name:
        v = name.rsplit("@", 1)[1]
        return (None, False) if v in ("", "latest") else (v, True)
    last = t.rsplit("/", 1)[-1]
    if ":" in last:
        v = last.split(":", 1)[1]
        return (None, False) if v in ("", "latest") else (v, True)
    return None, False


def rules_apply(min_version: str | None, version: str | None, pinned: bool) -> bool:
    """Whether `when` rules that lower a class may be used. They were checked against `min_version` or later;
    a deployment pinned to an older release, or to one whose version cannot be read, gets the base class."""
    if not min_version or not pinned:
        return True
    have, need = parse(version), parse(min_version)
    if have is None or need is None:
        return False
    return have >= need
