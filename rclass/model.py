"""Core types: the five Reversibility Classes and a classification result."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class RClass(IntEnum):
    """Reversibility Class of a single agent action. Higher means harder to undo."""

    R0 = 0  # No effect outside the agent's working context
    R1 = 1  # Restorable with ordinary effort; nobody outside observed it
    R2 = 2  # Compensable at bounded cost
    R3 = 3  # Hard to recover; access, credentials, availability or physical state
    R4 = 4  # Irreversible: disclosure, unrecoverable destruction, unrecallable consequence

    @classmethod
    def parse(cls, value: "str | int | RClass") -> "RClass":
        if isinstance(value, RClass):
            return value
        if isinstance(value, int):
            return cls(value)
        text = str(value).strip().upper()
        if text in cls.__members__:
            return cls[text]
        raise ValueError(f"not a Reversibility Class: {value!r}")

    def __str__(self) -> str:  # "R2" rather than "RClass.R2"
        return self.name

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    RClass.R0: "No effect",
    RClass.R1: "Restorable",
    RClass.R2: "Compensable",
    RClass.R3: "Hard to recover",
    RClass.R4: "Irreversible",
}


@dataclass(frozen=True)
class Classification:
    """The class assigned to one action, and why."""

    rclass: RClass
    source: str  # "registry", "rule", "override", "annotations", or "fail-closed"
    rationale: str
    server: str
    tool: str
    rule_index: int | None = None  # index of the matching `when` rule, if any

    def to_dict(self) -> dict:
        return {
            "class": str(self.rclass),
            "label": self.rclass.label,
            "source": self.source,
            "rationale": self.rationale,
            "server": self.server,
            "tool": self.tool,
            "rule_index": self.rule_index,
        }
