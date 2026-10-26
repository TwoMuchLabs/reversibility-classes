# Reversibility Classes for Agent Actions

**If your AI agent gets this wrong, can you undo it?**

Agent permission systems ask *which tools* an agent may use, or *how confident* the model is. Neither answers the question a person actually cares about. This project defines five **Reversibility Classes**, R0 to R4, that describe how recoverable a single tool call is. It also publishes an open, evidence-linked registry of real MCP servers' tools classified on that scale, and a small reference classifier you can drop into a gateway.

| Class | Name | Test | Default policy |
|---|---|---|---|
| **R0** | No effect | Nothing changes outside the agent's working context | Act |
| **R1** | Restorable | The prior state is recorded, you can write it back yourself, and nobody outside saw it | Act, then notify |
| **R2** | Compensable | Can't be restored, but a known fix exists at bounded cost | Approve, or act within a budget |
| **R3** | Hard to recover | Needs a human or out-of-band recovery; changes access, credentials, availability or physical state | Approve each time |
| **R4** | Irreversible | A disclosure, unrecoverable destruction, or a consequence that can't be recalled | Never autonomous |

Charging a customer $40,000 by mistake is expensive but compensable: refund the charge and the customer is made whole, at a known cost. **R2**. A one-line message to the wrong Slack channel is cheap but permanent: **R4**. Severity scores get these backwards; reversibility doesn't.

