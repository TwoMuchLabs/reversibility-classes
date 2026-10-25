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

## Check your own agent

```bash
pipx install "git+https://github.com/TwoMuchLabs/reversibility-classes"
rclass scan
```

`rclass scan` finds your MCP client configuration (Claude Desktop, Claude Code, Cursor, VS Code or Windsurf), starts each server the way your client does, asks it for its tools, and tells you what an agent connected to it could do:

```text
files   Filesystem (MCP reference server) · 14 tools
        R0 10  R1 2  R2 0  R3 0  R4 2
        irreversible: edit_file, write_file

repo    Git (MCP reference server) · 12 tools
        R0 7  R1 2  R2 0  R3 2  R4 1
        irreversible: git_checkout

notes   Memory / knowledge graph (MCP reference server) · 9 tools
        R0 3  R1 3  R2 0  R3 0  R4 3
        irreversible: delete_entities, delete_observations, delete_relations

35 tools across 3 servers. 6 irreversible (R4).
Under the default policy an agent may act alone on 27 (R0–R1); the other 8 need a human.
```

- **Unknown tools count as R4.** A server or tool the registry doesn't cover is reported, never assumed safe. Its own annotations are shown as a hint, not trusted.
- **Version drift is flagged.** If your server offers tools the record doesn't list, or the record lists tools your server no longer has, you'll see it.
- **Nothing secret is printed.** Server arguments and environment values never appear in the output. `--list` shows what's configured without starting anything.
- **Use it in CI.** `rclass scan .mcp.json --fail-on R4` exits non-zero if anything irreversible is connected; `--json` gives the full report.
- **Help fill the gaps.** `--write-drafts DIR` writes a starting record for every unclassified tool, ready to [contribute](CONTRIBUTING.md).

## Why this exists

- **MCP's tool hints are too coarse.** `readOnlyHint` and `destructiveHint` can't say *"compensable"* or *"this is a disclosure"*, and the MCP spec tells clients to treat them as untrusted anyway. This spec maps them conservatively (section 7) and lets an independent registry say more.
- **Class is a fact about the action type, not a model's opinion.** A gateway can enforce it with no model in the decision path.
- **Arguments matter.** GitHub's `create_repository` is R1 when the repository is private, and R4 when it's public. Records can say so with narrow, allow-listed rules.
- **Unknown means R4.** Unclassified tools fail closed.
- **Audit-ready.** Logging the class and the rule behind every decision answers "which irreversible actions did this agent take, and who approved them?"

Read the full specification: **[SPEC.md](SPEC.md)** (v0.2, draft for comment).

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

The registry has had one independent audit; the fixes are applied, and the [open questions](docs/open-classification-questions.md) it raised are waiting for opinions. Disagree with a class? [Open a classification dispute](../../issues/new?template=classification-dispute.yml). That is the point.

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

- **[Conformance vectors](docs/conformance.md):** 46 classification cases and 9 invalid records in one JSON file, each tied to a section of the spec. Pass them and your implementation agrees with the spec, not just with this repository.
- **`rclass export`** writes the whole registry as one JSON document, so another implementation can consume it without parsing YAML.

## Status and roadmap

v0.2 is a draft for comment. It defines *restorable* precisely and adds pre-image capture; see the [changelog](CHANGELOG.md). Open questions for v0.3 include a **detection window** (how long a mistake usually goes unnoticed), **pattern rules** for sequences of actions that are harmless alone but not together, and whether a reversibility hint belongs in MCP itself. See [SPEC section 12](SPEC.md#12-open-questions-for-v03).

## License

Specification, schema and registry: [CC BY 4.0](LICENSE-SPEC-AND-DATA.md). Code: [Apache 2.0](LICENSE). To cite, see [CITATION.cff](CITATION.cff).

A [Two Much Labs](https://twomuchlabs.com) project. Maintained by [Deepak Waghmare](https://github.com/waghmaredb). Written in a personal capacity.
