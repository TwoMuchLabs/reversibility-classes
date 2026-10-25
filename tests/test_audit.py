"""The decision log: hash chain, outcomes, and the report an auditor reads (rclass/audit.py)."""

import io
import json

import pytest

from rclass import audit, gate
from rclass.cli import main
from rclass.model import RClass
from rclass.registry import Registry

REG = Registry.bundled()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("RCLASS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    proj, home = tmp_path / "p", tmp_path / "h"
    proj.mkdir(), home.mkdir()
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "db": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "t.db"]}}}))
    return proj, home, tmp_path / "audit.jsonl"


def send(setup, event, **kw):
    proj, home, log = setup
    event = {"session_id": "s1", "cwd": str(proj), **event}
    out = io.StringIO()
    gate.run_hook(io.StringIO(json.dumps(event)), out, kw.get("policy", gate.GatePolicy()), lambda: REG,
                  log_path=log, home=home)
    return out.getvalue()


def pre(setup, tid, query, **kw):
    return send(setup, {"hook_event_name": "PreToolUse", "tool_use_id": tid, "tool_name": "mcp__db__write_query",
                        "tool_input": {"query": query}}, **kw)


def post(setup, tid):
    return send(setup, {"hook_event_name": "PostToolUse", "tool_use_id": tid, "tool_name": "mcp__db__write_query",
                        "tool_input": {}, "tool_response": {"ok": True}})


def test_report_answers_who_approved_what(setup):
    pre(setup, "t1", "DROP TABLE a")      # asked, then ran: approved
    assert post(setup, "t1") == ""        # PostToolUse never decides
    pre(setup, "t2", "DROP TABLE b")      # asked, never ran: refused
    pre(setup, "t3", "DROP TABLE c", policy=gate.GatePolicy(deny_from=RClass.R4))  # blocked
    rows = audit.report(audit.read(setup[2]))
    assert [r["outcome"].split(" ")[0] for r in rows] == ["approved", "not", "blocked"]
    assert rows[0]["class"] == "R4" and rows[0]["action"] == "db/write_query"
    assert rows[0]["user"] and rows[0]["host"]


def test_log_holds_a_digest_never_the_arguments(setup):
    pre(setup, "t1", "DROP TABLE customers_secret")
    text = setup[2].read_text()
    assert "customers_secret" not in text
    entry = json.loads(text.splitlines()[0])
    assert entry["args_sha256"] == audit.args_digest({"query": "DROP TABLE customers_secret"})


def test_chain_is_intact_then_detects_tampering(setup):
    for i in range(4):
        pre(setup, f"t{i}", "DROP TABLE x")
    log = setup[2]
    assert audit.verify(log) == (4, [])
    lines = log.read_text().splitlines()

    edited = json.loads(lines[1])
    edited["decision"] = "allow"
    log.write_text("\n".join([lines[0], json.dumps(edited), *lines[2:]]) + "\n")
    assert any("changed" in what for _, what in audit.verify(log)[1])

    log.write_text("\n".join([lines[0], *lines[2:]]) + "\n")              # a line removed
    assert any("does not follow" in what for _, what in audit.verify(log)[1])

    log.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n")  # reordered
    assert audit.verify(log)[1]


def test_cli_verify_and_report(setup, capsys):
    pre(setup, "t1", "DROP TABLE a")
    post(setup, "t1")
    log = str(setup[2])
    assert main(["audit", "verify", log]) == 0
    assert "chain intact" in capsys.readouterr().out
    assert main(["audit", "report", log]) == 0
    assert "approved (" in capsys.readouterr().out
    assert main(["audit", "report", log, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["decision"] == "ask"
    lines = setup[2].read_text().splitlines()
    setup[2].write_text(lines[1] + "\n")
    assert main(["audit", "verify", log]) == 1


def test_parallel_appends_keep_one_chain(tmp_path):
    import threading
    log = tmp_path / "a.jsonl"
    threads = [threading.Thread(target=audit.append, args=(log, {"event": "pre", "i": i})) for i in range(40)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    n, problems = audit.verify(log)
    assert n == 40 and problems == []


def test_hook_settings_include_post_tool_use():
    cfg = json.loads(gate.claude_hook_settings([]))["hooks"]
    assert cfg["PostToolUse"][0]["hooks"][0]["command"] == cfg["PreToolUse"][0]["hooks"][0]["command"]


def test_auto_mode_approval_is_not_credited_to_a_person(setup):
    send(setup, {"hook_event_name": "PreToolUse", "tool_use_id": "a1", "tool_name": "mcp__db__write_query",
                 "permission_mode": "auto", "tool_input": {"query": "DROP TABLE a"}})
    post(setup, "a1")
    assert audit.report(audit.read(setup[2]))[0]["outcome"] == "approved (auto mode) and ran"
