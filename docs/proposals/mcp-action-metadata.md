# Proposal to MCP: define `outcome` by recoverability

**Status:** drafted 2026-10-25, not yet submitted.
**Target:** [`modelcontextprotocol/experimental-ext-tool-annotations`](https://github.com/modelcontextprotocol/experimental-ext-tool-annotations), file `specification/draft/action-metadata.mdx` (draft of 2026-08-12, commit `fecace7`), owned by the [Tool Annotations Interest Group](https://modelcontextprotocol.io/community/interest-groups/tool-annotations).
**Patch:** [`mcp-action-metadata-outcome.patch`](mcp-action-metadata-outcome.patch) (adds 84 lines, changes none).

MCP's draft `action-metadata` extension gives each tool an `outcome` of `benign`, `consequential` or `irreversible`, but does not define the three values. This proposal defines them with the test Reversibility Classes uses (who can put it back), requires the declared value to be the tool's worst case, and adds an optional `outcomeRef` so finer schemes such as R0–R4 can refine the three values without changing the wire format. It follows the interest group's own pattern: a coarse value every client acts on, refined behind an open `type`, as data-labelling schemes refine `sensitive`.

What the group has not decided, and this proposal does not assume: whether `outcome` stays a closed enum (its open questions list the other enums, not this one), and whether action metadata replaces or coexists with `destructiveHint`.

## How to submit

1. Post in the MCP Discord channel `#tool-annotations-ig` first (text below). The SEP guidelines expect a prior discussion in the relevant group.
2. Fork the repository, then:
   ```bash
   git clone https://github.com/<you>/experimental-ext-tool-annotations && cd experimental-ext-tool-annotations
   git checkout -b outcome-recoverability
   git apply /path/to/mcp-action-metadata-outcome.patch
   git commit -am "action-metadata: define outcome by recoverability; add outcomeRef"
   git push -u origin outcome-recoverability
   ```
3. Open the pull request with the description below, and link it in the Discord thread.

## Discord post (`#tool-annotations-ig`)

> Hi all. I maintain Reversibility Classes (https://github.com/TwoMuchLabs/reversibility-classes), an open R0–R4 scale for how recoverable a tool call is, with a third-party registry of 536 MCP tools across 20 servers (each with a rationale and a source link) and a Claude Code hook that gates calls by class.
>
> `action-metadata`'s `outcome` (benign / consequential / irreversible) looks like the natural home, so I'd like to propose a narrow change rather than a new annotation:
> 1. Define the three values by one test, who can put it back, so independent classifiers agree.
> 2. Declare the worst case (a SQL tool that accepts DROP is irreversible); lower a specific call only through Tool Resolution.
> 3. An optional `outcomeRef` with an open `type`, the way schemes refine `sensitive`, so R0–R4 or any other scheme can refine the floor without wire changes.
>
> Draft PR: <link>. I'd value a read from Sam and Robert, and I'm happy to walk through it on an IG call. On SEP-1984, reviewers said a reversibility hint wasn't clearly actionable; this one is tied to host behaviour (no prompt / proceed with an undo path / confirm), which the PR spells out.

## Pull request description

**Title:** action-metadata: a recoverability test for `outcome`, worst-case declaration, optional `outcomeRef`

**What this changes.** Additions only, to `specification/draft/action-metadata.mdx` and `docs/open-questions.md`:

- **A test for each `outcome` value.** `benign`: nothing changes outside the agent's working context. `consequential`: the principal can return to the prior state, or an equivalent one at a known and bounded cost, through interfaces it already holds and without anyone else's consent. `irreversible`: recovery needs someone outside the principal or out-of-band effort, or is impossible. Every disclosure is `irreversible`.
- **Worst-case declaration.** A tool declares the value its most consequential permitted invocation reaches. A host lowers a specific call only through a per-call mechanism such as Tool Resolution (SEP-1862). A missing value is treated as `irreversible`, matching `destructiveHint`'s default.
- **Optional `outcomeRef`** (`type`, `class`, `ref`): an open-string pointer to a finer scheme. A scheme maps each of its classes to exactly one `outcome`, a producer may not emit a class that maps lower than the declared `outcome`, and a client that does not know the scheme ignores it.
- **Two open questions**: whether `outcome` stays a closed enum and whether `consequential` should split on the wire; and whether `outcomeRef` should reuse `evidenceRef`'s shape.

**Why.** Without definitions, `consequential` and `irreversible` will be read differently by each server author, and a host cannot build policy on values that mean different things per server. The test is stated in terms a reviewer can check against the tool's code: does the system keep the prior state, can the principal write it back, does anyone outside have to agree or has anyone outside already seen it.

**What client behaviour it enables** (the first question in "Tool Annotations as Risk Vocabulary"):

| `outcome` | Default host behaviour |
| :--- | :--- |
| `benign` | Proceed without a prompt. |
| `consequential` | Proceed with a notification and an undo path, or within a pre-approved budget. |
| `irreversible` | Confirm with a human first (the recommended default for `requiresReview`). |

A host that implements a finer scheme can do better: in Reversibility Classes, R1 (exactly restorable) and R2 (compensable at a cost, such as a refund) get different defaults, and R3 (needs a human to recover, such as a rotated credential) is confirmed each time.

**Does it need trust?** Partly, which is why `outcomeRef` exists. A server's own `outcome` is a claim. A third-party scheme whose classes link to evidence can be used when the server is not trusted, matching the accountability model in `docs/trust-model.md`.

**Evidence and implementation.**
- The Reversibility Classes registry classifies 536 tools across 20 MCP servers (GitHub, Stripe, Supabase, Sentry, Terraform, Kubernetes, Playwright, Notion, the reference servers and others); each class has a one-sentence rationale and a link to the source the tool names were checked against.
- `rclass hook claude-code` is a working host-side consumer: a Claude Code PreToolUse hook that classifies each MCP call with its arguments and asks for confirmation from R2 up.
- 60 language-neutral conformance vectors, written from the specification text.

**What this does not change.** No existing field, enum value or default. It does not decide coexistence with `destructiveHint`.

**Disclosure.** I maintain Reversibility Classes, the example scheme. The proposal is written so that any scheme can use `outcomeRef`; the mapping table is an example, not a requirement. Drafted with AI assistance; I checked every claim and link.
