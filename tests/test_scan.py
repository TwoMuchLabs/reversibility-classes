"""Tests for `rclass scan`, using a fake stdio MCP server so no network or real servers are needed."""

import json
import sys
from pathlib import Path

import pytest

from rclass import RClass, Registry
from rclass import scan as sc
from rclass.cli import main

FAKE = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")
REG = Registry.bundled()
SECRET = "sk-live-THIS-MUST-NEVER-BE-PRINTED"


def git_tools():
    return [{"name": n} for n in sorted(sc._tool_names(REG, "git"))]


def fake(tmp_path, name, behaviour, **extra):
    spec = tmp_path / f"{name}.json"
    spec.write_text(json.dumps(behaviour))
    return {"command": sys.executable, "args": [FAKE, str(spec), "--token", SECRET], **extra}


def write_config(tmp_path, servers, key="mcpServers"):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({key: servers}))
    return p


# ---------------------------------------------------------------- config parsing

def test_parses_common_config_shapes(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "mcpServers": {"a": {"command": "x", "args": ["1"], "env": {"K": "v"}}},
        "servers": {"b": {"type": "http", "url": "https://mcp.stripe.com"}},       # VS Code
        "projects": {"/home/me/proj": {"mcpServers": {"c": {"command": "y"}}}},    # ~/.claude.json
    }))
    got = {s.key: s for s in sc.load_config(p)}
    assert set(got) == {"a", "b", "proj/c"}
    assert got["b"].remote and not got["a"].remote


def test_bad_config_is_a_clear_error(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="could not read"):
        sc.load_config(p)


def test_discovers_known_locations(tmp_path):
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text("{}")
    (tmp_path / ".mcp.json").write_text("{}")
    found = sc.known_config_paths(home=tmp_path, cwd=tmp_path)
    assert tmp_path / ".cursor" / "mcp.json" in found and tmp_path / ".mcp.json" in found


# ---------------------------------------------------------------- identification

@pytest.mark.parametrize("command, args, expected", [
    ("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "filesystem"),
    ("npx", ["-y", "@modelcontextprotocol/server-filesystem@2.0.1"], "filesystem"),
    ("uvx", ["mcp-server-git==2026.1.1", "--repository", "."], "git"),
    ("docker", ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server:v1.2"], "github"),
    ("/usr/local/bin/github-mcp-server", ["stdio"], "github"),
    ("npx", ["@playwright/mcp@latest"], "playwright"),
    ("npx", ["-y", "@modelcontextprotocol/server-brave-search"], None),  # archived server, different tools
    ("node", ["my-own-server.js"], None),
])
def test_identifies_servers_by_package(command, args, expected):
    cfg = sc.ServerConfig(key="k", source="s", command=command, args=args)
    assert sc.identify_by_package(cfg, REG) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://api.githubcopilot.com/mcp/", "github"),
    ("https://api.githubcopilot.com/mcp/x/repos", "github"),
    ("https://mcp.stripe.com", "stripe"),
    ("https://mcp.stripe.com.evil.example/", None),
    ("https://example.com/mcp", None),
])
def test_identifies_hosted_servers_by_endpoint(url, expected):
    assert sc.identify_by_endpoint(sc.ServerConfig(key="k", source="s", url=url), REG) == expected


def test_registry_packages_are_unique_across_records():
    seen = {}
    for rec in REG.records:
        for pkg in rec["server"].get("packages", []):
            assert pkg not in seen, f"{pkg} claimed by {seen[pkg]} and {rec['server']['id']}"
            seen[pkg] = rec["server"]["id"]


# ---------------------------------------------------------------- scanning a live (fake) server

def test_scan_unrecognised_server_fails_closed_and_shows_hints(tmp_path):
    cfg = write_config(tmp_path, {"mine": fake(tmp_path, "mine", {
        "tools": [{"name": "read_thing", "annotations": {"readOnlyHint": True}}, {"name": "zap_thing"}],
        "page_size": 1, "noise": True, "server_request": True,
    })})
    res = sc.scan([cfg], REG)
    s = res.servers[0]
    assert s.status == "scanned" and s.record is None
    assert {t.name: t.rclass for t in s.tools} == {"read_thing": RClass.R4, "zap_thing": RClass.R4}
    assert s.unclassified == ["read_thing", "zap_thing"]
    assert {t.name: t.hint for t in s.tools}["read_thing"] is RClass.R0  # shown, not trusted


def test_scan_recognises_by_tools_and_reports_drift(tmp_path):
    tools = git_tools()
    tools = [t for t in tools if t["name"] != "git_log"] + [{"name": "git_push"}]
    cfg = write_config(tmp_path, {"g": fake(tmp_path, "g", {"tools": tools, "name": "mcp-git"})})
    s = sc.scan([cfg], REG).servers[0]
    assert s.record == "git" and s.matched_by == "tools"
    assert s.not_offered == ["git_log"]
    assert s.unclassified == ["git_push"]
    assert next(t for t in s.tools if t.name == "git_push").rclass is RClass.R4


