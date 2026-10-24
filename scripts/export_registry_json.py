#!/usr/bin/env python3
"""Export the registry as one compact JSON file for the project website.

Usage: python scripts/export_registry_json.py OUT.json

Every record is validated first, so the site never shows a record CI would reject.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rclass.registry import read_record_file, validate_record  # noqa: E402


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(out: Path) -> None:
    servers, tools = [], []
    for path in sorted((ROOT / "registry").glob("*.y*ml")):
        record = read_record_file(path)
        validate_record(record, str(path))
        s = record["server"]
        servers.append({
            "id": s["id"],
            "name": s["name"],
            "source": s.get("source"),
            "version": s.get("version"),
            "status": s.get("status"),
            "file": f"registry/{path.name}",
        })
        for t in record["tools"]:
            entry = {"s": s["id"], "n": t["name"], "c": t["class"], "r": t["rationale"], "v": t.get("review", "draft")}
            if t.get("disclosure"):
                entry["d"] = True
            if t.get("when"):
                entry["w"] = t["when"]
            tools.append(entry)
    doc = {
        "spec_version": record.get("spec_version", "0.1"),
        "commit": git_commit(),
        "servers": servers,
        "tools": tools,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(servers)} servers, {len(tools)} tools")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
