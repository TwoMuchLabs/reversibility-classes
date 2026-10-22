"""Checks over the bundled open registry."""

import re

import pytest

from rclass import RClass, Registry, classify

REG = Registry.bundled()
ENTRIES = REG.all_entries()


def test_registry_loads_and_is_non_trivial():
    assert len(REG.servers()) >= 10
    assert len(ENTRIES) >= 50


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: f"{e.server}/{e.name}")
def test_lowering_rules_are_anchored_allow_lists(entry):
    """SPEC 5.3: a `matches` rule that lowers the class must be an anchored positive pattern."""
    for rule in entry.raw.get("when", []):
        if RClass.parse(rule["class"]) < entry.base and "matches" in rule:
            pattern = rule["matches"]
            assert pattern.startswith("^") and pattern.endswith("$"), pattern
            re.compile(pattern)


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e.disclosure], ids=lambda e: f"{e.server}/{e.name}")
def test_disclosures_are_r4(entry):
    assert entry.base is RClass.R4


@pytest.mark.parametrize(
    "server, tool, args, expected",
    [
        ("filesystem", "read_text_file", {"path": "a"}, RClass.R0),
        ("filesystem", "write_file", {"path": "a", "content": "x"}, RClass.R4),
        ("filesystem", "edit_file", {"path": "a", "edits": [], "dryRun": True}, RClass.R0),
        ("git", "git_commit", {"repo_path": ".", "message": "m"}, RClass.R1),
        ("slack", "slack_post_message", {"channel_id": "C1", "text": "hi"}, RClass.R4),
        ("sqlite", "write_query", {"query": "INSERT OR REPLACE INTO t VALUES (1)"}, RClass.R4),
        ("postgres", "query", {"sql": "COMMIT; DROP TABLE t"}, RClass.R4),
        ("sqlite", "write_query", {"query": "DROP TABLE t"}, RClass.R4),
        ("postgres", "query", {"sql": "SELECT 1"}, RClass.R0),
        # Regression cases from the first independent audit
        ("puppeteer", "puppeteer_navigate", {"url": "https://x.test"}, RClass.R0),
        ("puppeteer", "puppeteer_navigate", {"url": "https://x.test", "launchOptions": {"args": []}}, RClass.R4),
        ("kubernetes", "resources_delete", {"apiVersion": "apps/v1", "kind": "Deployment", "name": "a"}, RClass.R3),
        ("kubernetes", "resources_delete", {"apiVersion": "apps/v1", "kind": "StatefulSet", "name": "a"}, RClass.R4),
        ("playwright", "browser_take_screenshot", {}, RClass.R1),
        ("playwright", "browser_take_screenshot", {"filename": "README.md"}, RClass.R4),
        ("github", "merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 1}, RClass.R2),
        ("github", "merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 1, "commit_message": "x"}, RClass.R4),
        ("github", "issue_write", {"method": "update", "title": "New title"}, RClass.R4),
        ("sentry", "create_alert_rule", {"status": "disabled"}, RClass.R1),
        ("sentry", "create_alert_rule", {}, RClass.R4),
    ],
)
def test_known_examples(server, tool, args, expected):
    assert classify(server, tool, args, registry=REG).rclass is expected
