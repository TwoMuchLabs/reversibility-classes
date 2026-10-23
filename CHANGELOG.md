# Changelog

## v0.1.0 — 2026-10-23

First public draft of **Reversibility Classes for Agent Actions**: a shared vocabulary for one question, *if your AI agent gets this wrong, can you undo it?*

**What's in v0.1.0**
- **[SPEC.md](https://github.com/waghmaredb/reversibility-classes/blob/main/SPEC.md):** five classes (R0 no effect → R4 irreversible), argument-conditional rules, fail-closed default for unknown tools, a conservative mapping from MCP tool annotations, a default autonomy policy, and a minimum for session aggregation.
- **Registry:** 20 MCP servers, 536 tools. Every tool name is verified against the server's README or source at a pinned commit, and every class has a one-sentence rationale.
- **`rclass`:** a Python reference classifier, a CLI (`rclass lookup`, `validate`, `stats`) and a ~40-line gateway example.

**Audited before release.** An independent review found 20 under-classifications; all are fixed and covered by regression tests. 23 judgment calls are published as [open questions](https://github.com/waghmaredb/reversibility-classes/blob/main/docs/open-classification-questions.md).

**Install:** `pip install git+https://github.com/waghmaredb/reversibility-classes@v0.1.0`

This is a draft for comment. Disagreements are the most useful contribution: [open a classification dispute](https://github.com/waghmaredb/reversibility-classes/issues/new?template=classification-dispute.yml).
