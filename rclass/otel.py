"""Ship the decision log to OpenTelemetry: OTLP/JSON log records, to a file or a collector.

`rclass audit export` turns each line of the hook's decision log (rclass/audit.py) into one OTLP log record,
so the log reaches the SIEM or observability stack a platform team already runs, somewhere the agent can't
rewrite it.

- **To a file** (`--out FILE`, default stdout): JSON Lines of OTLP `LogsData`, the format of the
  OpenTelemetry file exporter, which the Collector's `otlp_json_file` receiver reads.
- **To a collector** (`--endpoint URL`): OTLP/HTTP with JSON encoding, POSTed to `/v1/logs`. The standard
  variables `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT` and
  `OTEL_EXPORTER_OTLP_HEADERS` (or `..._LOGS_HEADERS`) work too, so credentials needn't go on the command line.
- **Incrementally** (`--cursor FILE`): the hash of the last exported line is kept in the file and the next run
  starts after it. The cursor moves only after the collector accepted the batch, so a failed run resends.

Attributes follow the OpenTelemetry GenAI and MCP semantic conventions where they have a name for the thing
(`gen_ai.operation.name` = `execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.conversation.id`,
`mcp.method.name` = `tools/call`, `user.name`, `host.name`); those conventions are still in Development, so
names may change. Everything specific to this project is under `rclass.*`. Arguments are never exported, only
their SHA-256 digest, as in the log itself. Each record says whether its line passed the hash-chain check
(`rclass.chain.ok`), so a gap or an edit is visible downstream.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from . import audit

SEVERITY = {"INFO": 9, "WARN": 13}
BATCH = 512


class ExportError(RuntimeError):
    pass


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}  # OTLP/JSON encodes 64-bit integers as strings
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _attrs(d: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in d.items() if v is not None]


def _nanos(iso: Any) -> str | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return str(int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000)


def tool_name(e: Mapping[str, Any]) -> str | None:
    """The tool name as Claude Code wrote it, for both decision and outcome lines, so they join."""
    if e.get("event") == "post":
        return e.get("tool")
    server, tool = e.get("server"), e.get("tool")
    if server in (None, "claude-code"):
        return tool
    return f"mcp__{server}__{tool}"


def record(e: Mapping[str, Any], chain_ok: bool) -> dict[str, Any]:
    pre = e.get("event", "pre") == "pre"
    name = tool_name(e)
    is_mcp = bool(name and str(name).startswith("mcp__"))
    budget = e.get("budget") if isinstance(e.get("budget"), Mapping) else {}
    attrs = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.call.id": e.get("tool_use_id"),
        "gen_ai.conversation.id": e.get("session"),
        "mcp.method.name": "tools/call" if is_mcp else None,
        "user.name": e.get("user"),
        "host.name": e.get("host"),
        "rclass.event": "decision" if pre else "outcome",
        "rclass.server": e.get("server") if pre else None,
        "rclass.tool": e.get("tool") if pre else None,
        "rclass.class": e.get("class"),
        "rclass.source": e.get("source"),
        "rclass.rule_index": e.get("rule_index"),
        "rclass.effective_class": e.get("effective"),
        "rclass.decision": e.get("decision") if pre else None,
        "rclass.permission_mode": e.get("permission_mode"),
        "rclass.args_sha256": e.get("args_sha256"),
        "rclass.override.rationale": e.get("rationale"),
        "rclass.budget.name": budget.get("name"),
        "rclass.budget.calls": budget.get("calls"),
        "rclass.budget.max_calls": budget.get("max_calls"),
        "rclass.chain.hash": e.get("hash"),
        "rclass.chain.ok": chain_ok,
    }
    if pre:
        body = f"{e.get('class') or 'R4'} {e.get('server')}/{e.get('tool')}: {e.get('decision')}"
        warn = e.get("decision") == "deny"
    else:
        body = f"ran {name}"
        warn = False
    out: dict[str, Any] = {
        "severityNumber": SEVERITY["WARN" if warn or not chain_ok else "INFO"],
        "severityText": "WARN" if warn or not chain_ok else "INFO",
        "eventName": "rclass.decision" if pre else "rclass.outcome",
        "body": {"stringValue": body},
        "attributes": _attrs(attrs),
    }
    t = _nanos(e.get("time"))
    if t:
        out["timeUnixNano"] = t
    return out


def checked(path: Path) -> Iterator[tuple[dict[str, Any], bool]]:
    """Each line with whether it passes the chain check, the same test as `rclass audit verify`."""
    prev: str | None = audit.GENESIS
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                prev = None
                continue
            if not isinstance(e, dict):
                prev = None
                continue
            # The line after an unreadable one can't be checked against it, so it is not ok either.
            ok = prev is not None and e.get("prev") == prev and e.get("hash") == audit._line_hash(e)
            prev = e.get("hash")
            yield e, ok


def after(lines: Iterable[tuple[dict[str, Any], bool]], cursor: str | None) -> list[tuple[dict[str, Any], bool]]:
    """Lines after the one whose hash is `cursor`. A cursor not in the log (rotated, or rewritten) exports all."""
    lines = list(lines)
    if not cursor:
        return lines
    for i, (e, _) in enumerate(lines):
        if e.get("hash") == cursor:
            return lines[i + 1:]
    return lines


def logs_data(records: list[dict[str, Any]], version: str) -> dict[str, Any]:
    return {"resourceLogs": [{
        "resource": {"attributes": _attrs({"service.name": "rclass", "service.version": version})},
        "scopeLogs": [{"scope": {"name": "rclass.audit", "version": version}, "logRecords": records}],
    }]}


def batches(lines: list[tuple[dict[str, Any], bool]], version: str) -> Iterator[tuple[dict[str, Any], str | None]]:
    """(LogsData, hash of its last line) per batch."""
    for i in range(0, len(lines), BATCH):
        chunk = lines[i:i + BATCH]
        yield logs_data([record(e, ok) for e, ok in chunk], version), chunk[-1][0].get("hash")


def endpoint_from_env(explicit: str | None) -> str | None:
    """`--endpoint`, else OTEL_EXPORTER_OTLP_LOGS_ENDPOINT (used as given), else OTEL_EXPORTER_OTLP_ENDPOINT
    with /v1/logs added, as the OTLP exporter specification says."""
    if explicit:
        return explicit if explicit.rstrip("/").endswith("/v1/logs") else explicit.rstrip("/") + "/v1/logs"
    logs = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    if logs:
        return logs
    base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    return base.rstrip("/") + "/v1/logs" if base else None


def headers_from_env(extra: Iterable[str] = ()) -> dict[str, str]:
    """OTEL_EXPORTER_OTLP_HEADERS / _LOGS_HEADERS (`k=v,k2=v2`, values URL-encoded), then `--header K=V`."""
    from urllib.parse import unquote
    out: dict[str, str] = {}
    for var in ("OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_LOGS_HEADERS"):
        for part in (os.environ.get(var) or "").split(","):
            k, sep, v = part.partition("=")
            if sep and k.strip():
                out[k.strip()] = unquote(v.strip())
    for h in extra:
        k, sep, v = h.partition("=")
        if not sep or not k.strip():
            raise ExportError(f"--header must look like NAME=VALUE: {h!r}")
        out[k.strip()] = v.strip()
    return out


def post(url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float = 10.0) -> None:
    if not url.lower().startswith(("http://", "https://")):
        raise ExportError(f"endpoint must be an http(s) URL: {url}")
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
            body = resp.read(65536)
            if not 200 <= resp.status < 300:
                raise ExportError(f"collector answered HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise ExportError(f"collector answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ExportError(f"could not reach {url} ({exc.__class__.__name__})") from None
    try:
        partial = json.loads(body or b"{}").get("partialSuccess") or {}
    except (ValueError, AttributeError):
        partial = {}
    if int(partial.get("rejectedLogRecords") or 0):
        raise ExportError(f"collector rejected {partial['rejectedLogRecords']} record(s): "
                          f"{partial.get('errorMessage') or 'no reason given'}")


def read_cursor(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None


def write_cursor(path: Path | None, value: str | None) -> None:
    if path is None or not value:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    tmp.replace(path)
