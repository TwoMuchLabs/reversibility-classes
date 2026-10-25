"""The Claude Code hook and the static policy exports (rclass/gate.py)."""

import io
import json

import pytest

from rclass import gate
from rclass.cli import main
from rclass.model import RClass
from rclass.registry import Registry

REG = Registry.bundled()

CONFIG = {"mcpServers": {
    "db": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "test.db"]},
    "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    "my.files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    "reports__v2": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "r.db"]},
    "mystery": {"command": "node", "args": ["./server.js"]},
    "spoof": {"command": "npx", "args": ["-y", "-p", "@modelcontextprotocol/server-filesystem", "evil"]},
}}


@pytest.fixture
def project(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    proj.mkdir(), home.mkdir()
    (proj / ".mcp.json").write_text(json.dumps(CONFIG))
    return proj, home


def hook(project, tool_name, tool_input=None, policy=None, mapping=None, log=None, raw=None):
    proj, home = project
    event = raw if raw is not None else json.dumps(
        {"session_id": "s", "cwd": str(proj), "hook_event_name": "PreToolUse",
         "tool_name": tool_name, "tool_input": tool_input or {}})
    out = io.StringIO()
    code = gate.run_hook(io.StringIO(event), out, policy or gate.GatePolicy(), lambda: REG, mapping, log, home=home)
    assert code == 0  # never a blocking exit, never a crash
    text = out.getvalue().strip()
    return json.loads(text)["hookSpecificOutput"] if text else None


def test_select_gets_no_decision_by_default(project):
    assert hook(project, "mcp__db__read_query", {"query": "SELECT 1"}) is None


def test_same_tool_is_judged_per_call(project):
    """write_query is R4 for DROP TABLE: the hook sees the arguments."""
    d = hook(project, "mcp__db__write_query", {"query": "DROP TABLE users"})
    assert d["permissionDecision"] == "ask" and d["hookEventName"] == "PreToolUse"
    assert d["permissionDecisionReason"].startswith("rclass: R4 Irreversible")


def test_allow_up_to_and_deny_from(project):
    allow = hook(project, "mcp__fs__read_text_file", {"path": "a"}, gate.GatePolicy(allow_up_to=RClass.R1))
    assert allow["permissionDecision"] == "allow"
    deny = hook(project, "mcp__fs__write_file", {"path": "a", "content": "x"}, gate.GatePolicy(deny_from=RClass.R4))
    assert deny["permissionDecision"] == "deny" and "Blocked by policy" in deny["permissionDecisionReason"]


@pytest.mark.parametrize("tool_name", ["mcp__mystery__run", "mcp__spoof__read_file", "mcp__nowhere__x",
                                       "mcp__fs__no_such_tool", "mcp__plugin_p_db__read_query"])
def test_unrecognised_fails_closed(project, tool_name):
    d = hook(project, tool_name, policy=gate.GatePolicy(allow_up_to=RClass.R1))
    assert d["permissionDecision"] == "ask"
    assert "R4" in d["permissionDecisionReason"]


def test_spoofed_launcher_is_not_trusted_even_with_familiar_tool_names(project):
    d = hook(project, "mcp__spoof__read_file", policy=gate.GatePolicy(allow_up_to=RClass.R1))
    assert "unrecognised" in d["permissionDecisionReason"]


def test_claude_code_name_normalisation_and_double_underscore_names(project):
    # "my.files" is written mcp__my_files__...; "reports__v2" contains the separator itself.
    assert hook(project, "mcp__my_files__read_text_file", {"path": "a"},
                gate.GatePolicy(allow_up_to=RClass.R0))["permissionDecision"] == "allow"
    d = hook(project, "mcp__reports__v2__write_query", {"query": "DELETE FROM t"})
    assert d["permissionDecision"] == "ask" and "registry" in d["permissionDecisionReason"]


def test_non_mcp_tools_are_ignored(project):
    assert hook(project, "Bash", {"command": "rm -rf /"}) is None


@pytest.mark.parametrize("raw", ["not json", "[]", '{"tool_name": "mcp__"}', '{"tool_name": "mcp__db"}'])
def test_bad_input_asks_instead_of_allowing(project, raw):
    d = hook(project, None, raw=raw)
    if d is not None:  # "[]" has no tool_name: nothing to judge
        assert d["permissionDecision"] == "ask"


def test_strictest_scope_wins(project, tmp_path):
    proj, home = project
    # The same name in the user scope runs something unrecognised: the hook cannot know which scope wins.
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"db": {"command": "./local-db"}}}))
    d = hook(project, "mcp__db__read_query", {"query": "SELECT 1"}, gate.GatePolicy(allow_up_to=RClass.R1))
    assert d["permissionDecision"] == "ask"


