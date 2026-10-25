#!/usr/bin/env python3
"""Weekly drift check: does the registry still describe the servers people install today?

Starts the latest release of each server in scripts/drift_targets.json with `rclass scan`'s client,
and compares its tools with the registry record. Writes a Markdown report.

    python scripts/check_drift.py report.md        # exit 1 if any server offers unclassified tools, or could not be checked

New tools are the actionable part: until someone classifies them, every gateway using the registry
treats them as R4. Tools in the record that the server does not list are reported separately: some
servers only expose tools you switch on, so absence is not always removal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rclass import Registry  # noqa: E402
from rclass import scan as sc  # noqa: E402


def main(out: Path) -> int:
    targets = json.loads((ROOT / "scripts" / "drift_targets.json").read_text())["servers"]
    registry = Registry.bundled()
    rows, new_tools, missing, failures = [], {}, {}, []
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(["git", "init", "-q", work], check=True)  # the Git server needs a repository
        for sid, t in targets.items():
            cfg = sc.ServerConfig(
                key=sid, source="drift_targets.json", command=t["command"],
                args=[a.replace("{work}", work) for a in t.get("args", [])],
                env={k: v.replace("{work}", work) for k, v in t.get("env", {}).items()},
            )
            rep = sc.scan_server(cfg, registry, timeout=300)
            if rep.status != "scanned":
                failures.append((sid, rep.message))
                rows.append(f"| {sid} | could not start: {rep.message} | | |")
                continue
            if rep.record != sid:
                failures.append((sid, f"recognised as {rep.record!r}, expected {sid!r}"))
            version = rep.server_info.get("version", "?")
            if rep.unclassified:
                new_tools[sid] = rep.unclassified
            rows.append(f"| {sid} | {version} | {len(rep.unclassified) or '·'} | {len(rep.not_offered) or '·'} |")
            if rep.not_offered:
                missing[sid] = rep.not_offered

    lines = ["# Registry drift check", "",
             "| Server | Latest version | New tools (unclassified, R4 until classified) | In record, not listed |",
             "|---|---|---|---|", *rows, ""]
    for sid, tools in new_tools.items():
        lines += [f"## {sid}: {len(tools)} new tools to classify", "", *[f"- [ ] `{n}`" for n in tools], ""]
    if missing:
        lines += ["## In the record but not listed", "",
                  "Not always a removal: some servers only expose tools you switch on (Sentry reaches most of its "
                  "tools through `execute_sentry_tool`). Check before deleting anything from a record.", ""]
        lines += [f"- **{sid}** ({len(names)}): " + ", ".join(f"`{n}`" for n in names[:25])
                  + (f", +{len(names) - 25} more" if len(names) > 25 else "") for sid, names in missing.items()]
        lines.append("")
    if failures:
        lines += ["## Could not check", "", *[f"- **{s}**: {m}" for s, m in failures], ""]
    if not new_tools and not failures:
        lines += ["No new tools to classify.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out.read_text())
    # A server that could not be checked is drift we cannot see: report it the same way, never as a pass.
    return 1 if (new_tools or failures) else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "drift-report.md")))
