"""Run the language-neutral conformance vectors (conformance/vectors.json) against rclass.

Any other implementation of the specification should pass the same file; see docs/conformance.md.
"""

import copy
import json
from pathlib import Path

import pytest

from rclass import RecordError, Registry, classify, validate_record

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "conformance" / "vectors.json").read_text())


def _registry(case):
    reg = Registry()
    for name in case["registry"]:
        reg.add(copy.deepcopy(VECTORS["records"][name]), name)
    for o in case["overrides"]:
        reg.add_override(copy.deepcopy(o["record"]), o["reason"])
    return reg


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["id"])
def test_case(case):
    call, expect = case["call"], case["expect"]
    c = classify(
        call["server"], call["tool"], call.get("arguments"),
        registry=_registry(case),
        annotations=call.get("annotations"),
        trusted_server=call.get("trusted_server", False),
    )
    assert str(c.rclass) == expect["class"], case["description"]
    assert c.source == expect["source"], case["description"]
    if "rule_index" in expect:
        assert c.rule_index == expect["rule_index"], case["description"]
    if expect.get("preimage"):
        assert c.preimage is not None
        assert str(c.unconfirmed().rclass) == expect["fallback"]
    else:
        assert c.preimage is None


@pytest.mark.parametrize("item", VECTORS["invalid_records"], ids=lambda i: i["id"])
def test_invalid_record_is_rejected(item):
    with pytest.raises(RecordError):
        validate_record(copy.deepcopy(item["record"]))


def test_vector_records_are_valid():
    for name, rec in VECTORS["records"].items():
        validate_record(copy.deepcopy(rec), name)
