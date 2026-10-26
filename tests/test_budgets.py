"""R2 budgets (rclass/budgets.py): compensable calls run unprompted up to a limit; nothing else ever does."""

import io
import json
import threading

import pytest

from rclass import audit, budgets, gate
from rclass.cli import main
from rclass.model import RClass
from rclass.registry import Registry

from test_gate import CONFIG, REG, hook


@pytest.fixture
def project(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    proj.mkdir(), home.mkdir()
    (proj / ".mcp.json").write_text(json.dumps(CONFIG))
    return proj, home


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RCLASS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def policy(text, **kw):
    return gate.GatePolicy(budgets=budgets.parse(__import__("yaml").safe_load(text), "<test>"), **kw)


TABLES = """
budgets:
  - name: tables
    server: sqlite
    tools: [create_table]
    max_calls: 2
"""


def test_r2_within_budget_is_allowed_then_asks(project):
    p = policy(TABLES)
    for n in (1, 2):
        out = hook(project, "mcp__db__create_table", {"query": "CREATE TABLE t (x)"}, p)
        assert out["permissionDecision"] == "allow"
        assert f"within budget 'tables': call {n} of 2 this session" in out["permissionDecisionReason"]
    assert hook(project, "mcp__db__create_table", {"query": "CREATE TABLE u (x)"}, p)["permissionDecision"] == "ask"


def test_session_budgets_are_per_session(project):
    p = policy(TABLES.replace("max_calls: 2", "max_calls: 1"))
    assert hook(project, "mcp__db__create_table", {}, p, session="a")["permissionDecision"] == "allow"
    assert hook(project, "mcp__db__create_table", {}, p, session="a")["permissionDecision"] == "ask"
    assert hook(project, "mcp__db__create_table", {}, p, session="b")["permissionDecision"] == "allow"


def test_user_budgets_span_sessions(project):
    p = policy("""
budgets:
  - {name: t, server: sqlite, tools: [create_table], max_calls: 1, window: 1d, scope: user}
""")
    assert hook(project, "mcp__db__create_table", {}, p, session="a")["permissionDecision"] == "allow"
    assert hook(project, "mcp__db__create_table", {}, p, session="b")["permissionDecision"] == "ask"


def test_window_expires(tmp_path):
    b = budgets.parse({"budgets": [{"name": "t", "server": "sqlite", "max_calls": 1, "window": "1h"}]}, "<t>")[0]
    led = budgets.Ledger(tmp_path)
    assert led.try_spend(b, "s", None, now=1000.0)
    assert led.try_spend(b, "s", None, now=1000.0 + 3599) is None
    assert led.try_spend(b, "s", None, now=1000.0 + 3600)


@pytest.mark.parametrize("tool,args", [
    ("mcp__db__write_query", {"query": "DROP TABLE t"}),   # R4
    ("mcp__mystery__anything", {}),                        # unrecognised
    ("mcp__spoof__write_file", {}),                        # spoofed launcher
])
def test_never_r3_r4_or_unrecognised(project, tool, args):
    p = policy("""
budgets:
  - {name: all-sqlite, server: sqlite, max_calls: 100}
  - {name: all-fs, server: filesystem, max_calls: 100}
""")
    assert hook(project, tool, args, p)["permissionDecision"] == "ask"


def test_tools_default_to_every_r2_tool_of_the_server(project):
    p = policy("budgets:\n  - {name: s, server: sqlite, max_calls: 5}\n")
    assert hook(project, "mcp__db__append_insight", {"insight": "x"}, p)["permissionDecision"] == "allow"
    assert hook(project, "mcp__db__write_query", {"query": "DELETE FROM t"}, p)["permissionDecision"] == "ask"


def test_budget_is_keyed_by_record_not_config_name(project):
    p = policy("budgets:\n  - {name: s, server: db, max_calls: 5}\n")  # 'db' is a config name, not a record
    assert hook(project, "mcp__db__create_table", {}, p)["permissionDecision"] == "ask"


def test_session_guard_raise_is_never_budgeted(project, tmp_path):
    # An egress-flagged R2 tool raised to R4 by the guard must ask, even with a budget for that server.
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    (reg_dir / "poster.yaml").write_text("""
spec_version: '0.3'
server: {id: poster, name: Poster, source: https://example.com/poster, evidence: [https://example.com/poster],
  version: '1', packages: [poster-mcp]}
tools:
- {name: post, class: R2, egress: true, rationale: Posts a draft to a destination the caller chooses.}
- {name: list, class: R0, rationale: Lists drafts.}
""")
    reg = Registry.bundled()
    for rec in Registry.from_paths([reg_dir]).records:
        reg.add(rec, reg_dir)
    proj, home = project
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"p": {"command": "npx", "args": ["-y", "poster-mcp"]}}}))
    p = policy("budgets:\n  - {name: posts, server: poster, max_calls: 5}\n")

    def call(tool, args):
        event = json.dumps({"session_id": "g", "cwd": str(proj), "hook_event_name": "PreToolUse",
                            "tool_name": tool, "tool_input": args})
        out = io.StringIO()
        gate.run_hook(io.StringIO(event), out, p, lambda: reg, home=home)
        return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue().strip() else None

    assert call("mcp__p__post", {"url": "https://a.example"})["permissionDecision"] == "allow"  # nothing read yet
    call("mcp__p__list", {})
    out = call("mcp__p__post", {"url": "https://a.example"})
    assert out["permissionDecision"] == "ask" and "SPEC 9" in out["permissionDecisionReason"]


