# Reversibility Classes for Agent Actions

**Version:** 0.4.0 (draft)
**Status:** Request for comments
**Editor:** Deepak Waghmare
**License:** This specification is licensed under the [Apache License 2.0](LICENSE), which includes a patent grant to implementers. Versions published before 25 October 2026 were licensed under CC BY 4.0.

---

## 1. Abstract

AI agents now call tools that change the world: they write files, move money, merge code, delete infrastructure and send messages to people. Most permission systems decide what an agent may do by *capability* ("may use the Slack tool") or by a model's *confidence*. Neither answers the question a human actually cares about: **if this goes wrong, can it be undone?**

This specification defines five **Reversibility Classes**, R0 to R4, that describe how recoverable the effect of a single tool call is. It defines a record format for classifying tools (including classes that depend on a call's arguments), a fail-closed rule for unclassified tools, a conservative mapping from MCP tool annotations, and a default autonomy policy per class.

The goal is a shared vocabulary that gateways, agent frameworks, auditors and regulators can all use, so that "the agent may act alone on R0 and R1, and must ask before R2 and above" means the same thing everywhere.

## 2. Conventions

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT** and **MAY** are to be interpreted as described in BCP 14 ([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119), [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) when, and only when, they appear in all capitals, as shown here.

Sections marked *(informative)* explain or advise and are not requirements.

## 3. Terminology

- **Action.** A single tool invocation with concrete arguments, e.g. one MCP `tools/call`.
- **Principal.** The person or organization on whose behalf the agent acts, and who owns the systems it acts on.
- **Effect.** Any change the action causes outside the agent's own working context: to stored data, to money, to access rights, to physical state, or to what another party knows.
- **Prior state.** The state an action's effect replaces or removes, in full: the content itself, not a record that a change happened.
- **Restore.** Return the affected state to exactly the prior state. An effect is **restorable** only when all three of these hold:
  1. **Recorded.** The prior state is held in full, either by the system acted on (version history, a trash folder, snapshots the principal can read) or because it was captured before the action (Section 5.6).
  2. **Writable.** The principal can write that state back through an interface it already holds. A restoration that needs a person outside the principal, a support request, an out-of-band process, or another party's consent does not count.
  3. **Unobserved.** No party outside the principal has seen, received or acted on the changed state before it is restored.

  A record of an action is not a means of restoring it. An audit log that shows a message was sent documents a disclosure; it does not reverse it. A record shows *what* the prior state was; restoration also needs a way to write it back and an effect nobody else has relied on.
- **Compensate.** Take a further, known action that leaves the world in a state the principal accepts as equivalent, at a bounded and knowable cost (a refund, a cancellation, a revert commit).
- **Disclosure.** An effect through which information reaches a party outside the principal's control. A disclosure cannot be restored: deleting a sent message does not un-send its notification. An action is a disclosure when its *purpose* is to deliver content to people (a message, comment, email, issue, review or publication) or to place content where people outside the principal can read it (a public repository, a public page). A state change that only *incidentally* notifies someone (assigning a ticket, changing a status, adding a label) is classified by the recoverability of the state change, not as a disclosure. Distinguishing internal from external audiences is an open question (Section 12).
- **Classifier.** Any component (gateway, proxy, framework, library) that assigns a Reversibility Class to an action before it executes.

## 4. The five classes

A Reversibility Class describes the **worst plausible recoverable state** after an action completes as intended. It does not describe how likely the action is to be wrong.

| Class | Name | Test | Typical examples |
|---|---|---|---|
| **R0** | No effect | The action has no effect outside the agent's working context. | Read a file, run a `SELECT`, search, fetch a public page, get the time. |
| **R1** | Restorable | The effect is restorable (Section 3): the prior state is recorded in full, the principal can write it back through an interface it already holds, and no outside party has observed or relied on the change. | Edit a file whose current version is committed to version control, create a branch, save a draft, create a private note. |
| **R2** | Compensable | The prior state cannot be restored, but a known compensating action exists at bounded cost. | Charge a card in error (refund it), make a cancellable booking (cancel, paying any fee), merge a PR (revert it; any CI it triggered has already run), create a paid cloud resource (delete it; cost already incurred). |
| **R3** | Hard to recover | Recovery needs a human, an out-of-band process, or unbounded effort, including when the prior state is recorded but the principal cannot write it back itself; or the action changes access rights, credentials, availability or physical state. | Change permissions, rotate a secret, delete a running deployment, force-push, apply infrastructure changes, unlock a door. |
| **R4** | Irreversible | The effect cannot be restored or compensated: a disclosure, destruction of data with no recovery path, or an external consequence that cannot be recalled. | Send a message or email, publish or post publicly, share data with a third party, delete data with no backup, transfer funds with no recall. |

### 4.1 Classification rules

1. **Highest applicable class wins.** If an action meets the test for more than one class, it **MUST** be assigned the highest.
2. **Disclosure is R4.** Any action whose effect includes a disclosure **MUST** be classified R4, even if the stored artifact can later be deleted.
3. **Class is a property of the action type, declared ahead of time.** A classifier **MUST** be able to assign a class from the tool identity and arguments alone, without asking a model to reason about the call at runtime. Model reasoning **MAY** be used to *raise* a class, never to lower it.
4. **Arguments can change the class.** Where a tool's effect depends on its arguments (a SQL statement, a recipient, a `force` flag, a target namespace), the record **SHOULD** use conditional rules (Section 5.3).
5. **Intended effect, not failure mode.** Classes describe the action succeeding as requested. Side effects of a tool *malfunctioning* are out of scope for this version.
6. **The principal's environment matters, and is declared, not guessed.** A deployment **MAY** lower a class for its own environment (e.g. it has point-in-time database backups, so `DROP TABLE` is R3 rather than R4) by publishing an override (Section 5.5). It **MUST NOT** lower the class of a tool marked as a disclosure.

### 4.2 Why not severity? *(informative)*

Severity asks "how bad is it if this goes wrong?" and needs a judgment about the specific situation. Reversibility asks "can we get back?" and is mostly a fact about the action type. The two diverge in useful ways: charging a customer $40,000 by mistake is expensive but compensable (R2), because a refund makes the customer whole at a known cost, while a one-line Slack message to the wrong channel is cheap but permanent (R4). Autonomy policies built on reversibility can be enforced without a model in the decision path.

### 4.3 Recorded is not restorable *(informative)*

Most systems record far more than they let anyone restore. The three conditions in Section 3 separate the cases:

| Recorded in full? | Principal can write it back? | Seen or relied on outside? | Class | Example |
|---|---|---|---|---|
| yes | yes | no | R1 | Editing a document with version history; `write_file` behind a gateway that snapshots the file first (Section 5.6) |
| yes | no, needs a human or special tooling | no | R3 | `git_add` over a staged change: the old version survives as an unreachable object until garbage collection, and recovering it needs `git fsck` |
| yes | yes | yes | R4 | A message to the wrong channel: the workspace logs it and the sender can delete it, but readers have already seen it |
| no | — | — | R2, R3 or R4 | An overwrite with no history: R2 if a known compensation exists, R3 or R4 otherwise |

The first row is what makes this vocabulary a design tool and not only a label. Capturing the prior state before an action is often cheap, and it changes the class of the action, and with it what the agent may do alone.

## 5. Classification records

A **classification record** assigns classes to the tools of one tool server. Records are published as YAML or JSON and **MUST** validate against [`schema/rclass.schema.json`](schema/rclass.schema.json).

### 5.1 Server fields

| Field | Required | Meaning |
|---|---|---|
| `spec_version` | yes | The version of this specification the record targets, e.g. `"0.3"`. Records targeting `"0.1"`, `"0.2"` or `"0.3"` remain valid. |
| `server.id` | yes | A stable short identifier, e.g. `filesystem`, `github`. |
| `server.name` | yes | Human-readable name. |
| `server.source` | yes | URL of the server's repository or documentation. |
| `server.evidence` | yes | URL(s) of the exact page(s) the tool list was verified against. |
| `server.version` | no | Version, tag or commit the record was verified against. |
| `server.status` | no | `active` or `archived`. |
| `server.notes` | no | Anything a reviewer should know, e.g. a read-only mode the server enforces. |
| `server.packages` | no | How people install the server, without versions: npm or PyPI package names, or container images (e.g. `@modelcontextprotocol/server-filesystem`, `mcp-server-git`, `ghcr.io/github/github-mcp-server`). Lets tools recognise the server in a client's configuration. |
| `server.endpoints` | no | URLs of hosted deployments that expose the same tool set (e.g. `https://mcp.stripe.com`). |
| `server.min_version` | no | The earliest release of the server the record's `when` rules were checked against, e.g. `"2.0.3"`. When a deployment runs a pinned release older than this, or a pinned release whose version cannot be read (such as a container digest), a classifier **MUST NOT** apply rules that lower a class below the tool's base class. An unpinned deployment runs the newest release and is not older. |
| `default_class` | no | Class for tools of this server not listed in `tools`. If absent, the fail-closed rule (Section 6) applies. |
| `tools` | yes | List of tool entries. |

### 5.2 Tool fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Exact tool name as exposed by the server. |
| `class` | yes | `R0`–`R4`. The class when no conditional rule matches. This **MUST** be the highest class the tool can reach unless `when` rules cover every case. |
| `rationale` | yes | One sentence: why this class. |
| `disclosure` | no | `true` if the tool's effect includes a disclosure (Section 4.1, rule 2). Overrides can never lower such a tool. |
| `egress` | no | `true` if the tool sends content the caller chooses to a destination the caller chooses, such as a URL or host (Section 9). A tool that sends queries only to its own provider, such as a search API, is not egress. |
| `when` | no | Ordered list of conditional rules (Section 5.3). |
| `compensation` | for R1/R2 | The tool(s) or process that restore or compensate, e.g. `create_refund` for a card charge. An R1 or R2 entry (including one reached through a `when` rule) **SHOULD** name it; the open registry requires it. The compensation **MUST** reverse *this* action and **MUST NOT** depend on the consent of a party outside the principal (this restates the definitions of R1 and R2 in Section 3). |
| `undo_window` | no | ISO 8601 duration during which restoration or compensation remains possible, e.g. `P180D`. |
| `review` | no | `draft` (machine-proposed), `reviewed` (checked by one person), or `verified` (checked by two people or by the server's maintainer). Default `draft`. |

### 5.3 Conditional rules

A `when` rule lowers or raises a tool's class for specific arguments. Rules are evaluated in order; the **first** matching rule sets the class. If no rule matches, the tool's base `class` applies.

```yaml
- name: execute_sql
  class: R4            # base: an arbitrary statement may destroy data
  rationale: Executes arbitrary SQL against the database.
  when:
    - arg: query
      matches: '^\s*SELECT\b[^;]*;?\s*$'
      class: R0
      rationale: A single SELECT statement.
    # No rule for INSERT: INSERT OR REPLACE and ON CONFLICT DO UPDATE overwrite rows.
```

*(informative)* Pattern-matching SQL is weak evidence. A `SELECT` can call functions with side effects, a `WITH` clause can hide a `DELETE`, an `INSERT` can be an upsert, and a server that wraps a query in a read-only transaction can be escaped by a query that begins with `COMMIT;` if the driver accepts several statements. A registry **SHOULD** lower SQL only to R0, only for a single statement (no `;` except a trailing one), and only when the server also enforces read-only execution, and say so in the rationale.

Supported matchers:

| Matcher | Meaning |
|---|---|
| `equals` | Argument equals the given JSON value. |
| `in` | Argument equals one of the listed values. |
| `matches` | Argument, as a string, matches the given regular expression (case-insensitive, Python `re` / ECMAScript compatible subset). |
| `absent` | `true` if the argument is not present. |

A rule **MAY** combine several argument conditions under `all:`; all must hold.

A tool marked `disclosure: true` is classified by its worst case. A conditional rule **MAY** lower it only for argument values that verifiably do not disclose (for example, creating a repository with `private: true`, or a server default the record cites). Deployment overrides can never lower it (Section 5.5).

**Safety requirement.** A conditional rule that *lowers* a class below the base **MUST** match a positive, narrowly defined safe form (an allow-list), never a negative one ("does not contain `DROP`"). Classifiers **MUST** fall back to the base class when an argument is missing, malformed, or of an unexpected type.

### 5.4 Registry

A **registry** is a collection of classification records. This repository maintains an open registry under [`registry/`](registry/). Anyone **MAY** maintain another. Where registries disagree, a classifier **SHOULD** use the highest class.

### 5.5 Deployment overrides

A deployment **MAY** publish an override record with the same shape, scoped to its environment, to raise any class or to lower a non-disclosure class with a stated reason (e.g. "point-in-time backups with 30-day retention"). Overrides **MUST** be logged with every decision they affect.

Some tools are disclosures only because their destination *may* be public: a repository, a wiki page. An override **MAY** lower such a tool only by setting `disclosure: false` on its entry, which asserts that in this deployment the destination is readable by no one outside the principal (for example, "this agent can only reach private repositories"). The assertion and its reason **MUST** be logged with every decision. Without that explicit assertion, an override **MUST NOT** lower a disclosure tool.

When several overrides cover the same tool (for example, one from a platform team and one from a product team), a classifier **MUST** apply the one that gives the call the highest class. An override cannot replace another by being loaded later.

An override is a decision someone made about risk, so it can say who made it and for how long, in its `server` fields:

| Field | Meaning |
|---|---|
| `owner` | Who is accountable for the override, e.g. a team. |
| `approved_by` | Who approved it. |
| `expires` | The last day it may lower a class, as `YYYY-MM-DD`. |
| `environments` | The environments it applies in, e.g. `["dev", "staging"]`. |

- An override with `environments` **MUST NOT** apply unless the deployment declares one of those environments. A deployment that declares none gets no environment-scoped overrides.
- After its `expires` date, an override **MUST NOT** lower a class. It **MAY** still raise one.
- A deployment **MAY** require governed overrides: an override that lacks `owner`, `approved_by` or `expires` then **MUST NOT** lower a class.
- A classifier **MUST** log an override's `owner`, `approved_by` and `expires`, when present, with every decision the override affects, alongside its reason.

### 5.6 Pre-image capture

A deployment **MAY** make an action restorable by capturing its prior state (its *pre-image*) before each call: a gateway reads the file before it is overwritten, dumps the rows before a `DELETE`, or snapshots a configuration before it changes. It declares this in an override entry with a `preimage` object:

```yaml
- name: write_file
  class: R1
  rationale: The gateway copies the file's current content before every write.
  preimage:
    captures: full content and mode of the file at `path`, or that it did not exist
    restore: copy the captured file back with the gateway's restore command
    retention: P30D
```

The override is subject to every rule in Section 5.5, and also:

1. **Capture before execution.** The gateway **MUST** capture the pre-image and confirm the capture succeeded before it executes the call. If the capture fails or cannot be confirmed, the classifier **MUST** use the class the action would have without this override.
2. **R1 only.** An entry with `preimage` **MUST** have class R1 and no rule that sets another class. Capturing state is a claim of restorability, nothing more.
3. **Not for disclosure.** Capturing state does not un-disclose anything. An entry with `preimage` **MUST NOT** be a disclosure, and `preimage` **MUST NOT** be used to lower a disclosure tool.
4. **Only the state captured.** The override **MUST NOT** be declared for a tool whose effect can reach beyond the captured state: other files, money, access rights, triggered automation, or other parties. Where that depends on arguments, the override **SHOULD** use `when` rules to confine itself to argument forms whose effect the capture fully covers.
5. **Recorded with every decision.** A reference to the captured pre-image **MUST** be logged with the action. Restoration is possible only within `retention`; after that, the action's effect is no longer restorable.

The open registry describes tools as they come, so its records **MUST NOT** contain `preimage`.

## 6. Unclassified tools: fail closed

If a classifier has no record for a tool, it **MUST** treat the action as **R4**, unless it applies the annotation mapping in Section 7 to annotations from a server the deployment has explicitly marked as trusted.

## 7. Mapping from MCP tool annotations

The [Model Context Protocol](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) lets servers attach behavioral hints to tools (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). The MCP specification requires clients to treat these as untrusted unless the server is trusted. They are also too coarse to express compensability or disclosure.

A classifier **MAY** derive a provisional class from annotations of a trusted server using this table, and **MUST NOT** use it for untrusted servers. The first matching row applies. Absent hints take their MCP defaults (`readOnlyHint=false`, `destructiveHint=true`, `openWorldHint=true`).

| `readOnlyHint` | `destructiveHint` | `openWorldHint` | Provisional class |
|---|---|---|---|
| `true` | any | any | **R0** |
| `false` | any | `true` | **R4** (writes to an open world may be disclosure) |
| `false` | `false` | `false` | **R1** |
| `false` | `true` | `false` | **R3** |

A registry record, where one exists, **MUST** take precedence over this mapping. *(informative)* The mapping deliberately never yields R2: compensability is exactly the distinction the hints cannot carry, and the main reason this specification exists.

## 8. Default autonomy policy *(informative)*

Classes are a vocabulary; policy is the deployment's choice. As a starting point:

| Class | Default policy |
|---|---|
| R0 | Act autonomously. |
| R1 | Act, then notify. |
| R2 | Ask for approval, or act within a pre-approved budget (amount, count, time). |
| R3 | Ask for explicit approval for each instance. |
| R4 | Never act autonomously. Require approval; for high-stakes disclosures, require a second principal. |

Trust **SHOULD** be earned per class. A record of reliable R0 behavior is evidence for promoting R1 autonomy; it is never evidence for R4.

## 9. Aggregation

Single-action classes are necessary but not sufficient. A sequence of low-class actions can combine into a high-class outcome: reading a private document (R0) and then posting a summary to a public channel (R4) is a disclosure of that document.

A classifier **MUST** at minimum:

1. Record the class of every action alongside the action in its audit log.
2. Treat a session's effective class as the highest class of any action it has taken so far.

An outbound read request (for example an HTTP `GET`, or navigating a browser) is classified by its intended effect, which is usually R0. The information the request carries to the remote party, such as data encoded in a URL, is the most common exfiltration channel and is an aggregation concern: it is dangerous only in combination with what the session has already read.

**Egress after a read.** A classifier that tracks sessions **SHOULD** treat an `egress` action as R4 once the session has performed any action that is not itself egress, unless the action's destination is on an allow-list the principal configured. Without this rule, reading a secret (R0) and then requesting a URL that carries it (R0) is a disclosure no single class records. The rule is deliberately coarse: it treats every earlier action as a read, because a classifier usually cannot tell which data was private.

Finer pattern-level rules (for example, blocking R4 actions only in sessions that have read data labelled confidential) remain an open problem. *(informative)* This is closely related to the "lethal trifecta" of private data, untrusted content and external communication described by Simon Willison.

## 10. Audit logging *(informative)*

Logging the class, the record and rule that produced it, and the policy outcome with every action gives auditors a direct answer to "which irreversible actions did this agent take, and who approved them?". This supports record-keeping and human-oversight duties such as those in Articles 12 and 14 of the EU AI Act, and OWASP's guidance on excessive agency (LLM06). A class is not a compliance determination by itself.

## 11. Conformance

A classifier conforms to this specification (version 0.2) if it:

1. Assigns a class R0–R4 to every action before execution.
2. Applies registry records, conditional rules and overrides as specified in Sections 4–5.
3. Applies the fail-closed rule (Section 6) and uses the annotation mapping (Section 7) only for trusted servers.
4. Never assigns a class lower than the highest applicable record, except by a logged deployment override that does not concern disclosure.
5. Meets the aggregation minimum in Section 9.
6. Honors a `preimage` override only after confirming the capture, falls back otherwise, and logs the pre-image reference (Section 5.6).

*(informative)* The [conformance vectors](docs/conformance.md) ([`conformance/vectors.json`](conformance/vectors.json)) test items 2–4 and 6 with language-neutral cases, each tied to a section of this text. Passing them is the expected way to show that an implementation conforms. Where the vectors and this text disagree, the text governs and the disagreement is a defect in the vectors; please report it. A future minor version may make passing them a requirement.

A classification record conforms if it validates against the schema and every tool entry has a class and a rationale.

## 12. Open questions for v0.3

- **Detection window.** How long a mistake typically goes unnoticed matters as much as whether it can be undone. Should records carry a `detection_window` alongside `undo_window`?
- **Aggregation rules.** A minimal, enforceable pattern language for sequences.
- **Audience.** Should disclosure distinguish internal audiences (inside the principal's organization) from external ones?
- **Signed records.** Letting server maintainers sign their own records.
- **Upstreaming.** How these classes map onto MCP's draft `action-metadata` extension, whose `outcome` field has three values (`benign`, `consequential`, `irreversible`). A proposal defining those values by the test in Section 3, with R0 → `benign`, R1–R2 → `consequential` and R3–R4 → `irreversible`, is in [docs/proposals/mcp-action-metadata.md](docs/proposals/mcp-action-metadata.md).

Comments are welcome as GitHub issues.

## 13. Versioning and stability

This specification is versioned `MAJOR.MINOR.PATCH`, separately from the `rclass` tool.

- **PATCH** changes are editorial: wording, examples, links and clarifications that do not change what conforms.
- **MINOR** changes add or change requirements. While the major version is 0, a minor version **MAY** make changes that are not backward compatible; each one is listed in Appendix A.
- **MAJOR** version 1 will mark the class definitions and the record format as stable; after it, incompatible changes need a new major version.

A record's `spec_version` names `MAJOR.MINOR` (for example `"0.2"`). How changes are proposed and decided is described in [GOVERNANCE.md](GOVERNANCE.md).

## Appendix A. Change log

- **0.4.0** — Overrides can carry `owner`, `approved_by`, `expires` and `environments` (Section 5.5). An expired override may raise but not lower a class; an environment-scoped override applies only in the environments it names; a deployment may require all three governance fields before an override lowers a class. Records targeting 0.1–0.3 remain valid.
- **0.3.0** — Adds `server.min_version` (Section 5.1): rules that lower a class apply only to the release a record was checked against or later. Adds the `egress` tool field (Section 5.2) and a session rule treating egress after a read as R4 unless allow-listed (Section 9). When several deployment overrides cover one tool, the most restrictive result applies (Section 5.5). Records targeting 0.1 and 0.2 remain valid.
- **0.2.2** — Editorial: cites RFC 8174 (only all-capital key words are normative); restates the compensation requirement of Section 5.2 with key words, as already required by the definitions in Section 3; removes outdated "v0.1" references; points to the conformance vectors from Section 11 (informative); adds Section 13, describing how versions are numbered. No conformance requirement changes.
- **0.2.1** — Adds optional `server.packages` and `server.endpoints` to records (Section 5.1), so tools can recognise a server from how it is installed. Records keep `spec_version: '0.2'`.
- **0.2.0** — Defines *restorable* by three conditions: recorded, writable by the principal, unobserved (Section 3). Adds Section 4.3 on recorded versus restorable and Section 5.6 on pre-image capture. R1 and R2 entries name their compensation (Section 5.2).
- **0.1.0** — First public draft.
