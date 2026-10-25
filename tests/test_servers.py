"""Server mappings: recognising a company's own MCP servers (rclass/servers.py)."""

import io
import json

import pytest
import yaml

from rclass import gate, servers
from rclass.cli import main
from rclass.model import RClass
from rclass.registry import Registry

MAPPINGS = {"servers": [
    {"record": "github", "image": "registry.corp.example/mcp/github-mcp-server"},
    {"record": "sqlite", "command": "/opt/mcp/db/bin/server", "args": ["--stdio"]},
    {"record": "filesystem", "python": "/opt/corp/py/bin/python3", "module": "corp_fs.mcp"},
    {"record": "memory", "package": "@corp/memory-mcp", "registry": "https://npm.corp.example/"},
    {"record": "fetch", "package": "corp-fetch-mcp", "registry": "https://pypi.corp.example/simple"},
    {"record": "github", "url": "https://mcp-gw.corp.example/github"},
]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RCLASS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("RCLASS_SERVERS", raising=False)
    proj, home = tmp_path / "p", tmp_path / "h"
    proj.mkdir(), home.mkdir()
    reg = Registry.bundled()
    reg.mappings = servers.parse(MAPPINGS, "test")
    return proj, home, reg


def decide(env, servers_cfg, tool_name, args=None, policy=None):
    proj, home, reg = env
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": servers_cfg}))
    event = json.dumps({"session_id": tool_name, "cwd": str(proj), "tool_name": tool_name, "tool_input": args or {}})
    out = io.StringIO()
    gate.run_hook(io.StringIO(event), out, policy or gate.GatePolicy(allow_up_to=RClass.R1), lambda: reg, home=home)
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("cfg,tool,args", [
    ({"command": "docker", "args": ["run", "-i", "--rm", "registry.corp.example/mcp/github-mcp-server:v2.1.0"]},
     "get_me", {}),
    ({"command": "/opt/mcp/db/bin/server", "args": ["--stdio"]}, "read_query", {"query": "SELECT 1"}),
    ({"command": "/opt/corp/py/bin/python3", "args": ["-m", "corp_fs.mcp", "/srv"]}, "read_text_file", {"path": "a"}),
    ({"command": "npx", "args": ["--registry", "https://npm.corp.example", "-y", "@corp/memory-mcp@1.0.0"]},
     "read_graph", {}),
    ({"command": "uvx", "args": ["--index-url", "https://pypi.corp.example/simple", "corp-fetch-mcp"]},
     "fetch", {"url": "https://docs.python.org"}),
    ({"url": "https://mcp-gw.corp.example/github"}, "get_me", {}),
])
def test_each_kind_of_internal_server_is_recognised(env, cfg, tool, args):
    d = decide(env, {"x": cfg}, f"mcp__x__{tool}", args)
    assert d["permissionDecision"] == "allow", d


@pytest.mark.parametrize("cfg,why", [
    ({"command": "/opt/mcp/db/bin/server", "args": ["--stdio", "--evil"]}, "different arguments"),
    ({"command": "/opt/mcp/db/bin/server", "args": ["--stdio"], "env": {"LD_PRELOAD": "/tmp/x.so"}}, "code-changing env"),
    ({"command": "npx", "args": ["--registry", "https://npm.evil.example", "-y", "@corp/memory-mcp"]}, "other registry"),
    ({"command": "npx", "args": ["--registry", "https://npm.corp.example", "-p", "@corp/memory-mcp", "sh"]}, "-p"),
    ({"command": "uvx", "args": ["--index-url", "https://pypi.corp.example/simple", "--with", "evil",
                                 "corp-fetch-mcp"]}, "extra packages"),
    ({"command": "docker", "args": ["run", "-i", "registry.corp.example/mcp/github-mcp-server", "sh"]}, "command after image"),
    ({"command": "/opt/corp/py/bin/python3", "args": ["-m", "other.module"]}, "other module"),
    ({"url": "https://mcp-gw.corp.example.evil.com/github"}, "lookalike gateway"),
])
def test_mappings_do_not_vouch_for_anything_else(env, cfg, why):
    tool = "read_query" if "db" in json.dumps(cfg) else "get_me"
    d = decide(env, {"x": cfg}, f"mcp__x__{tool}", {"query": "SELECT 1"})
    assert d["permissionDecision"] == "ask" and "R4" in d["permissionDecisionReason"], why


def test_launcher_inside_the_project_is_never_mapped(env):
    proj, home, reg = env
    inside = str(proj / "bin" / "server")
    reg.mappings = servers.parse({"servers": [{"record": "sqlite", "command": inside}]}, "t")
    d = decide(env, {"x": {"command": inside}}, "mcp__x__read_query", {"query": "SELECT 1"})
    assert d["permissionDecision"] == "ask"


def test_conflicting_mappings_are_unrecognised(env):
    proj, home, reg = env
    reg.mappings = servers.parse({"servers": [{"record": "sqlite", "command": "/opt/a"},
                                              {"record": "memory", "command": "/opt/a"}]}, "t")
    d = decide(env, {"x": {"command": "/opt/a"}}, "mcp__x__read_query", {"query": "SELECT 1"})
    assert d["permissionDecision"] == "ask"


def test_mirrored_image_keeps_version_scoping(env):
    """A mirror pinned below github's min_version (2.0.3) gets no class-lowering rules."""
    old = {"command": "docker", "args": ["run", "-i", "registry.corp.example/mcp/github-mcp-server:v0.10.0"]}
    d = decide(env, {"x": old}, "mcp__x__create_repository", {"name": "r", "private": True})
    assert d["permissionDecision"] == "ask"


@pytest.mark.parametrize("bad", [
    {"servers": [{"image": "x"}]},
    {"servers": [{"record": "github", "image": "x", "url": "https://y"}]},
    {"servers": [{"record": "github", "command": "relative/path"}]},
    {"servers": [{"record": "github", "module": "m"}]},
    {"servers": [{"record": "github", "package": "p"}]},
    {"servers": [{"record": "github", "url": "http://plain"}]},
    {"nope": []},
])
def test_malformed_mapping_files_are_rejected(bad):
    with pytest.raises(servers.MappingError):
        servers.parse(bad, "t")


def test_cli_rejects_unknown_records_and_asks_in_the_hook(tmp_path, capsys, monkeypatch):
    f = tmp_path / "servers.yaml"
    f.write_text(yaml.safe_dump({"servers": [{"record": "no-such-record", "command": "/opt/x"}]}))
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"x": {"command": "/opt/x"}}}))
    assert main(["scan", "--list", str(cfg), "--servers", str(f)]) == 2
    assert "no-such-record" in capsys.readouterr().err
    monkeypatch.setenv("RCLASS_STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name": "mcp__x__y"}'))
    assert main(["hook", "claude-code", "--servers", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_scan_shows_mapping_and_warns_when_the_file_is_writable(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RCLASS_SERVERS", raising=False)
    f = tmp_path / "servers.yaml"
    f.write_text(yaml.safe_dump({"servers": [{"record": "sqlite", "command": "/opt/mcp/db"}]}))
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"db": {"command": "/opt/mcp/db"}}}))
    main(["scan", "--list", str(cfg), "--servers", str(f)])
    out = capsys.readouterr()
    assert "recognised by your server mapping" in out.out
    assert "you can edit the server mapping" in out.err


def test_env_variable_and_hook_settings_carry_the_mapping(tmp_path, capsys, monkeypatch):
    import shlex
    f = tmp_path / "servers.yaml"
    f.write_text(yaml.safe_dump({"servers": [{"record": "sqlite", "command": "/opt/mcp/db"}]}))
    monkeypatch.setenv("RCLASS_SERVERS", str(f))
    assert servers.default_paths() == [f]
    assert main(["policy", "--format", "claude-hook", "--servers", str(f)]) == 0
    cmd = shlex.split(json.loads(capsys.readouterr().out)["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
    assert cmd[cmd.index("--servers") + 1] == str(f.resolve())
