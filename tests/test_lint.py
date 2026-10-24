"""Judgment lints over the registry.

Schema validation checks that records are well formed; these tests check that they are plausible.
They catch the most dangerous mistake a record can make — classing a destructive or sending tool
as harmless — by looking at the verbs in tool names. A name can mislead (`execute_range_query`
only reads), so every exception must be listed below with the reason it is safe.
"""

import re

import pytest

from rclass import RClass, Registry

REG = Registry.bundled()

# Verbs that, in a tool's name, suggest it changes, sends, runs or removes something.
RISKY_VERBS = {
    "delete", "remove", "drop", "destroy", "purge", "flush", "truncate", "wipe", "clear",
    "send", "post", "publish", "push", "reply", "comment", "share", "invite", "notify", "email",
    "merge", "exec", "execute", "eval", "evaluate", "run", "deploy", "apply", "install", "uninstall",
    "transfer", "pay", "payout", "refund", "charge", "rotate", "revoke", "grant",
    "set", "write", "update", "overwrite",
}

# Verbs whose purpose is to deliver content to people or other systems.
SENDING_VERBS = {"send", "post", "publish", "reply", "comment", "share", "invite", "email", "notify", "message"}
READING_VERBS = {"get", "list", "retrieve", "read", "search", "describe", "show", "find"}

# (server, tool) -> why the name misleads. Keep reasons specific; a reviewer should be able to check them.
R0_DESPITE_RISKY_NAME = {
    ("kubernetes", "execute_instant_query"): "Runs a read-only PromQL query.",
    ("kubernetes", "execute_range_query"): "Runs a read-only PromQL query.",
    ("notion", "API-post-search"): "Search sent as an HTTP POST; it only reads.",
    ("notion", "API-retrieve-a-comment"): "Reads comments.",
    ("terraform", "get_run_details"): "Reads a run.",
    ("terraform", "get_run_comments"): "Reads a run's comments.",
    ("terraform", "get_apply_details"): "Reads an apply.",
    ("terraform", "get_apply_logs"): "Reads an apply's logs.",
}

NOT_DISCLOSURE_DESPITE_SENDING_NAME = {
    ("github", "add_comment_to_pending_review"): "Visible only to its author until the review is submitted.",
    ("stripe", "send_money"): "Moves money (R4 on that ground); it delivers no content to people.",
}


def _tokens(name: str) -> set[str]:
    return set(re.split(r"[_\-]+", name.lower()))


ENTRIES = REG.all_entries()


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: f"{e.server}/{e.name}")
def test_risky_names_are_not_r0(entry):
    if entry.base is not RClass.R0 or not (_tokens(entry.name) & RISKY_VERBS):
        return
    assert (entry.server, entry.name) in R0_DESPITE_RISKY_NAME, (
        f"{entry.server}/{entry.name} is R0 but its name suggests it changes something. "
        "Raise its class, or add it to R0_DESPITE_RISKY_NAME with the reason it only reads."
    )


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: f"{e.server}/{e.name}")
def test_sending_names_are_disclosures(entry):
    toks = _tokens(entry.name)
    if not (toks & SENDING_VERBS) or (toks & READING_VERBS) or entry.disclosure:
        return
    assert (entry.server, entry.name) in NOT_DISCLOSURE_DESPITE_SENDING_NAME, (
        f"{entry.server}/{entry.name} sounds like it delivers content to others but is not marked "
        "disclosure: true. Mark it, or add it to NOT_DISCLOSURE_DESPITE_SENDING_NAME with the reason."
    )


def test_exception_lists_have_no_stale_entries():
    """An exception for a tool that no longer exists, or no longer needs one, should be removed."""
    known = {(e.server, e.name): e for e in ENTRIES}
    for key in R0_DESPITE_RISKY_NAME:
        assert key in known and known[key].base is RClass.R0, f"stale R0 exception: {key}"
    for key in NOT_DISCLOSURE_DESPITE_SENDING_NAME:
        assert key in known and not known[key].disclosure, f"stale disclosure exception: {key}"


# Classes whose definition is a claim that the action can be undone.
UNDO_CLAIM_CLASSES = {RClass.R1, RClass.R2}


def _claims_undo(entry) -> bool:
    if entry.base in UNDO_CLAIM_CLASSES:
        return True
    return any(RClass.parse(rule["class"]) in UNDO_CLAIM_CLASSES for rule in entry.raw.get("when", []))


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: f"{e.server}/{e.name}")
def test_undo_claims_name_their_undo(entry):
    """R1 and R2 say "this can be undone". Saying so without naming how is how a wrong-direction
    undo slips through (a refund is not undone by a refund). Name the operation, so a reviewer can
    check that it exists, that it reverses *this* action, and that no outside party must agree."""
    if not _claims_undo(entry):
        return
    comp = str(entry.raw.get("compensation", "")).strip()
    assert comp, (
        f"{entry.server}/{entry.name} is classed R1/R2 (directly or by a `when` rule) but has no "
        "`compensation`. Name the operation that restores or compensates it, or raise the class."
    )
    assert comp.lower() != entry.name.lower(), (
        f"{entry.server}/{entry.name} names itself as its own compensation; name the reversing operation."
    )
