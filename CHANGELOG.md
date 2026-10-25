# Changelog

## v0.4.2 — 2026-10-25

**Fixes from an enterprise red-team review.** Each reproduced case has a regression test.

- **Registry: `github/create_repository` without `private` is R4 again.** The rule that lowered it to R1 relied on a default the server only sets in recent releases; in v0.10.0 the server sends no default and GitHub creates a public repository. Registry classes still apply to every version of a server; version ranges are planned.
- **The hook never runs a call ungated because of its own error.** An unreadable registry or a bad `--map` now answers "ask" with the reason, instead of exiting with a code Claude Code treats as "no decision". The generated settings run a fixed interpreter (`python -P -m rclass`, new) with a 30-second timeout, so a missing `rclass` on PATH no longer disables the hook, and `-P` keeps a repository's own `rclass/` folder from running in its place.
- **Server identity is harder to spoof.** The hook reads configs from Claude Code's project folder (`CLAUDE_PROJECT_DIR`), not from the agent's current directory. It fingerprints each server's config the first time a session uses it; if the config changes mid-session, that server is unrecognised (R4) for the rest of the session. `--map` vouches only for servers in your own user config, never for a repository's `.mcp.json`.
- **Added records and overrides can no longer lower a class by accident.** Two records with different ids claiming the same package make the server unrecognised (extend a record under its own `server.id` instead). Several overrides for one tool are all kept and the strictest applies to each call; previously the last file loaded silently won.
- **Decision log on by default**, at `~/.local/state/rclass/audit.jsonl`: class, rule, the session's effective class (SPEC 9) and an override's reason; never arguments. `--no-log` turns it off.
- **README** leads with the specification and registry as inputs to central enforcement, and lists the hook's [limits](README.md#limits).

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.4.2`

## v0.4.1 — 2026-10-25

**Ready for review by others.**

- **Governance and community files**: [GOVERNANCE.md](GOVERNANCE.md) (an editor model, how each kind of change is decided, a 7-day comment period for normative specification changes, and the path to multi-organisation governance), [MAINTAINERS.md](MAINTAINERS.md), [SECURITY.md](SECURITY.md) with private reporting, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1), [ADOPTERS.md](ADOPTERS.md) and CODEOWNERS.
- **Specification 0.2.2 (editorial)**: cites RFC 8174, restates the compensation requirement with key words, removes outdated "v0.1" references, points to the conformance vectors, and adds a section on how versions are numbered. No conformance requirement changes.
- **Proposal to MCP**: [docs/proposals/mcp-action-metadata.md](docs/proposals/mcp-action-metadata.md) and a patch for MCP's draft `action-metadata` extension that defines `outcome` by recoverability and adds an optional `outcomeRef` for finer schemes such as R0–R4. Not yet submitted.
- **Supply chain**: every workflow action is pinned to a commit; Dependabot, CodeQL and the OpenSSF Scorecard run on the repository; `ruff` runs in CI and CI validates the registry with `--strict`. Releases now attach a wheel, an sdist, `SHA256SUMS` and signed build provenance, and can publish to PyPI through Trusted Publishing once it is enabled.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.4.1`

## v0.4.0 — 2026-10-25

**Enforce it.**

- **`rclass hook claude-code`**: a Claude Code PreToolUse hook that classifies each MCP tool call with its arguments and answers allow, ask or deny. By default R0–R1 calls get no decision and R2–R4 always ask; `--allow-up-to R1` and `--deny-from R4` change that, and `--log` writes one JSON line per decision with the class and rule, never the arguments. Servers are identified from what the Claude Code config runs; unrecognised servers and tools are R4; when a name exists in several scopes the strictest record wins; a failure asks instead of allowing. Handles Claude Code's spelling of server names (`my.server` becomes `my_server`) and names containing `__`.
- **`rclass policy --format claude-hook | claude-settings | cedar | cel`**: writes enforceable policy from an MCP client config without starting anything. Cedar for Docker MCP governance (R2–R4 as `@requireApproval`), CEL rules for agentgateway (only R0–R1 allowed, since it has no approval step), and Claude Code permission rules. Static formats use each tool's worst-case class, so they are never less strict than the hook; a test checks this.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.4.0`

## v0.3.1 — 2026-10-25

**The specification is now Apache 2.0.**

