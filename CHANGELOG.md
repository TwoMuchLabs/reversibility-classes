# Changelog

## v0.8.0 — 2026-10-26

**R2 budgets: fewer prompts for calls you can undo at a known cost.** The red team's main prompt-fatigue finding (H5): every R2 call asked, although SPEC 8 says R2 means "ask, or act within a pre-approved budget". No specification change.

- **A budget file** (`rclass/budgets.py`), kept where agents can't write: `--budgets FILE` on `hook` and `policy --format claude-hook`, else `RCLASS_BUDGETS`, else `/etc/rclass/budgets.yaml`. Each budget names a registry record, optionally its tools, a call count, a window (`30m`, `12h`, `1d`, or per session), a scope (each session, or all of an OS user's sessions), exact-match argument conditions (`where`), and an optional amount limit per call and in total, read from a named argument.
- **What a budget never covers:** R3 and R4; an unrecognised server; a call the session guard raised to R4; a server name that could be more than one record; a call whose amount argument is missing, negative or not a number. An unreadable count file allows nothing.
- **Counted before the call runs, under a lock**, so parallel calls can't overspend. When the budget is spent, the call asks as before, and a call that asks spends nothing.
- **The reason and the log say which budget allowed it** ("call 3 of 5 per day"). `rclass audit report` lists every call a budget allowed, whatever its class threshold, and whether it ran.
- **`rclass budgets check FILE`** validates the file against the registry, says what it allows in plain words, and warns when you can edit the file yourself. `rclass policy --format claude-hook --budgets FILE` checks it too, so a typo fails at install time rather than making every MCP call ask.
- **Reviewed before release** by a separate agent, which found five problems, all fixed here with a regression test each: a `NaN` amount switched off the total limit; a broader budget could pick up a call an amount-limited budget refused (now the first matching budget decides); a shell command could reset the count file without a prompt; holding a lock could stall the hook into Claude Code's timeout; and `policy --format claude-hook` didn't check the budget file.

**Security fixes, also affecting v0.7.0 and earlier:**

- **A repository could point the hook at its own files.** Claude Code passes a project's `.claude/settings.json` `env` to hooks (checked in a live session), so a repository could set `RCLASS_SERVERS` to a mapping it ships, or `RCLASS_STATE_DIR` to a folder inside itself. The hook now refuses a mapping or budget file inside the project folder and ignores a state folder there. `CLAUDE_PROJECT_DIR`, which the check relies on, is set by Claude Code after the settings `env`, so a repository can't move it (also checked live).
- **No lock waits forever.** The session state and decision log locks had no deadline, so a process holding one could stall the hook until Claude Code's timeout, after which the call runs without rclass. Each lock now gives up after 2 s and takes the strict path; the log is written anyway, and a racing line shows up in `rclass audit verify`.
- `rclass --version` reports the code that is running, not stale package metadata.
- **Tested in a live Claude Code session** (2.1.310, headless): with a budget of one `create_directory` call (raised to R2 by an override for the test), the first call ran without a prompt and the second was refused with "Needs your approval". `rclass audit verify` passed and the report showed "ran within budget 'one-directory' (call 1 of 1)".

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.8.0`

## v0.7.1 — 2026-10-26

**Tested in a live Claude Code session** (2.1.310, headless, with the MCP tools and WebFetch pre-approved so only the hook could refuse):

- `write_file` overwriting a file was refused with the hook's "R4 Irreversible (registry)" reason, and the file was unchanged.
- WebFetch after the session had read a file was refused with the session-guard reason.
- WebFetch with no earlier read went through.
- `rclass audit verify` passed on the resulting log, and `rclass audit report` showed each refusal as "not run".

**Fixed, found by that run:** Claude Code starts a session with `ToolSearch` (loading deferred tool definitions), and the session guard counted it as a read, so every WebFetch asked from the first call on. `ToolSearch`, `TodoWrite`, the task tools and plan mode no longer count as reads.

**Found, not fixed:** the archived SQLite reference server (`mcp-server-sqlite`) no longer starts with the current MCP Python SDK (`AttributeError: 'Server' object has no attribute 'list_resources'`). Its record stays, for deployments that still pin an older release.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.7.1`

