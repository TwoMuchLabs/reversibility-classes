"""Every test gets its own state folder, also when it runs as a hook (CLAUDE_PROJECT_DIR set), where the hook
ignores RCLASS_STATE_DIR and would otherwise use the real home folder."""

import pytest

from rclass import gate


@pytest.fixture(autouse=True)
def _own_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "STATE_DIR", tmp_path / "state" / "rclass")
    monkeypatch.setenv("RCLASS_STATE_DIR", str(tmp_path / "state"))