- **Licensing.** The specification, schema and conformance vectors move from CC BY 4.0 to the [Apache License 2.0](LICENSE), the same license as the code. Apache 2.0 includes a patent grant to everyone who implements the specification; CC BY 4.0 grants no patent rights. This matches the Model Context Protocol, which licenses its specification under Apache 2.0. The registry data and other documentation stay under CC BY 4.0. Versions published before this release remain available under CC BY 4.0. Details: [LICENSE-SPEC-AND-DATA.md](LICENSE-SPEC-AND-DATA.md).
- **Developer Certificate of Origin.** Every commit in a pull request must now be signed off (`git commit -s`); a check enforces it. See [CONTRIBUTING.md](CONTRIBUTING.md#sign-off-developer-certificate-of-origin).
- **Least-privilege workflows.** The drift check, which runs the latest releases of third-party MCP servers, now holds no write token and leaves no credentials on disk; a separate job files the issue. CI and release default to read-only tokens.
- The drift check opens its issue when a server cannot be checked, instead of passing.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.3.1`

## v0.3.0 — 2026-10-25

**Check your own agent.**

- **`rclass scan`** reads an MCP client configuration (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf), starts each local server, lists its tools and reports them by class, counting each tool at the worst class its arguments can reach. It went through two rounds of independent review, by a tester acting as a first-time user and as an attacker. Every finding from both rounds is fixed and has a regression test (two known limits are documented in the README: version drift is invisible to `--list`, and a process that detaches into its own session can outlive a scan):
  - Unknown tools and servers count as R4. A server is recognised only when its config runs a registry package or image as itself, or points at its hosted URL; `npx -p`, `uvx --from` with another command, docker entrypoint overrides, custom registries, project-local launchers and code-changing environment variables (`PATH`, `NODE_OPTIONS`, …) make it unrecognised. Tool names alone never match; `--assume` maps a started server explicitly.
  - Servers get the minimal environment the official MCP SDKs pass by default, not your whole shell (`--inherit-env` to change that). A repository's own config (`.mcp.json`, `.vscode/`, `.cursor/`) is not started without consent, and Claude Code's per-project approvals are honoured.
  - Arguments, environment values and server output are not printed; `--show-server-errors` shows server output redacted.
  - Hostile or broken servers cannot crash, hang or flood the scan: replies are shape-checked, lines, stderr, pages and tools are capped, and each server runs under a hard deadline in its own process group.
  - `--list` starts nothing and uses the registry's classes, for CI. `--fail-on CLASS` is a gate with documented exit codes (0 ok, 1 threshold, 2 usage, 3 incomplete). `--json` carries a report version and timestamp. `--write-drafts DIR` writes a starting record that never overwrites an existing file, and `rclass validate --strict` rejects unfinished drafts.
- **Records say how a server is installed**: optional `server.packages` (npm or PyPI names, container images) and `server.endpoints` (hosted URLs) in SPEC 5.1 and the schema. All 20 records now carry them.
- **Conformance vectors** ([docs/conformance.md](docs/conformance.md)): 46 classification cases and 9 invalid records, language-neutral, each tied to a SPEC section, with expected results written from the spec text. They caught three planted bugs when checked.
- **Clearer errors.** A mistyped server or tool name in `rclass lookup` still fails closed to R4, and now says so and suggests close matches. `rclass validate` with no path checks the bundled registry when run outside a checkout, and an empty folder or missing path is an error (exit 2), not a pass. Unreadable or malformed record files given to `--registry`, `--override` or `validate` give a one-line reason with the line number, never a traceback; a malformed client config reports where, without echoing its contents.
- `rclass export` writes the registry as one JSON document; `--override` applies a deployment override to `lookup` and `scan`, with its reason taken from `server.notes`.
- **Weekly drift check** (`.github/workflows/drift.yml`, `scripts/check_drift.py`): starts the latest release of the 10 registry servers that list their tools without real credentials, and opens a `drift` issue when one ships tools the registry does not classify. First run: Filesystem, Git, Fetch, Memory, Time, Sequential Thinking, Notion, Brave Search and Playwright (with every capability enabled) match their records exactly; Sentry exposes most of its tools through `execute_sentry_tool`, so they cannot be listed directly.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.3.0`, then `rclass scan`.

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

**Repository**
- Moved to [TwoMuchLabs/reversibility-classes](https://github.com/TwoMuchLabs/reversibility-classes). Old `waghmaredb/reversibility-classes` URLs, including `pip install git+...` links, redirect automatically.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.2.0`

## v0.1.0 — 2026-10-23

First public draft of **Reversibility Classes for Agent Actions**: a shared vocabulary for one question, *if your AI agent gets this wrong, can you undo it?*

**What's in v0.1.0**
- **[SPEC.md](https://github.com/waghmaredb/reversibility-classes/blob/main/SPEC.md):** five classes (R0 no effect → R4 irreversible), argument-conditional rules, fail-closed default for unknown tools, a conservative mapping from MCP tool annotations, a default autonomy policy, and a minimum for session aggregation.
- **Registry:** 20 MCP servers, 536 tools. Every tool name is verified against the server's README or source at a pinned commit, and every class has a one-sentence rationale.
- **`rclass`:** a Python reference classifier, a CLI (`rclass lookup`, `validate`, `stats`) and a ~40-line gateway example.

**Audited before release.** An independent review found 20 under-classifications; all are fixed and covered by regression tests. 23 judgment calls are published as [open questions](https://github.com/waghmaredb/reversibility-classes/blob/main/docs/open-classification-questions.md).

**Install:** `pip install git+https://github.com/waghmaredb/reversibility-classes@v0.1.0`

This is a draft for comment. Disagreements are the most useful contribution: [open a classification dispute](https://github.com/waghmaredb/reversibility-classes/issues/new?template=classification-dispute.yml).
