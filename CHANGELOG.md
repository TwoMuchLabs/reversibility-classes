# Changelog

## v0.2.0 — 2026-10-25

**What "restorable" means, made precise, and a way to make actions restorable.**

**Specification**
- **Restorable is defined by three conditions** (SPEC section 3): the prior state is *recorded* in full, the principal can *write it back* through an interface it already holds, and *no outside party* has seen or relied on the change. A record alone does not make an action restorable: logging a sent message documents a disclosure, it does not reverse it. New section 4.3 shows how the three conditions separate R1, R3 and R4.
- **Pre-image capture** (SPEC 5.6): a deployment that captures the prior state before each call may declare a tool R1 with a `preimage` override. The capture must be confirmed before execution or the classifier falls back; it never applies to a disclosure or to effects beyond the captured state; its reference is logged with every action.
- **R1 and R2 entries name their undo** in `compensation` (SPEC 5.2), and that undo must reverse *this* action without outside consent.
- Corrected examples. "Refunding a $40,000 charge" was not recoverable: undoing a refund needs the customer's consent. The example is now a mistaken charge, which a refund compensates.

**Registry**
- **Every undo claim audited.** All R1 and R2 entries were checked against server source and vendor docs: does the undo exist, reverse this action, need no outside consent, leave a bounded residue? 17 tools raised to a stricter class, none lowered; 9 rules tightened; 20 undo paths corrected or added. Wrong-direction undos included `git_add`/`git_reset` (each named the other) and Terraform tag updates. Details in [open questions](docs/open-classification-questions.md#undo-audit-october-2026), which also lists six new questions.
- Class counts: R0 333, R1 31, R2 18, R3 39, R4 115 (536 tools, 20 servers).
- Records now target `spec_version: '0.2'`; `'0.1'` records still validate.

**`rclass`**
- `preimage` in the schema, with validation; `Classification.preimage`, `fallback` and `unconfirmed()`; `Session.confirm_preimage()`; `Session.record()` refuses a pre-image action without `preimage_ref`. The gateway example shows the capture step.
- New lint: every R1/R2 entry must name its undo; the open registry may not contain `preimage`.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.2.0`

## Unreleased (before v0.2.0)

- Repository moved to [TwoMuchLabs/reversibility-classes](https://github.com/TwoMuchLabs/reversibility-classes). Old `waghmaredb/reversibility-classes` URLs, including `pip install git+...` links, redirect automatically.

## v0.1.0 — 2026-10-23

First public draft of **Reversibility Classes for Agent Actions**: a shared vocabulary for one question, *if your AI agent gets this wrong, can you undo it?*

**What's in v0.1.0**
- **[SPEC.md](https://github.com/waghmaredb/reversibility-classes/blob/main/SPEC.md):** five classes (R0 no effect → R4 irreversible), argument-conditional rules, fail-closed default for unknown tools, a conservative mapping from MCP tool annotations, a default autonomy policy, and a minimum for session aggregation.
- **Registry:** 20 MCP servers, 536 tools. Every tool name is verified against the server's README or source at a pinned commit, and every class has a one-sentence rationale.
- **`rclass`:** a Python reference classifier, a CLI (`rclass lookup`, `validate`, `stats`) and a ~40-line gateway example.

**Audited before release.** An independent review found 20 under-classifications; all are fixed and covered by regression tests. 23 judgment calls are published as [open questions](https://github.com/waghmaredb/reversibility-classes/blob/main/docs/open-classification-questions.md).

**Install:** `pip install git+https://github.com/waghmaredb/reversibility-classes@v0.1.0`

This is a draft for comment. Disagreements are the most useful contribution: [open a classification dispute](https://github.com/waghmaredb/reversibility-classes/issues/new?template=classification-dispute.yml).