## v0.7.0 — 2026-10-25

**Recognise your own servers.**

- **Server mappings** (`rclass/servers.py`). A file the platform team keeps outside any repository says which internal launcher, mirrored image, internal npm or PyPI package, `python -m` module or gateway URL runs which registry record. Without one, every call to an internal server was unrecognised (R4) and asked, which the red team called the main cause of prompt fatigue.
- Read from `--servers FILE` (on `scan`, `hook` and `policy`), else `RCLASS_SERVERS`, else `/etc/rclass/servers.yaml`. `rclass policy --format claude-hook` carries `--servers` into the hook as an absolute path.
- **The protections still apply.** A mapping never matches a config with an `env` that changes which code runs, a launcher inside the project, different arguments than the mapping lists, another index or extra packages, a command after the image, or a lookalike URL. Two mappings naming different records leave the server unrecognised. A mirrored image's tag still counts for `min_version`.
- `rclass scan` marks servers recognised by a mapping and warns when you can edit the mapping file; a mapping naming a record the registry lacks is an error (the hook asks).

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.7.0`

## v0.6.0 — 2026-10-25

**An audit log an auditor can use.**

- **Who approved what.** The hook now also runs after each MCP call, `WebFetch` and write it decides about (PostToolUse), so the log knows whether a call that asked went on to run. Decision lines add the user, host, Claude Code permission mode and a SHA-256 digest of the arguments; the arguments themselves are never logged.
- **`rclass audit report`** lists every decision at R3 or above, and everything that asked or blocked, with its outcome: approved and ran, refused or cancelled, or blocked. An approval in a mode other than the default is reported as that mode, not credited to a person. `--min-class` and `--json` are available.
- **`rclass audit verify`**: each line carries the hash of the one before it and of its own content, so a changed, removed or reordered line is reported (exit 1). This is tamper-evident on the same machine, not tamper-proof.
- Re-run `rclass policy --format claude-hook` to add the PostToolUse entry to your settings.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.6.0`

## v0.5.0 — 2026-10-25

**Specification 0.3.0: sessions and versions.**

- **Session guard** (SPEC 9). A tool with the new `egress` field sends data the caller chooses to a destination the caller chooses. Once a session has used any other tool, an egress call is R4 unless its host is allow-listed, so "read a secret, then fetch a URL carrying it" asks before the fetch. `fetch`, Playwright's `browser_navigate` and `browser_tabs`, Puppeteer's `puppeteer_navigate` and Sentry's uptime monitors are marked egress; Claude Code's own `WebFetch` is treated the same way. `rclass hook` now sees every tool (so it knows when data was read) and allows hosts with `--egress-allow`. It loads the registry only for MCP calls, adding about 0.1 s to built-in tool calls.
- **Version-scoped rules** (SPEC 5.1). The new `server.min_version` names the earliest release a record's rules were checked against. A config pinned to an older release, or to one whose version can't be read (a digest), gets the base class: rules that lower it don't apply. The GitHub record sets `min_version: 2.0.3`, the first release containing the commit it was checked against.
- **Several overrides** (SPEC 5.5): the one giving the call the highest class applies, whatever order they load in.
- **Hardened after a second review** that re-ran the first one's attacks and attacked the new code:
  - What an egress call brings back counts as a read, so fetching an internal page and then an outside one asks.
  - Allow-listing only trusts hosts every parser reads the same way (no userinfo, backslashes or non-ASCII), and every URL in a call's arguments must be allowed.
  - Server configs are fingerprinted at the session's first tool call of any kind, before an agent can rewrite them.
  - If session state can't be written, or disappears mid-session, the guard assumes the session has read data. A built-in tool writing to the hook's state, log or Claude Code's settings asks first.
  - Parallel hook runs merge their state under a lock, and session ids map to files by hash.
  - A mistyped hook flag asks instead of exiting 2, which would block every tool.
  - Endpoint claims by records with different ids make a hosted server unrecognised, as package claims already did.
- Six new conformance cases (52 in all) cover versions and overrides. Records using the new fields declare `spec_version: '0.3'`; 0.1 and 0.2 records stay valid.

**Install:** `pip install git+https://github.com/TwoMuchLabs/reversibility-classes@v0.5.0`

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