**What it is for.** The specification and the registry are the core: a shared definition of each class, and per-tool classes with the evidence behind them, for whatever enforces your policy. In an organisation that is a central gateway, which can take the registry as JSON (`rclass export`) or as ready-made Cedar or CEL policy (`rclass policy`). For one developer, `rclass scan` shows what an agent could do and `rclass hook` gates Claude Code on their own machine, with [limits](#limits) worth reading first.

## Check your own agent

```bash
pipx install "git+https://github.com/TwoMuchLabs/reversibility-classes"
rclass scan
```

`rclass scan` finds your MCP client configuration (Claude Desktop, Claude Code, Cursor, VS Code or Windsurf), starts each local server, asks it for its tools, and tells you what an agent connected to it could do:

```text
files     Filesystem (MCP reference server) · 14 tools
          R0 10  R1 2  R2 0  R3 0  R4 2
          irreversible (R4): edit_file, write_file

repo      Git (MCP reference server) · 12 tools
          R0 7  R1 2  R2 0  R3 2  R4 1
          irreversible (R4): git_checkout
          approval each time (R3): git_add, git_reset

notes     Memory / knowledge graph (MCP reference server) · 9 tools
          R0 3  R1 3  R2 0  R3 0  R4 3
          irreversible (R4): delete_entities, delete_observations, delete_relations

35 tools across 3 servers. 6 irreversible (R4).
Under the default policy an agent may act alone on 27 (R0–R1); the other 8 need a human.
Each tool is counted at the worst class its arguments can reach.
Registry classes are drafts: checked against source by their author, not yet reviewed by a second person.
```

What it promises:

- **Worst case, not best case.** A tool is counted at the highest class any of its arguments can reach. Unknown tools and servers count as R4, and a server's own annotations are shown as a hint, never trusted.
- **No guessing who a server is.** A server is matched to a registry record only when its config runs that package or image as itself, or points at its hosted URL. Anything that could make something else run counts as unrecognised: `npx -p` or `uvx --from` running a different command, a docker entrypoint or command override, a custom registry or source, a launcher from a relative or project path, or an `env` that sets `PATH`, `NODE_OPTIONS`, `PYTHONPATH` and the like. Tool names alone never count, because a server chooses its own; if they look familiar, it suggests `--assume`, which only applies to a server it starts and lists.
- **Minimal environment.** Each server gets only what the official MCP SDKs pass by default (`PATH`, `HOME`, `USER`, …), proxy and certificate settings, and its own `env` from the config. Your cloud keys and tokens stay out. `--inherit-env` passes everything.
- **Repository configs need consent.** A `.mcp.json`, `.vscode/mcp.json` or `.cursor/mcp.json` belongs to whoever wrote the repository. `rclass scan` asks before starting its servers (or needs `--yes`), and honours what you already approved or disabled in Claude Code.
- **No secrets in the output.** Arguments, environment values and what a failing server writes are not printed. `--show-server-errors` shows server output with your config's values and common token shapes redacted (best effort).
- **Built for hostile servers.** Replies are shape-checked, lines and tool lists are capped, and each server runs under a hard deadline in its own process group, which is killed afterwards. (A child that detaches into a new session of its own can outlive it; run scans of untrusted configs in a container.)
- **Drift is flagged.** If your server offers tools the record doesn't list, they count as R4 and are named; `--write-drafts DIR` turns them into a starting record to [contribute](CONTRIBUTING.md).

Starting a server runs the command your config names, exactly as your client would, so only start configs you trust.

**In CI, scan without starting anything.** `--list` reads the config and reports the registry's classes for every server it recognises, so a pull request can't run code on your CI machine:

```bash
rclass scan .mcp.json --list --fail-on R4
```

Exit codes: `0` ok · `1` something reachable is at or above the threshold (an unidentified server counts as R4) · `2` usage error · `3` the scan was incomplete (a server couldn't be scanned, or its config entry is invalid), so the gate can't pass.

`--list` can't see version drift: it applies the record's classes to whatever version the config pins. Run a full scan somewhere you trust (a developer machine, or a scheduled job) to catch tools a newer release added.

## Enforce it

**In Claude Code, gate every MCP call by its class.** `rclass hook` is a [PreToolUse hook](https://code.claude.com/docs/en/hooks): Claude Code hands it each MCP tool call with its arguments, and it answers with the class of *that* call. A SELECT through `write_query` is R0; the same tool with `DROP TABLE` is R4 and asks you first, even if you allowlisted the tool.

```bash
rclass policy --format claude-hook > hook.json   # merge into .claude/settings.json or ~/.claude/settings.json
```

```text
rclass: R4 Irreversible (registry). Accepts any non-SELECT statement: UPDATE, DELETE and DROP destroy
prior values, and even INSERT can be an upsert (…) that overwrites rows. Needs your approval.
```

By default R0–R1 calls get no decision (your own permission rules apply) and R2–R4 ask, unless an R2 call is within a budget your platform team set (below). After a session has read anything, a call that can carry data to a destination the agent chooses is treated as R4 (SPEC 9), so "read the credentials file, then fetch a URL with them in it" asks before the fetch. `--allow-up-to R1` lets low classes run without a prompt and `--deny-from R4` blocks instead of asking. Every decision is logged to `~/.local/state/rclass/audit.jsonl` with its class, rule, the session's effective class, the user, host and Claude Code permission mode, and a SHA-256 digest of the arguments, never the arguments themselves (`--log FILE` to move it, `--no-log` to stop). The hook also runs after each call it decides about, so the log knows which prompts led to the call running. Servers are identified from what your Claude Code config runs, as in `rclass scan --list`, using Claude Code's project folder rather than wherever the agent has moved to. An unrecognised server or tool is R4, and so is a server whose config changes during a session. `--map NAME=RECORD` vouches for a server in your own user config; a repository's `.mcp.json` cannot use it. Any error inside the hook asks rather than allowing. A hook can only make Claude Code stricter: your own deny and ask rules still apply.

**Answer the auditor's question.** `rclass audit report` lists every R3 or R4 decision, and everything that asked or blocked, with what happened next: approved and ran, refused, or blocked. Each log line carries the hash of the one before it, so `rclass audit verify` exits 1 if a line was changed, removed or reordered. That makes tampering on the same machine evident, not impossible: ship the log to storage the agent can't write if it has to hold up.

```text
2026-10-25T13:02:11+00:00  R4  db/write_query  ask: approved (alex) and ran  (session 3f2a9c1e, alex@laptop)
2026-10-25T13:04:40+00:00  R4  web/fetch  ask: not run (refused or cancelled)  (session 3f2a9c1e, alex@laptop)
```

**Recognise your own servers.** A server mapping file, kept where agents and repositories can't write (`/etc/rclass/servers.yaml` by default, or `RCLASS_SERVERS`, or `--servers FILE`), says which of your launchers, mirrored images, internal packages or gateway URLs runs which registry record. The record can be public or your own (`--registry`).

```yaml
servers:
  - record: github
    image: registry.corp.example/mcp/github-mcp-server     # a mirror; the tag still counts for min_version
  - record: corp-billing
    command: /opt/mcp/billing/bin/server                    # an absolute launcher, with its exact arguments
    args: [--stdio]
  - record: corp-db
    package: "@corp/db-mcp"                                 # from your internal npm registry only
    registry: https://npm.corp.example
  - record: github
    url: https://mcp-gw.corp.example/github                 # a gateway that fronts a public server
```

The usual protections still apply: an `env` that changes which code runs, a launcher inside the project, extra packages or indexes, or two mappings naming different records leave a server unrecognised. `rclass scan` marks servers it recognised through a mapping, and warns if you can edit the mapping file yourself.

**Let routine R2 calls run within a budget.** SPEC 8 lets an R2 (compensable) call run without a prompt when it falls within a pre-approved budget. Without one, every R2 call asks, and people asked dozens of times a day stop reading the prompt. A budget file, kept where agents can't write (`/etc/rclass/budgets.yaml` by default, or `RCLASS_BUDGETS`, or `--budgets FILE`), says which R2 tools may run unprompted, how often and for how much:

```yaml
budgets:
  - name: supabase-branches
    server: supabase                 # a registry record id, not the name in your MCP config
    tools: [create_branch]           # default: every R2 tool of the server
    max_calls: 5
    window: 1d                       # s, m, h or d; omit for "per session"
    scope: user                      # all of this OS user's sessions; default: each session
  - name: small-credits
    server: corp-billing
    tools: [issue_credit]
    max_calls: 20
    window: 1d
    scope: user
    where: {currency: usd}           # only calls whose arguments equal these
    amount: {arg: amount_cents, max_each: 5000, max_total: 20000}
```

```text
rclass: R2 Compensable (registry). Creates a branch that incurs hourly compute charges; … Allowed within
budget 'supabase-branches': call 3 of 5 per day (SPEC 8).
```

A budget never covers R3 or R4, an unrecognised server, a call the session guard raised, or a call whose amount argument is missing or isn't a finite number (strings don't count). The first budget that matches a call decides it: if that budget is spent or the amount doesn't fit, the call asks, and a broader budget further down never picks it up. Limits don't stack, so put narrow budgets before broad ones; `rclass budgets check` warns about a budget that can never apply. Each call is counted under a lock before it runs, so parallel calls can't both take the last one, and once the budget is spent the call asks as before. `rclass budgets check FILE` says in plain words what a file allows, and warns if you can edit it yourself. `rclass audit report` lists every call a budget let through, with its count.

**For gateways that can't see arguments, export static policy.** These use each tool's worst-case class, so they are never less strict than the hook:

| Format | For | Approval step |
|---|---|---|
| `--format cedar` | [Docker MCP governance](https://docs.docker.com/ai/sandboxes/governance/reference/mcp-policy/) | R2–R4 become `@requireApproval` permits; unlisted tools stay denied |
| `--format cel` | [agentgateway](https://agentgateway.dev/docs/standalone/latest/documentation/configuration/security/mcp-authz/) `mcpAuthorization` | none, so only R0–R1 tools are allowed and the rest are filtered out |
| `--format claude-settings` | Claude Code permission rules | R2–R4 go to `ask` (or `deny`), which outrank allow rules |

```bash
rclass policy --format cedar .mcp.json --deny-from R4 > rclass.cedar
```

Server names in the output are the names in your config; for Docker and agentgateway they must match the names the gateway registered. Budgets need the hook, which counts calls, so static policy still asks for every R2 tool.

### Limits

Read these before relying on the hook as a control.

- **The session guard is coarse.** Once a session has used any tool, a call that sends data to a destination the agent chooses (`fetch`, browser navigation, Claude Code's `WebFetch`) is R4 and asks, because it could carry what the session read. List hosts you trust with `--egress-allow docs.python.org` (subdomains included); a URL that parsers could read differently (userinfo, backslashes, non-ASCII hosts) is never treated as allow-listed, and every URL in a call's arguments must be allowed, not just the one named `url`. An allow-listed host with an open redirect or user-controlled subdomains can still carry data on, so list narrowly. The guard does not see data leaving through a shell command such as `curl`, which Claude Code's own rules govern.
- **It runs on the developer's machine.** The hook asks before a built-in tool writes to its session state, its log or Claude Code's settings, assumes a read if its state can't be kept or has disappeared mid-session, and runs Python with `-I` so a repository can't substitute its own `rclass` package, through the current folder or `PYTHONPATH`. An agent determined to get around it on the same machine still can, for example through a shell command that never names those paths. Use it to catch mistakes, not as a boundary against a compromised agent; for that, enforce at a gateway the agent cannot reach.
- **If Claude Code cannot start the hook at all**, or it times out, Claude Code runs the call under your normal permission rules. The generated settings run a fixed interpreter (`python -m rclass`) with a timeout to make that unlikely. If you need a floor that holds even then, add `--format claude-settings` through managed settings; it asks for every tool whose worst case is R2 or above, so it prompts more than the hook.
- **Records are checked against one release of each server.** A record can say which release with `server.min_version`; if your config pins an older release, or one whose version can't be read, the rules that lower a class are ignored and you get the stricter base class. Only the GitHub record sets it so far. An unpinned image or package counts as the newest release, so a stale cached image is treated as new.
- **Your own servers need a mapping.** A server launched from an absolute path, an internal registry image, an internal npm or PyPI index, `python -m`, or a gateway URL matches no public record, so it is R4 and every call asks, until your platform team maps it (below).
- **Budgets are counted on the developer's machine.** The count sits with the session state, so it has the same limit: an agent that can write anywhere on disk can reset it. A budget saves prompts on calls you could undo at a known cost; it is not a spending control. Put hard money limits in the service itself. Budgets need file locks, so on Windows they never apply and R2 calls ask.
- **A repository can set environment variables for the hook.** Claude Code passes a project's `.claude/settings.json` `env` to hooks, so inside a hook rclass trusts none of them. It runs Python with `-I` (no `PYTHONPATH`, `PYTHONHOME` or user site-packages), ignores `RCLASS_SERVERS`, `RCLASS_BUDGETS`, `RCLASS_STATE_DIR` and `XDG_STATE_HOME`, finds your home folder from the system rather than `HOME`, and refuses a mapping or budget file inside the project folder. Pass `--servers`, `--budgets` and `--state-dir` to `rclass policy --format claude-hook` and the hook gets them as flags. What no hook can stop is a variable that changes how programs load (`LD_PRELOAD`); `${VAR}` references in your own MCP config also expand with the hook's environment.
- **No lock is held for long.** If another process holds one of the hook's locks for more than 2 s, the hook stops waiting and takes the strict path (no budget, assume the session has read data), rather than running into Claude Code's hook timeout, after which Claude Code would run the call without rclass.
- **MCP tools, plus `WebFetch` after a read.** The hook sees every tool so the session guard knows when data has been read, but it only decides MCP calls and `WebFetch`; other built-in tools are left to Claude Code's rules. It adds about 0.1 s to each built-in tool call and about 0.4 s to each MCP call.


- **MCP's tool hints are too coarse.** `readOnlyHint` and `destructiveHint` can't say *"compensable"* or *"this is a disclosure"*, and the MCP spec tells clients to treat them as untrusted anyway. This spec maps them conservatively (section 7) and lets an independent registry say more.
- **Class is a fact about the action type, not a model's opinion.** A gateway can enforce it with no model in the decision path.
- **Arguments matter.** GitHub's `create_repository` is R1 when the repository is private, and R4 when it's public. Records can say so with narrow, allow-listed rules.
- **Unknown means R4.** Unclassified tools fail closed.
- **Audit-ready.** Logging the class and the rule behind every decision answers "which irreversible actions did this agent take, and who approved them?"

Read the full specification: **[SPEC.md](SPEC.md)** (v0.3.0, draft for comment).

Browse the whole registry: **[reversibility.twomuchlabs.com](https://reversibility.twomuchlabs.com)**.

## The registry

<!-- registry-stats:start -->
**20 servers, 536 tools classified.** Counts are base classes; some tools drop to a lower class for specific arguments.

| Server | Tools | R0 | R1 | R2 | R3 | R4 |
|---|---|---|---|---|---|---|
| [Brave Search (official MCP server)](registry/brave-search.yaml) | 8 | 8 | · | · | · | · |
| [Fetch (MCP reference server)](registry/fetch.yaml) | 1 | 1 | · | · | · | · |
| [Filesystem (MCP reference server)](registry/filesystem.yaml) | 14 | 10 | 2 | · | · | 2 |
| [Git (MCP reference server)](registry/git.yaml) | 12 | 7 | 2 | · | 2 | 1 |
| [GitHub (official MCP server)](registry/github.yaml) | 96 | 60 | 5 | 5 | 3 | 23 |
| [Google Maps (archived MCP reference server)](registry/google-maps.yaml) | 7 | 7 | · | · | · | · |
| [Kubernetes (containers/kubernetes-mcp-server)](registry/kubernetes.yaml) | 76 | 59 | 2 | · | 4 | 11 |
| [Memory / knowledge graph (MCP reference server)](registry/memory.yaml) | 9 | 3 | 3 | · | · | 3 |
| [Notion (official self-hosted MCP server)](registry/notion.yaml) | 24 | 14 | 1 | · | 1 | 8 |
| [Playwright MCP (Microsoft)](registry/playwright.yaml) | 72 | 29 | 5 | · | 12 | 26 |
| [PostgreSQL (archived MCP reference server)](registry/postgres.yaml) | 1 | · | · | · | · | 1 |
| [Puppeteer (archived MCP reference server)](registry/puppeteer.yaml) | 7 | 2 | · | · | · | 5 |
| [Sentry (official MCP server)](registry/sentry.yaml) | 76 | 52 | 2 | 6 | 5 | 11 |
| [Sequential Thinking (MCP reference server)](registry/sequentialthinking.yaml) | 1 | 1 | · | · | · | · |
| [Slack (archived MCP reference server)](registry/slack.yaml) | 8 | 5 | · | · | · | 3 |
| [SQLite (archived MCP reference server)](registry/sqlite.yaml) | 6 | 3 | · | 2 | · | 1 |
| [Stripe (official hosted MCP server)](registry/stripe.yaml) | 15 | 8 | · | 2 | · | 5 |
| [Supabase (official MCP server)](registry/supabase.yaml) | 40 | 23 | 1 | 2 | 4 | 10 |
| [Terraform (HashiCorp MCP server)](registry/terraform.yaml) | 61 | 39 | 8 | 1 | 8 | 5 |
| [Time (MCP reference server)](registry/time.yaml) | 2 | 2 | · | · | · | · |
| **Total** | **536** | **333** | **31** | **18** | **39** | **115** |
<!-- registry-stats:end -->

Every record links to the exact README or source file the tool names were verified against. Every class carries a one-sentence rationale. Entries start as `draft` and become `reviewed` or `verified` as people check them; see [CONTRIBUTING.md](CONTRIBUTING.md).

A few classifications worth arguing about:

- `filesystem/write_file` is **R4**: it overwrites without keeping the old content. So is `edit_file`, though it returns a diff: it rewrites the file as UTF-8, so invalid bytes and mixed line endings change in ways the diff does not show.
- `slack/slack_add_reaction` is **R4**: other people see it, and the server can't remove it.
- `postgres/query` is **R4** unless the query is a single `SELECT`. The server wraps every query in a read-only transaction, but a query that starts with `COMMIT;` escapes it ([reported by Datadog Security Labs](https://securitylabs.datadoghq.com/articles/mcp-vulnerability-case-study-SQL-injection-in-the-postgresql-mcp-server/) in 2025).
- Every Playwright tool that accepts a `filename` is **R4** when one is given: an explicit filename is resolved against your workspace root, so it can overwrite your own files.

The registry has had one independent audit; the fixes are applied, and the [open questions](docs/open-classification-questions.md) it raised are waiting for opinions. Disagree with a class? [Open a classification dispute](https://github.com/TwoMuchLabs/reversibility-classes/issues/new?template=classification-dispute.yml). That is the point.

## Use it in your own code

```bash
pip install "git+https://github.com/TwoMuchLabs/reversibility-classes"
rclass lookup sqlite write_query --args '{"query": "DROP TABLE users"}'
# R4  Irreversible  (registry)
#   Accepts any non-SELECT statement: UPDATE, DELETE and DROP destroy prior values, and even INSERT can be an upsert (INSERT OR REPLACE, ON CONFLICT DO UPDATE) that overwrites rows.
#   default policy: never_autonomous
```

```python
from rclass import Session, Decision

session = Session()  # bundled open registry
c, decision = session.check("git", "git_commit", {"repo_path": ".", "message": "wip"})
print(c.rclass, decision)          # R1 Decision.ALLOW_AND_NOTIFY
session.record(c, decision)
```

[`examples/gateway_hook.py`](examples/gateway_hook.py) shows how to gate MCP `tools/call` requests in a proxy in about 40 lines.

**Make actions restorable.** If your gateway saves a file's current content before letting the agent overwrite it, `write_file` is restorable in your deployment: declare a `preimage` override and it becomes R1, with the capture checked before every call and its reference logged ([SPEC 5.6](SPEC.md#56-pre-image-capture)). A record of what changed is not enough on its own; you also need a way to write the old state back, before anyone else has seen the change.

Your environment can be safer than the defaults. If you keep point-in-time database backups, publish a **deployment override** that lowers `DROP TABLE` from R4 to R3 (restoring needs a DBA, so not R1), with the reason logged on every decision. A nightly snapshot is not enough on its own: it loses whatever changed since. Overrides can never lower a disclosure.

## Implement it in another language

Most MCP gateways aren't written in Python. Two things make a port straightforward:

- **[Conformance vectors](docs/conformance.md):** 52 classification cases and 9 invalid records in one JSON file, each tied to a section of the spec. Pass them and your implementation agrees with the spec, not just with this repository.
- **`rclass export`** writes the whole registry as one JSON document, so another implementation can consume it without parsing YAML.

## Status and roadmap

The specification is v0.3.0, a draft for comment; `rclass` 0.5 implements it and adds `rclass scan`, the Claude Code hook and policy exports (the tool and the spec are versioned separately). See the [changelog](CHANGELOG.md). Open questions for v0.3 include a **detection window** (how long a mistake usually goes unnoticed), **pattern rules** for sequences of actions that are harmless alone but not together, and whether a reversibility hint belongs in MCP itself. See [SPEC section 12](SPEC.md#12-open-questions-for-v03).

## License

Specification, schema, conformance vectors and code: [Apache 2.0](LICENSE), which includes a patent grant to implementers. Registry data and other documentation: [CC BY 4.0](LICENSE-SPEC-AND-DATA.md). To cite, see [CITATION.cff](CITATION.cff).

[Governance](GOVERNANCE.md) · [Maintainers](MAINTAINERS.md) · [Security](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Adopters](ADOPTERS.md)

A [Two Much Labs](https://twomuchlabs.com) project. Maintained by [Deepak Waghmare](https://github.com/waghmaredb). Written in a personal capacity.
