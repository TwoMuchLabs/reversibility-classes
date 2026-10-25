"""Default autonomy policy per class (SPEC section 8) and session aggregation (section 9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .classify import classify
from .model import Classification, RClass
from .registry import Registry


class Decision(str, Enum):
    ALLOW = "allow"                    # act autonomously
    ALLOW_AND_NOTIFY = "allow_notify"  # act, then tell the principal
    APPROVE_OR_BUDGET = "approve_or_budget"  # ask, or act within a pre-approved budget
    APPROVE_EACH = "approve_each"      # explicit approval for this instance
    NEVER_AUTONOMOUS = "never_autonomous"  # approval required; maybe a second principal


DEFAULT_POLICY: dict[RClass, Decision] = {
    RClass.R0: Decision.ALLOW,
    RClass.R1: Decision.ALLOW_AND_NOTIFY,
    RClass.R2: Decision.APPROVE_OR_BUDGET,
    RClass.R3: Decision.APPROVE_EACH,
    RClass.R4: Decision.NEVER_AUTONOMOUS,
}


@dataclass
class Session:
    """Tracks one agent session: every classified action and the session's effective class.

    The effective class is the highest class of any action taken so far (SPEC 9).
    """

    registry: Registry | None = None
    policy: Mapping[RClass, Decision] = field(default_factory=lambda: dict(DEFAULT_POLICY))
    log: list[dict[str, Any]] = field(default_factory=list)
    effective: RClass = RClass.R0

    def check(
        self,
        server: str,
        tool: str,
        args: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Classification, Decision]:
        """Classify an action and return the policy decision. Does not record it."""
        c = classify(server, tool, args, registry=self.registry, **kwargs)
        return c, self.policy[c.rclass]

    def confirm_preimage(self, classification: Classification, captured: bool) -> tuple[Classification, Decision]:
        """Call after attempting the capture a classification requires (SPEC 5.6). If it failed,
        the override does not apply and the stricter class and decision are returned."""
        c = classification if captured or classification.preimage is None else classification.unconfirmed()
        return c, self.policy[c.rclass]

    def record(
        self,
        classification: Classification,
        decision: Decision,
        approved_by: str | None = None,
        preimage_ref: str | None = None,
    ) -> None:
        """Append an executed action to the audit log and update the effective class.

        An action classified through pre-image capture must be recorded with a reference to the
        captured state, or it cannot be restored (SPEC 5.6)."""
        if classification.preimage is not None and not preimage_ref:
            raise ValueError(
                f"{classification.server}/{classification.tool} relies on pre-image capture; "
                "record it with preimage_ref, or record classification.unconfirmed() instead."
            )
        entry = classification.to_dict()
        entry["decision"] = decision.value
        entry["approved_by"] = approved_by
        if preimage_ref:
            entry["preimage_ref"] = preimage_ref
        self.log.append(entry)
        self.effective = max(self.effective, classification.rclass)