def test_deny_from_r2_wins_over_a_budget(project):
    p = policy(TABLES, deny_from=RClass.R2)
    assert hook(project, "mcp__db__create_table", {}, p)["permissionDecision"] == "deny"


AMOUNTS = """
budgets:
  - name: inserts
    server: sqlite
    tools: [append_insight]
    max_calls: 10
    where: {topic: sales}
    amount: {arg: meta.cost, max_each: 50, max_total: 80}
"""


@pytest.mark.parametrize("args", [
    {"topic": "sales"},                                        # no amount
    {"topic": "sales", "meta": {"cost": "lots"}},              # not a number
    {"topic": "sales", "meta": {"cost": -1}},                  # negative
    {"topic": "sales", "meta": {"cost": True}},                # bool is not a number
    {"topic": "sales", "meta": {"cost": 51}},                  # over max_each
    {"topic": "hr", "meta": {"cost": 1}},                      # `where` doesn't match
])
def test_amount_must_be_present_and_within_limits(project, args):
    assert hook(project, "mcp__db__append_insight", args, policy(AMOUNTS))["permissionDecision"] == "ask"


def test_amount_total(project):
    p = policy(AMOUNTS)

    def call(c):
        return hook(project, "mcp__db__append_insight", {"topic": "sales", "meta": {"cost": c}}, p)
    assert call(50)["permissionDecision"] == "allow"
    out = call(30)
    assert out["permissionDecision"] == "allow" and "meta.cost 80 of 80" in out["permissionDecisionReason"]
    assert call(1)["permissionDecision"] == "ask"


def test_refused_call_spends_nothing(project):
    p = policy(AMOUNTS)
    assert hook(project, "mcp__db__append_insight", {"topic": "sales", "meta": {"cost": 60}}, p)["permissionDecision"] == "ask"
    assert hook(project, "mcp__db__append_insight", {"topic": "sales", "meta": {"cost": 50}}, p)["permissionDecision"] == "allow"


def test_parallel_calls_cannot_overspend(tmp_path):
    b = budgets.parse({"budgets": [{"name": "t", "server": "sqlite", "max_calls": 5, "window": "1d",
                                    "scope": "user"}]}, "<t>")[0]
    led = budgets.Ledger(tmp_path)
    got = []
    threads = [threading.Thread(target=lambda: got.append(led.try_spend(b, None, None))) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for g in got if g) == 5


def test_unreadable_ledger_asks(project, tmp_path):
    (tmp_path / "state" / "rclass").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "rclass" / "budgets.json").write_text("{not json")
    assert hook(project, "mcp__db__create_table", {}, policy(TABLES))["permissionDecision"] == "ask"


