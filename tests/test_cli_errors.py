"""Every way a user can mistype a command ends in a one-line message and exit 2, never a traceback."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rclass.cli import main

REPO = Path(__file__).resolve().parent.parent


def run(argv, capsys, cwd=None, monkeypatch=None):
    if cwd is not None:
        monkeypatch.chdir(cwd)
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_validate_outside_repo_checks_bundled_registry(tmp_path, capsys, monkeypatch):
    code, out, err = run(["validate"], capsys, tmp_path, monkeypatch)
    assert code == 0
    assert "bundled registry" in err
    assert "20/20 records valid" in out


def test_validate_missing_path(tmp_path, capsys, monkeypatch):
    code, _, err = run(["validate", "nosuch.yaml"], capsys, tmp_path, monkeypatch)
    assert code == 2 and "path not found: nosuch.yaml" in err


def test_validate_empty_folder_is_not_a_pass(tmp_path, capsys, monkeypatch):
    (tmp_path / "empty").mkdir()
    code, _, err = run(["validate", "empty"], capsys, tmp_path, monkeypatch)
    assert code == 2 and "no record files" in err


@pytest.mark.parametrize("content,expect", [
    ("spec_version: '0.2'\nserver: {id: x\n", "not valid YAML at line"),
    ("- just\n- a list\n", "must be a mapping"),
    ("", "must be a mapping"),
])
def test_bad_record_files_are_one_line_errors(tmp_path, capsys, monkeypatch, content, expect):
    f = tmp_path / "rec.yaml"
    f.write_text(content)
    for argv in (["validate", str(f)], ["lookup", "x", "y", "--registry", str(f)],
                 ["lookup", "x", "y", "--override", str(f)], ["stats", "--registry", str(f)]):
        code, _, err = run(argv, capsys)
        assert code in (1, 2), argv
        assert expect in err, (argv, err)
        assert "Traceback" not in err


def test_bad_json_record(tmp_path, capsys):
    f = tmp_path / "rec.json"
    f.write_text("{bad")
    code, _, err = run(["lookup", "x", "y", "--registry", str(f)], capsys)
    assert code == 2 and "not valid JSON at line 1" in err


def test_override_missing_file(capsys):
    code, _, err = run(["lookup", "a", "b", "--override", "/nope/override.yaml"], capsys)
    assert code == 2 and "--override file not found" in err


def test_lookup_suggests_close_server(capsys):
    code, out, err = run(["lookup", "githb", "issue_write"], capsys)
    assert code == 0 and out.startswith("R4")
    assert "did you mean github" in err


def test_lookup_suggests_close_tool(capsys):
    code, out, err = run(["lookup", "github", "issue_writ"], capsys)
    assert out.startswith("R4") and "did you mean issue_write" in err


def test_lookup_unknown_server_points_at_stats(capsys):
    code, out, err = run(["lookup", "zzzzzz", "t"], capsys)
    assert out.startswith("R4") and "rclass stats" in err


def test_lookup_json_stays_clean(capsys):
    code, out, err = run(["lookup", "githb", "issue_write", "--json"], capsys)
    assert json.loads(out)["class"] == "R4"
    assert "did you mean" in err


def test_scan_bad_json_config_gives_position_not_content(tmp_path, capsys):
    f = tmp_path / "mcp.json"
    f.write_text('{"mcpServers": {"x": {"command": "a", "env": {"TOKEN": "ghp_secret123456789012345678901234567890"}},}}')
    code, _, err = run(["scan", "--list", str(f)], capsys)
    assert code == 2
    assert "not valid JSON (line 1" in err
    assert "ghp_" not in err


def test_installed_entry_point_never_tracebacks(tmp_path):
    """Run through a real subprocess, from a folder that is not the repository."""
    for argv in (["validate"], ["validate", "missing"], ["lookup", "a", "b", "--override", "missing"]):
        r = subprocess.run([sys.executable, "-c", "import sys; from rclass.cli import main; sys.exit(main())", *argv],
                           cwd=tmp_path, capture_output=True, text=True, env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"})
        assert "Traceback" not in r.stderr, (argv, r.stderr)