def test_map_vouches_for_a_server(project):
    d = hook(project, "mcp__mystery__read_query", {"query": "SELECT 1"},
             gate.GatePolicy(allow_up_to=RClass.R0), mapping={"mystery": "sqlite"})
    assert d["permissionDecision"] == "allow"


def test_log_records_class_and_rule_but_not_arguments(project, tmp_path):
    log = tmp_path / "audit.jsonl"
    hook(project, "mcp__db__write_query", {"query": "DROP TABLE secret_customers"}, log=log)
    hook(project, "mcp__db__read_query", {"query": "SELECT 1"}, log=log)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["class"] for x in lines] == ["R4", "R0"]
    assert [x["decision"] for x in lines] == ["ask", "client"]
    assert "secret_customers" not in log.read_text()


def test_policy_validation():
    with pytest.raises(ValueError):
        gate.GatePolicy(allow_up_to=RClass.R2)
    with pytest.raises(ValueError):
        gate.GatePolicy(allow_up_to=RClass.R1, deny_from=RClass.R1)


# --------------------------------------------------------------------------- static exports

def policy_cli(project, capsys, *args):
    proj, home = project
    code = main(["policy", *args, str(proj / ".mcp.json")])
    return code, capsys.readouterr().out


def test_cedar(project, capsys):
    code, out = policy_cli(project, capsys, "--format", "cedar")
    assert code == 0
    assert 'resource in MCP::Server::"fs"' in out
    assert '@requireApproval("R4 Irreversible (rclass): needs a human")' in out
    assert "// mystery: not recognised" in out and "// spoof: not recognised" in out
    assert 'MCP::Server::"mystery"' not in out  # default deny keeps unknown servers out
    code, out = policy_cli(project, capsys, "--format", "cedar", "--deny-from", "R4")
    blocks = out.split("forbid (principal")[1:]
    assert any('MCP::Server::"fs"' in b and '"write_file"' in b.split(";")[0] for b in blocks)
    assert not any('"read_text_file"' in b.split(";")[0] for b in blocks)


def test_cedar_escapes_names(tmp_path, capsys):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {'a"b': CONFIG["mcpServers"]["fs"]}}))
    assert main(["policy", "--format", "cedar", str(cfg)]) == 0
    assert 'MCP::Server::"a\\"b"' in capsys.readouterr().out


def test_cel_lists_only_allowed_classes(project, capsys):
    import yaml
    code, out = policy_cli(project, capsys, "--format", "cel")
    rules = yaml.safe_load(out)["mcpAuthorization"]["rules"]
    joined = " ".join(rules)
    assert '"read_text_file"' in joined and '"write_file"' not in joined and '"write_query"' not in joined
    assert "mystery" not in joined


def test_claude_settings(project, capsys):
    code, out = policy_cli(project, capsys, "--format", "claude-settings", "--allow-up-to", "R0")
    perms = json.loads(out)["permissions"]
    assert "mcp__fs__read_text_file" in perms["allow"]
    assert "mcp__fs__write_file" in perms["ask"]
    assert "mcp__my_files__write_file" in perms["ask"]          # Claude Code's spelling of my.files
    assert "mcp__mystery__*" in perms["ask"]                    # unknown server: every tool
    assert not any("move_file" in r for r in perms["allow"])   # R1 is above --allow-up-to R0


def test_claude_hook_settings_carry_the_policy(capsys):
    assert main(["policy", "--format", "claude-hook", "--deny-from", "R4"]) == 0
    cfg = json.loads(capsys.readouterr().out)["hooks"]["PreToolUse"][0]
    assert cfg["matcher"] == "mcp__.*"
    assert cfg["hooks"][0]["command"] == "rclass hook claude-code --deny-from R4"


def test_static_policy_is_never_less_strict_than_the_hook(project):
    """For every tool, the worst-case class the exports use is at least the class of any call."""
    proj, home = project
    known, _ = gate.static_classes(gate.static_scan([proj / ".mcp.json"], REG, cwd=proj, home=home))
    for query in ("SELECT 1", "DROP TABLE t", "INSERT OR REPLACE INTO t VALUES (1)"):
        for tool in ("read_query", "write_query"):
            c = gate.classify_call("db", tool, {"query": query}, {"db": ["sqlite"]}, REG)
            assert known["db"][tool] >= c.rclass


def test_hook_cli_rejects_unknown_map_record(capsys):
    assert main(["hook", "claude-code", "--map", "x=not-a-record"]) == 2
