"""Tests for `rclass scan`, with fake and hostile stdio MCP servers (no network, no real servers).

Many tests here reproduce findings from the independent review of `rclass scan`; their ids are noted.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from rclass import RClass, Registry
from rclass import scan as sc
from rclass.cli import main
from rclass.registry import stats

FIX = Path(__file__).parent / "fixtures"
FAKE, HOSTILE = str(FIX / "fake_mcp_server.py"), str(FIX / "hostile_mcp_server.py")
REG = Registry.bundled()
SECRET = "sk-live-THIS-MUST-NEVER-BE-PRINTED"


def git_tools():
    return [{"name": n} for n in sorted(sc._tool_names(REG, "git"))]


def fake(tmp_path, name, behaviour, **extra):
    spec = tmp_path / f"{name}.json"
    spec.write_text(json.dumps(behaviour))
    return {"command": sys.executable, "args": [FAKE, str(spec), "--token", SECRET], **extra}


def hostile(mode, extra=None):
    return {"command": sys.executable, "args": [HOSTILE, mode] + ([extra] if extra else [])}


def write_config(tmp_path, servers, key="mcpServers", name="config.json"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({key: servers}))
    return p


def scan1(tmp_path, entry, **kw):
    return sc.scan([write_config(tmp_path, {"s": entry})], REG, **kw).servers[0]


# ---------------------------------------------------------------- config parsing

def test_parses_common_config_shapes(tmp_path):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({
        "mcpServers": {"a": {"command": "x", "args": ["1"], "env": {"K": "v"}}},
        "servers": {"b": {"type": "http", "url": "https://mcp.stripe.com"}},
        "projects": {str(tmp_path / "proj"): {"mcpServers": {"c": {"command": "y"}}},
                     str(tmp_path / "other"): {"mcpServers": {"d": {"command": "z"}}}},
    }))
    (tmp_path / "proj").mkdir()
    got = {s.key: s for s in sc.load_config(p, cwd=tmp_path / "proj")}
    assert set(got) == {"a", "b", "proj/c"}          # S8: only the current project by default
    assert got["b"].remote and not got["a"].remote
    every = {s.key for s in sc.load_config(p, cwd=tmp_path / "proj", all_projects=True)}
    assert str(tmp_path / "other") + "/d" in every


def test_bad_config_is_a_clear_error(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="could not read"):
        sc.load_config(p)


@pytest.mark.parametrize("entry, error", [                     # M4, S12
    ({"command": ["npx", "x"]}, "`command` must be a string"),
    ({"command": "npx", "args": "x y"}, "`args` must be a list"),
    ({"command": "npx", "env": ["A=1"]}, "`env` must map"),
    ({}, "neither `command` nor `url`"),
])
def test_wrong_config_types_are_reported_per_server(tmp_path, entry, error):
    s = scan1(tmp_path, entry)
    assert s.status == "error" and error in s.message


def test_discovers_known_locations(tmp_path):
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text("{}")
    (tmp_path / ".mcp.json").write_text("{}")
    found = sc.known_config_paths(home=tmp_path, cwd=tmp_path)
    assert tmp_path / ".cursor" / "mcp.json" in found and tmp_path / ".mcp.json" in found


def test_variable_expansion(tmp_path, monkeypatch):                # S7
    monkeypatch.setenv("RCLASS_TEST_VAR", "hello")
    monkeypatch.delenv("RCLASS_UNSET", raising=False)
    p = write_config(tmp_path, {"v": {
        "command": "x",
        "args": ["${workspaceFolder}/data", "${env:RCLASS_TEST_VAR}", "${RCLASS_TEST_VAR}", "${RCLASS_UNSET:-fallback}"],
        "env": {"A": "${RCLASS_TEST_VAR}"}}}, key="servers", name=".vscode/mcp.json")
    s = sc.load_config(p)[0]
    assert s.args == [f"{tmp_path}/data", "hello", "hello", "fallback"] and s.env == {"A": "hello"}
    assert s.cwd == str(tmp_path) and s.project and not s.needs


def test_unresolvable_variables_are_not_started(tmp_path, monkeypatch):
    monkeypatch.delenv("RCLASS_UNSET", raising=False)
    for entry in ({"command": "x", "args": ["${input:token}"]}, {"command": "x", "env": {"T": "${RCLASS_UNSET}"}}):
        s = scan1(tmp_path, entry)
        assert s.status == "error" and "would supply or prompt for" in s.message


# ---------------------------------------------------------------- identification

@pytest.mark.parametrize("command, args, expected", [
    ("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "filesystem"),
    ("npx", ["-y", "@modelcontextprotocol/server-filesystem@2.0.1"], "filesystem"),
    ("uvx", ["mcp-server-git==2026.1.1", "--repository", "."], "git"),
    ("uv", ["tool", "run", "mcp-server-time"], "time"),
    ("docker", ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server:v1.2"], "github"),
    ("docker", ["run", "-i", "--rm", "docker.io/hashicorp/terraform-mcp-server:0.2.3"], "terraform"),
    ("npx", ["@playwright/mcp@latest"], "playwright"),
    # S3: no matching on a word that merely appears somewhere
    ("uv", ["run", "--with", "mcp[cli]", "mcp", "run", "server.py"], None),
    ("mcp", ["run", "server.py"], None),
    ("node", ["proxy.js", "--upstream-name", "mcp-server-time"], None),
    ("uvx", ["--from", "git+https://github.com/someone/fork", "mcp-server-git"], None),
    ("uvx", ["--from", "evil-fetch==6.6.6", "mcp-server-fetch"], None),
    ("npx", ["-y", "github:someone/server-filesystem"], None),
    ("npx", ["-y", "@modelcontextprotocol/server-brave-search"], None),  # archived server, different tools
    ("/usr/local/bin/github-mcp-server", ["stdio"], None),               # binaries: use --assume
])
def test_identifies_servers_by_what_they_run(command, args, expected):
    sid, _ = sc.identify_by_package(sc.ServerConfig(key="k", source="s", command=command, args=args), REG)
    assert sid == expected


def test_custom_source_is_explained():
    _, note = sc.identify_by_package(sc.ServerConfig(key="k", source="s", command="uvx",
                                                     args=["--from", "git+https://x/y", "mcp-server-git"]), REG)
    assert note and "custom source" in note


@pytest.mark.parametrize("url, expected, exact", [
    ("https://api.githubcopilot.com/mcp/", "github", True),
    ("https://api.githubcopilot.com/mcp/readonly", "github", False),     # N2
    ("https://mcp.sentry.dev/mcp?org=acme", "sentry", True),
    ("https://mcp.stripe.com", "stripe", True),
    ("https://mcp.stripe.com.evil.example/", None, False),
    ("https://example.com/mcp", None, False),
])
def test_identifies_hosted_servers_by_endpoint(url, expected, exact):
    sid, ex = sc.identify_by_endpoint(sc.ServerConfig(key="k", source="s", url=url), REG)
    assert sid == expected
    if sid:
        assert ex == exact


def test_registry_packages_are_unique_across_records():
    seen = {}
    for rec in REG.records:
        for pkg in rec["server"].get("packages", []):
            assert pkg not in seen, f"{pkg} claimed by {seen[pkg]} and {rec['server']['id']}"
            seen[pkg] = rec["server"]["id"]


# ---------------------------------------------------------------- worst-case classes (M1)

def test_scan_never_understates_a_record():
    for sid in REG.servers():
        tools, _ = sc._tool_reports(sid, [{"name": n} for n in sc._tool_names(REG, sid)], REG)
        for t in tools:
            assert t.rclass >= max(e.base for e in REG.entries(sid, t.name)), f"{sid}/{t.name} understated"


@pytest.mark.parametrize("sid, tool, cls", [
    ("puppeteer", "puppeteer_navigate", RClass.R4),
    ("github", "merge_pull_request", RClass.R4),
    ("github", "create_repository", RClass.R4),
    ("kubernetes", "resources_scale", RClass.R3),
    ("playwright", "browser_take_screenshot", RClass.R4),
])
def test_tools_with_lowering_rules_report_their_worst_case(sid, tool, cls):
    c, _, _, lower, _ = sc.worst_case(REG, sid, tool)
    assert c is cls and lower


def test_hosted_github_counts_match_the_record(tmp_path):
    s = scan1(tmp_path, {"url": "https://api.githubcopilot.com/mcp/"})
    rec = next(r for r in REG.records if r["server"]["id"] == "github")
    base = {str(c): sum(1 for t in rec["tools"] if RClass.parse(t["class"]) is c) for c in RClass}
    assert s.counts() == base  # GitHub's rules only lower classes, so worst case == base class


# ---------------------------------------------------------------- scanning a live (fake) server

def test_scan_unrecognised_server_fails_closed_and_shows_hints(tmp_path):
    s = scan1(tmp_path, fake(tmp_path, "mine", {
        "tools": [{"name": "read_thing", "annotations": {"readOnlyHint": True}}, {"name": "zap_thing"}],
        "page_size": 1, "noise": True, "server_request": True}))
    assert s.status == "scanned" and s.record is None
    assert {t.name: t.rclass for t in s.tools} == {"read_thing": RClass.R4, "zap_thing": RClass.R4}
    assert {t.name: t.hint for t in s.tools}["read_thing"] is RClass.R0


def test_tool_names_alone_never_match_a_record(tmp_path):        # S2
    tools = [t for t in git_tools() if t["name"] != "git_log"] + [{"name": "git_push"}]
    s = scan1(tmp_path, fake(tmp_path, "g", {"tools": tools}))
    assert s.record is None and s.suggested == "git"
    assert all(t.rclass is RClass.R4 for t in s.tools)


def test_lookalike_single_tool_is_not_even_suggested(tmp_path):   # S2
    s = scan1(tmp_path, hostile("fetch_lookalike"))
    assert s.record is None and s.suggested is None and s.tools[0].rclass is RClass.R4


def test_assume_maps_a_server_to_a_record_and_reports_drift(tmp_path):
    tools = [t for t in git_tools() if t["name"] != "git_log"] + [{"name": "git_push"}]
    s = sc.scan([write_config(tmp_path, {"g": fake(tmp_path, "g", {"tools": tools})})], REG, assume={"g": "git"}).servers[0]
    assert s.record == "git" and s.matched_by == "assumed"
    assert s.not_offered == ["git_log"] and s.unclassified == ["git_push"]


def test_project_config_needs_consent(tmp_path):                 # M6
    p = write_config(tmp_path, {"s": fake(tmp_path, "f", {"tools": [{"name": "x"}]})}, name=".mcp.json")
    s = sc.scan([p], REG).servers[0]
    assert s.status == "skipped" and s.needs_consent
    assert sc.scan([p], REG, consent=True).servers[0].status == "scanned"


def test_claude_code_approvals_are_honoured(tmp_path):           # M6
    proj = tmp_path / "proj"
    p = write_config(proj, {"on": fake(tmp_path, "a", {"tools": [{"name": "x"}]}),
                            "off": fake(tmp_path, "b", {"tools": [{"name": "y"}]})}, name=".mcp.json")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps({"projects": {str(proj.resolve()): {
        "enabledMcpjsonServers": ["on"], "disabledMcpjsonServers": ["off"]}}}))
    by = {s.key: s for s in sc.scan([p], REG, home=home, cwd=proj).servers}
    assert by["on"].status == "scanned" and by["off"].status == "skipped" and "Claude Code" in by["off"].message


def test_servers_get_a_minimal_environment(tmp_path, monkeypatch):  # M6
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAFAKESECRET")
    out = tmp_path / "env.json"
    s = scan1(tmp_path, {**hostile("envleak", str(out)), "env": {"OWN": "1"}})
    assert s.status == "scanned"
    seen = json.loads(out.read_text())
    assert "AWS_SECRET_ACCESS_KEY" not in seen and seen["OWN"] == "1" and "PATH" in seen
    scan1(tmp_path, hostile("envleak", str(out)), inherit_env=True)
    assert "AWS_SECRET_ACCESS_KEY" in json.loads(out.read_text())


# ---------------------------------------------------------------- hostile servers: M3, M4, M5, S1, S4, S5, S6

@pytest.mark.parametrize("mode", ["error_string", "result_list", "tools_not_list"])
def test_malformed_replies_become_errors(tmp_path, mode):
    assert scan1(tmp_path, hostile(mode)).status == "error"


def test_unparseable_lines_are_skipped(tmp_path):
    # 200,000 nested brackets before the real reply: skipped, not a crash
    assert scan1(tmp_path, hostile("deep")).status == "scanned"


@pytest.mark.parametrize("mode", ["dict_name", "int_name"])
def test_invalid_tool_names_make_the_scan_incomplete(tmp_path, mode):
    s = scan1(tmp_path, hostile(mode))
    assert s.status == "scanned" and s.invalid_names >= 1 and s.incomplete


def test_server_that_exits_immediately_is_reported(tmp_path):
    for _ in range(5):  # the original crash was a race; repeat it
        s = scan1(tmp_path, {"command": "sh", "args": ["-c", "echo missing API_URL >&2; exit 3"]})
        assert s.status == "error" and "exited" in s.message


def test_request_flood_cannot_hang_the_scan(tmp_path):
    t0 = time.monotonic()
    s = scan1(tmp_path, hostile("request_flood"), timeout=4)
    assert s.status == "error" and time.monotonic() - t0 < 20


def test_endless_cursor_and_duplicates_are_bounded(tmp_path):
    s = scan1(tmp_path, hostile("endless_cursor"))
    assert s.truncated and len(s.tools) == 1 and s.incomplete
    d = scan1(tmp_path, hostile("dup_tools"))
    assert len(d.tools) == 1 and d.duplicates == 2


def test_oversized_lines_and_stderr_floods_are_survived(tmp_path):
    assert scan1(tmp_path, hostile("huge_line"), timeout=30).status == "scanned"
    assert scan1(tmp_path, hostile("stderr_flood"), timeout=30).status == "scanned"


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_children_of_a_server_are_killed(tmp_path):
    pidfile = tmp_path / "child.pid"
    scan1(tmp_path, hostile("orphan", str(pidfile)), timeout=5)
    pid = int(pidfile.read_text())
    time.sleep(0.5)
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split()[2]
    except FileNotFoundError:
        return
    assert state == "Z"  # killed; a zombie only because this init does not reap orphans


def test_control_characters_cannot_forge_output(tmp_path, capsys):
    main(["scan", str(write_config(tmp_path, {"evil": hostile("ansi")})), "--details"])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\nAll tools are R0" not in out and "unprintable:" in out


# ---------------------------------------------------------------- secrets (M7)

def test_server_text_is_hidden_by_default_and_redacted_on_request(tmp_path, capsys):
    cfg = write_config(tmp_path, {"db": {**hostile("error_secret", "/srv/customers/acme-merger"),
                                         "env": {"DB_PASSWORD": "hunter2-prod"}}})
    main(["scan", str(cfg)])
    out = capsys.readouterr()
    for leaked in ("hunter2", "ghp_", "acme-merger"):
        assert leaked not in out.out and leaked not in out.err
    main(["scan", str(cfg), "--show-server-errors", "--json"])
    doc = capsys.readouterr().out
    assert "server_text_redacted" in doc
    for leaked in ("hunter2", "ghp_abcdefghij", "acme-merger"):
        assert leaked not in doc


def test_cli_never_prints_config_secrets(tmp_path, capsys):
    cfg = write_config(tmp_path, {"mine": {**fake(tmp_path, "m", {"tools": [{"name": "t"}]}), "env": {"API_KEY": SECRET}}})
    for flags in ([], ["--json"], ["--details"], ["--list"], ["--show-server-errors"]):
        main(["scan", str(cfg), *flags])
        out = capsys.readouterr()
        assert SECRET not in out.out and SECRET not in out.err


# ---------------------------------------------------------------- CLI: exit codes (M2, S13)

def test_fail_on_threshold(tmp_path):
    cfg = write_config(tmp_path, {"g": {"command": "uvx", "args": ["mcp-server-git"]}})
    assert main(["scan", str(cfg), "--list", "--fail-on", "R4"]) == 1       # git_checkout is R4, from the registry
    cfg2 = write_config(tmp_path, {"t": {"command": "uvx", "args": ["mcp-server-time"]}}, name="c2.json")
    assert main(["scan", str(cfg2), "--list", "--fail-on", "R1"]) == 0      # time is all R0


def test_unknown_servers_count_as_r4(tmp_path):
    cfg = write_config(tmp_path, {"t": {"command": "uvx", "args": ["mcp-server-time"]},
                                  "r": {"url": "https://unknown.example/mcp"}})
    assert main(["scan", str(cfg), "--list", "--fail-on", "R4"]) == 1
    cfg2 = write_config(tmp_path, {"t": {"command": "uvx", "args": ["mcp-server-time"]},
                                   "n": {"command": "node", "args": ["mine.js"]}}, name="c2.json")
    assert main(["scan", str(cfg2), "--list", "--fail-on", "R4"]) == 1


def test_incomplete_scan_cannot_pass_the_gate(tmp_path):
    cfg = write_config(tmp_path, {"ok": {"command": "uvx", "args": ["mcp-server-time"]},
                                  "broken": {"command": "definitely-not-a-real-binary-xyz"}})
    assert main(["scan", str(cfg), "--fail-on", "R1", "--server", "broken"]) == 3


def test_all_servers_failing_is_exit_3(tmp_path):
    assert main(["scan", str(write_config(tmp_path, {"broken": {"command": "definitely-not-a-real-binary-xyz"}}))]) == 3


def test_fail_on_rejects_unknown_classes(tmp_path):
    with pytest.raises(SystemExit):
        main(["scan", str(write_config(tmp_path, {})), "--fail-on", "high"])


def test_unknown_server_name_lists_what_is_configured(tmp_path, capsys):
    cfg = write_config(tmp_path, {"alpha": {"command": "x"}})
    assert main(["scan", str(cfg), "--server", "nosuch"]) == 2
    assert "Configured: alpha" in capsys.readouterr().err


def test_no_config_found_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sc, "known_config_paths", lambda *a, **k: [])
    assert main(["scan"]) == 2
    assert "No MCP client configuration found" in capsys.readouterr().err


# ---------------------------------------------------------------- output and drafts

def test_duplicates_are_counted_once(tmp_path):                  # S8
    entry = {"command": "uvx", "args": ["mcp-server-time"]}
    a = write_config(tmp_path, {"clock": entry}, name="a.json")
    b = write_config(tmp_path, {"clock": entry}, name="b.json")
    res = sc.scan([a, b], REG, start=False)
    assert [s.status for s in res.servers] == ["static", "duplicate"] and res.totals()["tools"] == 2


def test_json_is_complete(tmp_path, capsys):
    cfg = write_config(tmp_path, {"g": fake(tmp_path, "g", {"tools": git_tools()})})
    main(["scan", str(cfg), "--json", "--assume", "g=git"])
    doc = json.loads(capsys.readouterr().out)
    s = doc["servers"][0]
    assert doc["report_version"] == 1 and "rclass_version" in doc and "generated_at" in doc
    assert s["record"] == "git" and doc["totals"]["tools"] == len(git_tools())
    assert all({"name", "class", "source", "rationale", "review"} <= set(t) for t in s["tools"])


def test_drafts_never_overwrite_and_additions_are_named_as_such(tmp_path):  # S10
    out = tmp_path / "drafts"
    cfg = write_config(tmp_path, {
        "a": fake(tmp_path, "a", {"tools": [{"name": "x"}], "name": "My Server"}),
        "b": fake(tmp_path, "b", {"tools": [{"name": "y"}], "name": "My Server"}),
        "g": fake(tmp_path, "g", {"tools": git_tools() + [{"name": "git_push"}]})})
    main(["scan", str(cfg), "--write-drafts", str(out), "--assume", "g=git"])
    assert sorted(p.name for p in out.glob("*.yaml")) == ["git.additions.yaml", "my-server-2.yaml", "my-server.yaml"]
    assert main(["validate", "--strict", str(out)]) == 1
    assert main(["validate", str(out)]) == 0


def test_override_needs_a_reason(tmp_path):
    rec = {"spec_version": "0.2", "server": {"id": "git", "name": "g", "source": "https://x.test", "evidence": ["https://x.test"]},
           "tools": [{"name": "git_checkout", "class": "R3", "rationale": "Snapshots."}]}
    p = tmp_path / "o.json"
    p.write_text(json.dumps(rec))
    with pytest.raises(SystemExit, match="server.notes"):
        main(["lookup", "git", "git_checkout", "--override", str(p)])


def test_stats_and_registry_agree_on_totals():
    assert stats(REG)["tools"] == sum(len(sc._tool_names(REG, sid)) for sid in REG.servers())
