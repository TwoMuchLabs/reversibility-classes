import copy

import pytest

from rclass import (
    DEFAULT_POLICY,
    Decision,
    RClass,
    RecordError,
    Registry,
    Session,
    classify,
    from_mcp_annotations,
    validate_record,
)

RECORD = {
    "spec_version": "0.1",
    "server": {"id": "db", "name": "Test DB", "source": "https://example.com", "evidence": ["https://example.com"]},
    "tools": [
        {"name": "read", "class": "R0", "rationale": "Reads."},
        {
            "name": "execute_sql",
            "class": "R4",
            "rationale": "Arbitrary SQL.",
            "when": [
                {"arg": "query", "matches": r"^\s*SELECT\b[^;]*;?\s*$", "class": "R0", "rationale": "One SELECT."},
                {"arg": "query", "matches": r"^\s*INSERT\b[^;]*;?\s*$", "class": "R1", "rationale": "One INSERT."},
            ],
        },
        {
            "name": "deploy",
            "class": "R3",
            "rationale": "Deploys.",
            "when": [
                {
                    "all": [
                        {"arg": "env", "in": ["dev", "test"]},
                        {"arg": "options.force", "absent": True},
                    ],
                    "class": "R1",
                    "rationale": "Non-forced deploy to a scratch environment.",
                }
            ],
        },
        {"name": "send", "class": "R4", "disclosure": True, "rationale": "Sends a message."},
        {"name": "wipe", "class": "R4", "rationale": "Deletes with no backup."},
    ],
}


@pytest.fixture
def reg():
    r = Registry()
    r.add(copy.deepcopy(RECORD))
    return r


def test_base_class(reg):
    c = classify("db", "read", registry=reg)
    assert c.rclass is RClass.R0 and c.source == "registry"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("SELECT * FROM t", RClass.R0),
        ("  select id from t;", RClass.R0),
        ("INSERT INTO t VALUES (1)", RClass.R1),
        ("UPDATE t SET a = 1", RClass.R4),
        ("DELETE FROM t", RClass.R4),
        ("SELECT 1; DROP TABLE t", RClass.R4),  # second statement defeats the allow-list
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", RClass.R4),
    ],
)
def test_conditional_sql(reg, query, expected):
    assert classify("db", "execute_sql", {"query": query}, registry=reg).rclass is expected


@pytest.mark.parametrize("args", [None, {}, {"query": None}, {"query": ["SELECT 1"]}, {"query": True}])
def test_missing_or_malformed_args_fall_back_to_base(reg, args):
    assert classify("db", "execute_sql", args, registry=reg).rclass is RClass.R4


def test_all_conditions_and_nested_args(reg):
    assert classify("db", "deploy", {"env": "dev"}, registry=reg).rclass is RClass.R1
    assert classify("db", "deploy", {"env": "dev", "options": {"force": True}}, registry=reg).rclass is RClass.R3
    assert classify("db", "deploy", {"env": "prod"}, registry=reg).rclass is RClass.R3


def test_unclassified_tool_fails_closed(reg):
    c = classify("db", "unknown", registry=reg)
    assert c.rclass is RClass.R4 and c.source == "fail-closed"


def test_annotations_ignored_for_untrusted_server(reg):
    c = classify("other", "t", annotations={"readOnlyHint": True}, registry=reg)
    assert c.rclass is RClass.R4 and c.source == "fail-closed"


def test_annotations_used_for_trusted_server(reg):
    c = classify("other", "t", annotations={"readOnlyHint": True}, trusted_server=True, registry=reg)
    assert c.rclass is RClass.R0 and c.source == "annotations"


@pytest.mark.parametrize(
    "hints, expected",
    [
        ({"readOnlyHint": True}, RClass.R0),
        ({}, RClass.R4),  # MCP defaults: not read-only, destructive, open world
        ({"readOnlyHint": False, "openWorldHint": True}, RClass.R4),
        ({"destructiveHint": False, "openWorldHint": False}, RClass.R1),
        ({"destructiveHint": True, "openWorldHint": False}, RClass.R3),
    ],
)
def test_annotation_mapping(hints, expected):
    assert from_mcp_annotations(hints) is expected


def test_registry_beats_annotations(reg):
    c = classify("db", "wipe", annotations={"readOnlyHint": True}, trusted_server=True, registry=reg)
    assert c.rclass is RClass.R4


def test_highest_class_wins_across_registries(reg):
    other = copy.deepcopy(RECORD)
    other["tools"] = [{"name": "read", "class": "R2", "rationale": "Disagrees."}]
    reg.add(other)
    assert classify("db", "read", registry=reg).rclass is RClass.R2


def _override(tools):
    rec = copy.deepcopy(RECORD)
    rec["tools"] = tools
    return rec


def test_override_can_lower_non_disclosure(reg):
    reg.add_override(_override([{"name": "wipe", "class": "R3", "rationale": "Restorable."}]), "nightly snapshots")
    c = classify("db", "wipe", registry=reg)
    assert c.rclass is RClass.R3 and c.source == "override" and "nightly snapshots" in c.rationale


def test_override_cannot_lower_disclosure(reg):
    reg.add_override(_override([{"name": "send", "class": "R1", "rationale": "Trust me."}]), "please")
    assert classify("db", "send", registry=reg).rclass is RClass.R4


def test_override_can_lower_disclosure_only_with_explicit_assertion(reg):
    reg.add_override(
        _override([{"name": "send", "class": "R1", "disclosure": False, "rationale": "Internal only."}]),
        "agent can only reach a private sandbox",
    )
    c = classify("db", "send", registry=reg)
    assert c.rclass is RClass.R1 and c.source == "override"


def test_override_can_raise(reg):
    reg.add_override(_override([{"name": "read", "class": "R2", "rationale": "Sensitive."}]), "PII table")
    assert classify("db", "read", registry=reg).rclass is RClass.R2


def test_override_for_tool_not_in_registry(reg):
    reg.add_override(_override([{"name": "internal", "class": "R1", "rationale": "Ours."}]), "in-house tool")
    assert classify("db", "internal", registry=reg).rclass is RClass.R1


def test_session_aggregation(reg):
    s = Session(registry=reg)
    c, d = s.check("db", "read")
    assert d is Decision.ALLOW
    s.record(c, d)
    c, d = s.check("db", "send")
    assert d is Decision.NEVER_AUTONOMOUS
    s.record(c, d, approved_by="alice")
    assert s.effective is RClass.R4
    assert [e["class"] for e in s.log] == ["R0", "R4"] and s.log[1]["approved_by"] == "alice"


def test_default_policy_covers_every_class():
    assert set(DEFAULT_POLICY) == set(RClass)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r["tools"].append({"name": "read", "class": "R0", "rationale": "dup"}), "duplicate"),
        (lambda r: r["tools"][0].update({"class": "R9"}), "R9"),
        (lambda r: r["tools"][3].update({"class": "R2"}), "disclosure"),
        (lambda r: r["tools"][0].pop("rationale"), "rationale"),
    ],
)
def test_invalid_records_rejected(mutate, message):
    rec = copy.deepcopy(RECORD)
    mutate(rec)
    with pytest.raises(RecordError, match=message):
        validate_record(rec)
