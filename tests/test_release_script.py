import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
import sys; sys.modules["release"] = release
spec.loader.exec_module(release)


def test_parses_real_changelog():
    rs = release.parse_changelog((ROOT / "CHANGELOG.md").read_text())
    tags = [r.tag for r in rs]
    assert tags[:3] == ["v0.3.0", "v0.2.0", "v0.1.0"]
    assert rs[0].title == "v0.3.0 — Check your own agent"
    assert "rclass scan" in rs[0].notes and "## v0.2.0" not in rs[0].notes


def test_current_version_has_a_dated_section():
    """A version bump without a CHANGELOG section would release nothing; catch it in CI."""
    cur = release.project_version((ROOT / "pyproject.toml").read_text())
    assert cur in [r.tag for r in release.parse_changelog((ROOT / "CHANGELOG.md").read_text())]


def test_unreleased_section_is_not_part_of_previous_notes():
    text = "# C\n\n## Unreleased\n\n- next\n\n## v1.0.0 — 2026-01-01\n\n**One.**\n\n- a\n\n## Older notes\n\n- x\n"
    (r,) = release.parse_changelog(text)
    assert r.tag == "v1.0.0" and r.notes == "**One.**\n\n- a\n"
