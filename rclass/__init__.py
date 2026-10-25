"""Reference implementation of the Reversibility Classes specification (R0-R4)."""

from .classify import classify, from_mcp_annotations
from .model import Classification, RClass
from .policy import DEFAULT_POLICY, Decision, Session
from .registry import RecordError, Registry, validate_record

__all__ = [
    "Classification",
    "DEFAULT_POLICY",
    "Decision",
    "RClass",
    "RecordError",
    "Registry",
    "Session",
    "classify",
    "from_mcp_annotations",
    "validate_record",
]

__version__ = "0.4.1"