def test_audit_log_and_report_show_the_budget(project, tmp_path):
    log = tmp_path / "audit.jsonl"
    p = policy(TABLES)
    proj, home = project
    for i in (1, 2):
        ev = {"session_id": "s", "cwd": str(proj), "hook_event_name": "PreToolUse", "tool_use_id": f"t{i}",
              "tool_name": "mcp__db__create_table", "tool_input": {}}
        gate.run_hook(io.StringIO(json.dumps(ev)), io.StringIO(), p, lambda: REG, log_path=log, home=home)
    ev["hook_event_name"] = "PostToolUse"
    gate.run_hook(io.StringIO(json.dumps(ev)), io.StringIO(), p, lambda: REG, log_path=log, home=home)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert lines[0]["decision"] == "allow" and lines[0]["budget"] == {"name": "tables", "calls": 1, "max_calls": 2}
    assert audit.verify(log)[0]
    rows = audit.report(lines)  # default min class R3, but budgeted R2 calls are still reported
    assert [r["outcome"] for r in rows] == ["allowed within budget 'tables' (call 1 of 2), did not run",
                                            "ran within budget 'tables' (call 2 of 2)"]


@pytest.mark.parametrize("doc,err", [
    ({"budgets": [{"server": "sqlite", "max_calls": 1}]}, "needs `name`"),
    ({"budgets": [{"name": "a", "max_calls": 1}]}, "needs `server`"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": -1}]}, "max_calls"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": True}]}, "max_calls"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1, "window": "1w"}]}, "window"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1, "window": "0h"}]}, "window"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1, "scope": "user"}]}, "needs a `window`"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1, "amount": {"arg": "x"}}]}, "max_each"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1, "amount": {"arg": "x", "max_each": "5"}}]}, "number"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1, "class": "R3"}]}, "unknown field"),
    ({"budgets": [{"name": "a", "server": "s", "max_calls": 1}, {"name": "a", "server": "s", "max_calls": 1}]}, "two budgets"),
    ({"budget": []}, "`budgets` list"),
])
def test_parse_errors(doc, err):
    with pytest.raises(budgets.BudgetError, match=err):
        budgets.parse(doc, "<t>")


def test_check_against_registry():
    with pytest.raises(budgets.BudgetError, match="doesn't have"):
        budgets.check(budgets.parse({"budgets": [{"name": "a", "server": "nope", "max_calls": 1}]}, "<t>"), REG)
    with pytest.raises(budgets.BudgetError, match="tool 'nope'"):
        budgets.check(budgets.parse({"budgets": [{"name": "a", "server": "sqlite", "tools": ["nope"],
                                                  "max_calls": 1}]}, "<t>"), REG)


def test_cli_check_and_hook_misconfiguration(tmp_path, capsys, monkeypatch):
    f = tmp_path / "b.yaml"
    f.write_text(TABLES)
    assert main(["budgets", "check", str(f)]) == 0
    out = capsys.readouterr()
    assert "tables: sqlite (create_table), 2 calls per session" in out.out
    assert "warning: you can edit" in out.err
    bad = tmp_path / "bad.yaml"
    bad.write_text("budgets: [{name: a, server: sqlite}]")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "mcp__db__create_table"})))
    assert main(["hook", "claude-code", "--budgets", str(bad)]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_generated_hook_settings_carry_budgets_as_absolute_paths(tmp_path, capsys, monkeypatch):
    f = tmp_path / "b.yaml"
    f.write_text(TABLES)
    monkeypatch.chdir(tmp_path)
    assert main(["policy", "--format", "claude-hook", "--budgets", "b.yaml"]) == 0
    cmd = json.loads(capsys.readouterr().out)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert f"--budgets {f.resolve()}" in cmd


def test_env_default_path(tmp_path, monkeypatch):
    f = tmp_path / "b.yaml"
    f.write_text(TABLES)
    monkeypatch.setenv("RCLASS_BUDGETS", str(f))
    assert [b.name for b in budgets.load(budgets.default_paths())] == ["tables"]
