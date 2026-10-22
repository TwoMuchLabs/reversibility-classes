"""Minimal example: gate MCP `tools/call` requests by Reversibility Class.

Drop `gate()` into any proxy that sees JSON-RPC traffic between an agent and an MCP
server. It classifies the call, applies the default policy, and records the decision.

    python examples/gateway_hook.py
"""

from __future__ import annotations

from rclass import Decision, Session

session = Session()  # uses the bundled open registry


def gate(server_id: str, request: dict, ask_human) -> dict | None:
    """Return None to let the call through, or a JSON-RPC error to block it."""
    if request.get("method") != "tools/call":
        return None
    params = request.get("params", {})
    classification, decision = session.check(server_id, params.get("name", ""), params.get("arguments"))

    approver = None
    if decision not in (Decision.ALLOW, Decision.ALLOW_AND_NOTIFY):
        approver = ask_human(classification)
        if approver is None:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32001,
                    "message": f"Blocked: {classification.rclass} ({classification.rclass.label}) needs approval.",
                    "data": classification.to_dict(),
                },
            }
    session.record(classification, decision, approved_by=approver)
    return None


if __name__ == "__main__":
    def deny_all(c):
        print(f"  approval requested for {c.server}/{c.tool}: {c.rclass} — {c.rationale}")
        return None

    calls = [
        ("filesystem", {"name": "read_text_file", "arguments": {"path": "notes.md"}}),
        ("git", {"name": "git_commit", "arguments": {"repo_path": ".", "message": "wip"}}),
        ("slack", {"name": "slack_post_message", "arguments": {"channel_id": "C1", "text": "Done!"}}),
    ]
    for i, (server, params) in enumerate(calls):
        req = {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": params}
        result = gate(server, req, deny_all)
        print(f"{server}/{params['name']}: {'blocked' if result else 'allowed'}")
    print("session effective class:", session.effective)
