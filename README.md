# Reversibility Classes for Agent Actions

**If your AI agent gets this wrong, can you undo it?**

Agent permission systems ask *which tools* an agent may use, or *how confident* the model is. Neither answers the question a person actually cares about. This project defines five **Reversibility Classes**, R0 to R4, that describe how recoverable a single tool call is. It also publishes an open, evidence-linked registry of real MCP servers' tools classified on that scale, and a small reference classifier you can drop into a gateway.

| Class | Name | Test | Default policy |
|---|---|---|---|
| **R0** | No effect | Nothing changes outside the agent's working context | Act |
| **R1** | Restorable | Fully restorable with ordinary effort; nobody outside saw it | Act, then notify |
| **R2** | Compensable | Can't be restored, but a known fix exists at bounded cost | Approve, or act within a budget |
| **R3** | Hard to recover | Needs a human or out-of-band recovery; changes access, credentials, availability or physical state | Approve each time |
| **R4** | Irreversible | A disclosure, unrecoverable destruction, or a consequence that can't be recalled | Never autonomous |

Refunding a $40,000 charge is expensive but recoverable: **R2**. A one-line message to the wrong Slack channel is cheap but permanent: **R4**. Severity scores get these backwards; reversibility doesn't.

## Why this exists

- **MCP's tool hints are too coarse.** `readOnlyHint` and `destructiveHint` can't say *"compensable"* or *"this is a disclosure"*, and the MCP spec tells clients to treat them as untrusted anyway. This spec maps them conservatively (section 7) and lets an independent registry say more.
- **Class is a fact about the action type, not a model's opinion.** A gateway can enforce it with no model in the decision path.
- **Arguments matter.** The same `write_query` tool is R1 for a single `INSERT` and R4 for `DROP TABLE`. Records can say so with narrow, allow-listed rules.
- **Unknown means R4.** Unclassified tools fail closed.
- **Audit-ready.** Logging the class and the rule behind every decision answers "which irreversible actions did this agent take, and who approved them?"

Read the full specification: **[SPEC.md](SPEC.md)** (v0.1, draft for comment).

## The registry

<!-- registry-stats:start -->
**20 servers, 536 tools classified.** Counts are base classes; some tools drop to a lower class for specific arguments.

| Server | Tools | R0 | R1 | R2 | R3 | R4 |
|---|---|---|---|---|---|---|
| [Brave Search (official MCP server)](registry/brave-search.yaml) | 8 | 8 | · | · | · | · |
| [Fetch (MCP reference server)](registry/fetch.yaml) | 1 | 1 | · | · | · | · |
| [Filesystem (MCP reference server)](registry/filesystem.yaml) | 14 | 10 | 3 | · | · | 1 |
| [Git (MCP reference server)](registry/git.yaml) | 12 | 7 | 5 | · | · | · |
| [GitHub (official MCP server)](registry/github.yaml) | 96 | 60 | 5 | 8 | 2 | 21 |
| [Google Maps (archived MCP reference server)](registry/google-maps.yaml) | 7 | 7 | · | · | · | · |
| [Kubernetes (containers/kubernetes-mcp-server)](registry/kubernetes.yaml) | 76 | 59 | 2 | 1 | 4 | 10 |
| [Memory / knowledge graph (MCP reference server)](registry/memory.yaml) | 9 | 3 | 3 | · | · | 3 |
| [Notion (official self-hosted MCP server)](registry/notion.yaml) | 24 | 14 | 5 | 1 | · | 4 |
| [Playwright MCP (Microsoft)](registry/playwright.yaml) | 72 | 30 | 17 | · | 10 | 15 |
| [PostgreSQL (archived MCP reference server)](registry/postgres.yaml) | 1 | 1 | · | · | · | · |
| [Puppeteer (archived MCP reference server)](registry/puppeteer.yaml) | 7 | 2 | · | · | 1 | 4 |
| [Sentry (official MCP server)](registry/sentry.yaml) | 76 | 52 | 5 | 6 | 4 | 9 |
| [Sequential Thinking (MCP reference server)](registry/sequentialthinking.yaml) | 1 | 1 | · | · | · | · |
| [Slack (archived MCP reference server)](registry/slack.yaml) | 8 | 5 | · | · | · | 3 |
| [SQLite (archived MCP reference server)](registry/sqlite.yaml) | 6 | 3 | 2 | · | · | 1 |
| [Stripe (official hosted MCP server)](registry/stripe.yaml) | 15 | 8 | 1 | 1 | · | 5 |
| [Supabase (official MCP server)](registry/supabase.yaml) | 40 | 23 | 1 | 3 | 4 | 9 |
| [Terraform (HashiCorp MCP server)](registry/terraform.yaml) | 61 | 39 | 10 | 1 | 6 | 5 |
| [Time (MCP reference server)](registry/time.yaml) | 2 | 2 | · | · | · | · |
| **Total** | **536** | **335** | **59** | **21** | **31** | **90** |
<!-- registry-stats:end -->

Every record links to the exact README or source file the tool names were verified against. Every class carries a one-sentence rationale. Entries start as `draft` and become `reviewed` or `verified` as people check them; see [CONTRIBUTING.md](CONTRIBUTING.md).

A few classifications worth arguing about:

- `filesystem/write_file` is **R4**: it overwrites without keeping the old content. `edit_file` is **R1**, because it returns a diff you can invert.
- `slack/slack_add_reaction` is **R4**: other people see it, and the server can't remove it.
- `postgres/query` is **R0**: the server forces a read-only transaction and rolls it back.

Disagree? [Open a classification dispute](../../issues/new?template=classification-dispute.yml). That is the point.

## Use it

```bash
pip install git+https://github.com/waghmaredb/reversibility-classes
rclass lookup sqlite write_query --args '{"query": "DROP TABLE users"}'
# R4  Irreversible  (registry)
#   Accepts any non-SELECT statement, including UPDATE, DELETE and DROP, which destroy prior values.
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

Your environment can be safer than the defaults. If you snapshot a database nightly, publish a **deployment override** that lowers `DROP TABLE` from R4 to R3, with the reason logged on every decision. Overrides can never lower a disclosure.

## Status and roadmap

v0.1 is a draft for comment. Open questions for v0.2 include a **detection window** (how long a mistake usually goes unnoticed), **pattern rules** for sequences of actions that are harmless alone but not together, and whether a reversibility hint belongs in MCP itself. See [SPEC section 12](SPEC.md#12-open-questions-for-v02).

## License

Specification, schema and registry: [CC BY 4.0](LICENSE-SPEC-AND-DATA.md). Code: [Apache 2.0](LICENSE). To cite, see [CITATION.cff](CITATION.cff).

Maintained by [Deepak Waghmare](https://github.com/waghmaredb). Written in a personal capacity.
