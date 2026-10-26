"""`rclass audit export`: the decision log as OpenTelemetry log records (rclass/otel.py)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from rclass import __version__, audit, otel
from rclass.cli import main


def write_log(path, n=3):
    for i in range(n):
        audit.append(path, {"event": "pre", "time": f"2026-10-26T08:00:0{i}+00:00", "session": "s1",
                            "tool_use_id": f"t{i}", "user": "alex", "host": "laptop", "permission_mode": "default",
                            "server": "db", "tool": "write_query", "class": "R4", "source": "registry",
                            "rule_index": None, "effective": "R4", "decision": "ask", "args_sha256": "ab" * 32})
    return path


def attrs(rec):
    out = {}
    for a in rec["attributes"]:
        (kind, v), = a["value"].items()
        out[a["key"]] = int(v) if kind == "intValue" else v
    return out


def export(capsys, *args):
    assert main(["audit", "export", *map(str, args)]) == 0
    cap = capsys.readouterr()
    return [json.loads(x) for x in cap.out.splitlines() if x.strip()], cap.err


def records(lines):
    return [r for d in lines for rl in d["resourceLogs"] for sl in rl["scopeLogs"] for r in sl["logRecords"]]


def test_decision_and_outcome_records(tmp_path, capsys):
    log = write_log(tmp_path / "a.jsonl", 1)
    audit.append(log, {"event": "pre", "time": "2026-10-26T08:00:05+00:00", "session": "s1", "tool_use_id": "t9",
                       "server": "db", "tool": "create_table", "class": "R2", "source": "registry", "decision": "allow",
                       "budget": {"name": "tables", "calls": 1, "max_calls": 2}})
    audit.append(log, {"event": "post", "time": "2026-10-26T08:00:06+00:00", "session": "s1", "tool_use_id": "t9",
                       "user": "alex", "host": "laptop", "tool": "mcp__db__create_table"})
    audit.append(log, {"event": "pre", "time": "2026-10-26T08:00:07+00:00", "session": "s1", "tool_use_id": "w1",
                       "server": "claude-code", "tool": "WebFetch", "class": "R4", "source": "session", "decision": "deny"})
    lines, err = export(capsys, log)
    assert "exported 4 record(s) to stdout" in err
    (d,) = lines
    res = {a["key"]: a["value"]["stringValue"] for a in d["resourceLogs"][0]["resource"]["attributes"]}
    assert res == {"service.name": "rclass", "service.version": __version__}
    r0, r1, r2, r3 = records(lines)
    a0 = attrs(r0)
    assert a0["gen_ai.operation.name"] == "execute_tool" and a0["gen_ai.tool.name"] == "mcp__db__write_query"
    assert a0["mcp.method.name"] == "tools/call" and a0["gen_ai.conversation.id"] == "s1"
    assert a0["rclass.class"] == "R4" and a0["rclass.decision"] == "ask" and a0["rclass.chain.ok"] is True
    assert a0["user.name"] == "alex" and a0["rclass.args_sha256"] == "ab" * 32
    assert "rclass.rule_index" not in a0  # nulls are left out
    assert r0["eventName"] == "rclass.decision" and r0["body"]["stringValue"] == "R4 db/write_query: ask"
    assert r0["timeUnixNano"] == str(1_793_001_600 * 10**9)  # 2026-10-26T08:00:00Z
    a1 = attrs(r1)
    assert a1["rclass.budget.name"] == "tables" and a1["rclass.budget.calls"] == 1
    a2 = attrs(r2)
    assert r2["eventName"] == "rclass.outcome" and a2["gen_ai.tool.name"] == "mcp__db__create_table"
    assert a2["gen_ai.tool.call.id"] == a1["gen_ai.tool.call.id"]  # decision and outcome join
    a3 = attrs(r3)
    assert a3["gen_ai.tool.name"] == "WebFetch" and "mcp.method.name" not in a3
    assert r3["severityText"] == "WARN"


def test_output_parses_as_otlp_logs_data(tmp_path, capsys):
    pb = pytest.importorskip("opentelemetry.proto.logs.v1.logs_pb2")
    json_format = pytest.importorskip("google.protobuf.json_format")
    lines, _ = export(capsys, write_log(tmp_path / "a.jsonl"))
    msg = json_format.ParseDict(lines[0], pb.LogsData())  # unknown fields raise
    assert len(msg.resource_logs[0].scope_logs[0].log_records) == 3
    assert msg.resource_logs[0].scope_logs[0].log_records[0].event_name == "rclass.decision"


def test_chain_problems_are_flagged(tmp_path, capsys):
    log = write_log(tmp_path / "a.jsonl", 4)
    rows = log.read_text().splitlines()
    edited = json.loads(rows[1])
    edited["decision"] = "allow"
    rows[1] = json.dumps(edited)
    del rows[2]  # removed: the line after the gap fails too
    log.write_text("\n".join(rows) + "\n")
    lines, err = export(capsys, log)
    assert [attrs(r)["rclass.chain.ok"] for r in records(lines)] == [True, False, False]
    assert "2 failed the hash-chain check" in err
    assert records(lines)[1]["severityText"] == "WARN"


def test_cursor_sends_only_new_lines(tmp_path, capsys):
    log, cur = write_log(tmp_path / "a.jsonl", 2), tmp_path / "cursor"
    out = tmp_path / "otlp.jsonl"
    assert main(["audit", "export", str(log), "--out", str(out), "--cursor", str(cur)]) == 0
    assert cur.read_text().strip() == audit.read(log)[-1]["hash"]
    assert main(["audit", "export", str(log), "--out", str(out), "--cursor", str(cur)]) == 0
    assert "exported 0 record(s)" in capsys.readouterr().err
    write_log(log, 1)
    assert main(["audit", "export", str(log), "--out", str(out), "--cursor", str(cur)]) == 0
    sent = records([json.loads(x) for x in out.read_text().splitlines()])
    assert len(sent) == 3


def test_unknown_cursor_exports_everything(tmp_path, capsys):
    log, cur = write_log(tmp_path / "a.jsonl", 2), tmp_path / "cursor"
    cur.write_text("f" * 64)
    lines, _ = export(capsys, log, "--cursor", cur)
    assert len(records(lines)) == 2


def test_batches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(otel, "BATCH", 2)
    lines, _ = export(capsys, write_log(tmp_path / "a.jsonl", 5))
    assert [len(records([d])) for d in lines] == [2, 2, 1]


class Collector:
    def __init__(self, status=200, body=b"{}"):
        self.got, self.status, self.body = [], status, body
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers["Content-Length"])
                outer.got.append((self.path, self.headers, json.loads(self.rfile.read(n))))
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


def test_post_to_collector_with_env_headers(tmp_path, monkeypatch, capsys):
    c = Collector()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", c.url)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Bearer%20abc,x-team=platform")
    log, cur = write_log(tmp_path / "a.jsonl", 2), tmp_path / "cursor"
    assert main(["audit", "export", str(log), "--cursor", str(cur)]) == 0
    (path, headers, body), = c.got
    assert path == "/v1/logs" and headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer abc" and headers["x-team"] == "platform"
    assert len(records([body])) == 2
    assert f"to {c.url}/v1/logs" in capsys.readouterr().err
    assert cur.read_text().strip()


@pytest.mark.parametrize("status,body,err", [
    (500, b"{}", "HTTP 500"),
    (200, b'{"partialSuccess": {"rejectedLogRecords": "1", "errorMessage": "bad"}}', "rejected 1 record"),
])
def test_failed_export_keeps_the_cursor(tmp_path, capsys, status, body, err):
    c = Collector(status, body)
    log, cur = write_log(tmp_path / "a.jsonl", 2), tmp_path / "cursor"
    assert main(["audit", "export", str(log), "--endpoint", c.url, "--cursor", str(cur)]) == 2
    assert err in capsys.readouterr().err
    assert not cur.exists()


def test_unreachable_collector(tmp_path, capsys):
    assert main(["audit", "export", str(write_log(tmp_path / "a.jsonl")), "--endpoint", "http://127.0.0.1:9",
                 "--timeout", "2"]) == 2
    assert "could not reach" in capsys.readouterr().err


def test_endpoint_rules(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    assert otel.endpoint_from_env(None) is None
    assert otel.endpoint_from_env("https://c.example:4318") == "https://c.example:4318/v1/logs"
    assert otel.endpoint_from_env("https://c.example/v1/logs") == "https://c.example/v1/logs"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://c.example/custom")
    assert otel.endpoint_from_env(None) == "https://c.example/custom"  # used as given, per the OTLP spec
    with pytest.raises(otel.ExportError, match="http"):
        otel.post("file:///etc/passwd", {}, {})
    with pytest.raises(otel.ExportError, match="NAME=VALUE"):
        otel.headers_from_env(["nope"])


def test_version_is_one_source_of_truth():
    import re
    text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == __version__
