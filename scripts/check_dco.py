#!/usr/bin/env python3
"""Fail if any commit in BASE..HEAD lacks a DCO sign-off matching its author.

Usage: python scripts/check_dco.py BASE HEAD
Merge commits and GitHub bot accounts are skipped. The sign-off must name the commit author's email.
"""

from __future__ import annotations

import re
import subprocess
import sys

SIGNOFF = re.compile(r"^Signed-off-by: .+ <([^>]+)>\s*$", re.M | re.I)


def commits(base: str, head: str) -> list[tuple[str, str, str]]:
    out = subprocess.check_output(
        ["git", "log", "--no-merges", "--format=%H%x00%ae%x00%B%x1e", f"{base}..{head}"], text=True)
    rows = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if chunk:
            sha, email, body = chunk.split("\x00", 2)
            rows.append((sha, email, body))
    return rows


BOT_EMAIL = re.compile(r"^\d+\+[\w.-]+\[bot\]@users\.noreply\.github\.com$|^[\w.-]+\[bot\]@users\.noreply\.github\.com$", re.I)


def missing(rows: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """Commits without a matching sign-off. GitHub's bot accounts (Dependabot and the like) cannot
    certify the DCO and are exempt; their changes are reviewed by the maintainer who merges them."""
    bad = []
    for sha, email, body in rows:
        if BOT_EMAIL.match(email):
            continue
        signers = {e.lower() for e in SIGNOFF.findall(body)}
        if email.lower() not in signers:
            bad.append((sha, email))
    return bad


def main(base: str, head: str) -> int:
    rows = commits(base, head)
    bad = missing(rows)
    for sha, email in bad:
        print(f"::error::commit {sha[:7]} has no 'Signed-off-by: ... <{email}>' line")
    if bad:
        print("\nSign off with `git commit -s`. To fix existing commits: git rebase --signoff "
              "<base-branch> && git push --force-with-lease. See CONTRIBUTING.md.")
        return 1
    print(f"all {len(rows)} commits signed off")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