def test_scan_error_and_timeout_are_reported_not_raised(tmp_path):
    cfg = write_config(tmp_path, {
        "crashes": fake(tmp_path, "c", {"crash": True}),
        "hangs": fake(tmp_path, "h", {"tools": [{"name": "x"}], "hang": True}),
        "missing": {"command": "definitely-not-a-real-binary-xyz"},
    })
    res = sc.scan([cfg], REG, timeout=3)
    by = {s.key: s for s in res.servers}
    assert by["crashes"].status == "error" and "exited" in by["crashes"].message
    assert by["hangs"].status == "error" and "timed out" in by["hangs"].message
    assert by["missing"].status == "error" and "could not start" in by["missing"].message


def test_vscode_input_placeholders_are_not_started(tmp_path):
    cfg = write_config(tmp_path, {"v": {"command": "npx", "args": ["x"], "env": {"TOKEN": "${input:token}"}}}, key="servers")
    s = sc.scan([cfg], REG).servers[0]
    assert s.status == "error" and "prompts for" in s.message


def test_list_mode_starts_nothing(tmp_path):
    marker = tmp_path / "started"
    cfg = write_config(tmp_path, {"m": {"command": sys.executable, "args": ["-c", f"open({str(marker)!r},'w')"]}})
    s = sc.scan([cfg], REG, start=False).servers[0]
    assert s.status == "listed" and not marker.exists()


# ---------------------------------------------------------------- CLI: output, secrets, exit codes

def test_cli_never_prints_secrets(tmp_path, capsys):
    cfg = write_config(tmp_path, {"mine": {**fake(tmp_path, "m", {"tools": [{"name": "t"}]}), "env": {"API_KEY": SECRET}}})
    for flags in ([], ["--json"], ["--details"], ["--list"]):
        main(["scan", str(cfg), *flags])
        out = capsys.readouterr()
        assert SECRET not in out.out and SECRET not in out.err


def test_cli_fail_on_threshold(tmp_path):
    cfg = write_config(tmp_path, {"g": {**fake(tmp_path, "g", {"tools": git_tools()})}})
    assert main(["scan", str(cfg), "--fail-on", "R4"]) == 1      # git_checkout is R4
    cfg_r = write_config(tmp_path, {"g": {"command": "uvx", "args": ["mcp-server-git"]}})
    assert sc.identify_by_package(sc.load_config(cfg_r)[0], REG) == "git"
    cfg2 = write_config(tmp_path, {"u": fake(tmp_path, "u", {"tools": [{"name": "unknown_tool"}]})})
    assert main(["scan", str(cfg2), "--fail-on", "R3"]) == 1     # unclassified counts as R4


def test_cli_json_is_complete(tmp_path, capsys):
    cfg = write_config(tmp_path, {"g": fake(tmp_path, "g", {"tools": git_tools()})})
    main(["scan", str(cfg), "--json"])
    doc = json.loads(capsys.readouterr().out)
    s = doc["servers"][0]
    assert s["record"] == "git" and s["counts"]["R0"] >= 1 and doc["totals"]["tools"] == len(git_tools())
    assert all({"name", "class", "source", "rationale"} <= set(t) for t in s["tools"])


def test_cli_writes_valid_looking_drafts(tmp_path):
    cfg = write_config(tmp_path, {"mine": fake(tmp_path, "m", {"tools": [{"name": "a"}, {"name": "b"}], "name": "My Server"})})
    out = tmp_path / "drafts"
    main(["scan", str(cfg), "--write-drafts", str(out)])
    import yaml
    files = list(out.glob("*.yaml"))
    assert len(files) == 1
    doc = yaml.safe_load(files[0].read_text())
    assert [t["name"] for t in doc["tools"]] == ["a", "b"] and all(t["class"] == "R4" for t in doc["tools"])


def test_cli_no_config_found_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sc, "known_config_paths", lambda *a, **k: [])
    assert main(["scan"]) == 2
    assert "No MCP client configuration found" in capsys.readouterr().err


def test_override_needs_a_reason(tmp_path):
    rec = {"spec_version": "0.2", "server": {"id": "git", "name": "g", "source": "https://x.test", "evidence": ["https://x.test"]},
           "tools": [{"name": "git_checkout", "class": "R3", "rationale": "Snapshots."}]}
    p = tmp_path / "o.json"
    p.write_text(json.dumps(rec))
    with pytest.raises(SystemExit, match="server.notes"):
        main(["lookup", "git", "git_checkout", "--override", str(p)])
