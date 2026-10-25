#!/usr/bin/env python3
"""Publish every release CHANGELOG.md describes that has no tag yet.

Run by .github/workflows/release.yml on each push to main; safe to run again (existing tags are skipped).

Which commit a version is tagged on:
- the version in pyproject.toml: the commit being pushed (HEAD);
- an older version: the first-parent commit titled "Release vX.Y.Z"; without one it is skipped, never guessed.

Usage: python scripts/release.py [--dry-run]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^## (v\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})\s*$", re.M)


@dataclass
class Release:
    tag: str
    date: str
    notes: str

    @property
    def title(self) -> str:
        m = re.match(r"\*\*(.+?)\*\*", self.notes)
        tagline = m.group(1).rstrip(".") if m else ""
        return f"{self.tag} — {tagline}" if tagline else self.tag


def parse_changelog(text: str) -> list[Release]:
    """Releases in the order they appear (newest first)."""
    heads = list(HEADING.finditer(text))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.end():end]
        nxt = re.search(r"^## ", body, re.M)  # stop at any other heading, e.g. "## Unreleased"
        out.append(Release(h.group(1), h.group(2), (body[:nxt.start()] if nxt else body).strip() + "\n"))
    return out


def project_version(pyproject: str) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    if not m:
        raise SystemExit("no version in pyproject.toml")
    return "v" + m.group(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def target_for(tag: str, current: str) -> str | None:
    if tag == current:
        return git("rev-parse", "HEAD")
    sha = git("log", "--first-parent", "--format=%H", "-1", "--fixed-strings", f"--grep=Release {tag}")
    return sha or None


def main(dry_run: bool) -> int:
    releases = parse_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    current = project_version((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if current not in {r.tag for r in releases}:
        print(f"{current} has no dated CHANGELOG section yet; nothing to release for it")
    tags = set(git("tag", "--list").split())
    for r in reversed(releases):  # oldest first, so the newest ends up "latest"
        if r.tag in tags:
            continue
        sha = target_for(r.tag, current)
        if not sha:
            print(f"skip {r.tag}: no commit titled 'Release {r.tag}' and it is not the current version")
            continue
        print(f"release {r.tag} at {sha[:7]}: {r.title}")
        if dry_run:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(r.notes)
        subprocess.check_call(["gh", "release", "create", r.tag, "--target", sha, "--title", r.title,
                               "--notes-file", f.name, f"--latest={'true' if r.tag == current else 'false'}"])
        if r.tag == current and os.environ.get("GITHUB_OUTPUT"):
            # Tells the build job which release to attach the wheel, sdist and provenance to.
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
                out.write(f"released={r.tag}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
