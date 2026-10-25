# Contributing

The registry is only as useful as it is accurate, so contributions are held to two rules: **every tool name is verified against a primary source**, and **every class has a one-sentence reason**.

## Ways to help

1. **Classify a server.** Add a record for an MCP server that isn't in [`registry/`](registry/).
2. **Challenge a class.** Open an issue using the *Classification dispute* template. Disagreements are the most valuable contributions: they are how the spec gets sharper.
3. **Review drafts.** Every machine-proposed entry starts as `review: draft`. Check one against the server's code and change it to `reviewed`.
4. **Maintainers:** if you maintain a server, a record you have checked can be marked `verified`. Please say so in the pull request.
5. **Comment on the spec.** Open questions for v0.3 are listed at the end of [`SPEC.md`](SPEC.md).

## Adding a server

The fastest start is to let `rclass scan` list the tools for you. Put the server in a config file the way you would run it, then:

```bash
rclass scan my-server.json --write-drafts drafts/
```

That writes `drafts/<server>.yaml` with every tool the server offers, each at R4 until you check it. (For a server that is already in the registry but has new tools, it writes `<id>.additions.yaml`: add those tools to the existing record; don't replace it.) A draft is not a contribution: `rclass validate --strict` rejects it until the placeholders are gone.

1. Start from the draft, or copy an existing file in `registry/` (e.g. `git.yaml`) to `registry/<server-id>.yaml`.
2. Fill in `server.source`, and in `server.evidence` the exact README or source-code URL you read the tool names from. Prefer the source code: READMEs drift. Add the commit or version to `server.version`.
3. Say how people install it, in `server.packages`: the npm or PyPI package name, or the container image, **without a version** (`@scope/server`, `mcp-server-x`, `ghcr.io/org/server`). If there is a hosted deployment with the same tools, add its URL to `server.endpoints`. This is how `rclass scan` recognises the server in someone's config; a server with no packages is never recognised.
4. List **every** tool the server exposes, with its exact registered name.
5. For each tool, pick the class with the tests in [SPEC section 4](SPEC.md#4-the-five-classes):
   - No effect outside the agent's context → **R0**
   - Fully restorable, nobody outside saw it → **R1**
   - Can't be restored, but a known compensating action exists → **R2**
   - Needs a human or out-of-band recovery, or changes access, credentials, availability or physical state → **R3**
   - Disclosure, unrecoverable destruction, or unrecallable consequence → **R4** (set `disclosure: true` for disclosures)
6. Use the **highest** class the tool can reach as its base. Add `when` rules only to *lower* the class for narrow, positively matched argument forms (an anchored regex, an exact value, or an absent flag), or to raise it.
7. Check it against the real server, then run the checks:

```bash
pip install -e ".[dev]"
rclass scan my-server.json --details     # every tool recognised, none "not in the registry record"
rclass validate --strict registry
pytest -q
```

## Using AI tools

AI-assisted contributions are welcome. Most of this registry was drafted with AI help and then checked. The rule is the same either way: **you are accountable for every tool name and every class you submit.** Say in the pull request which tools you used and what you checked by hand. The evidence links are how reviewers verify the work, so a tool name with no traceable source, or a class an AI proposed that you didn't check against the code, will be sent back.

## Sign-off (Developer Certificate of Origin)

Every commit in a pull request must be signed off, certifying the [Developer Certificate of Origin](https://developercertificate.org/): that you wrote the change, or otherwise have the right to submit it under the project's licenses. Add the line with `git commit -s`, which appends:

```text
Signed-off-by: Your Name <you@example.com>
```

The name and email must be yours, matching the commit author. A check on every pull request rejects commits without it; to fix older commits, run `git rebase --signoff main` and force-push your branch. Using AI tools does not change who signs: you do, and the sign-off is your statement that you checked the work (see above).

Contributions are licensed under the license of the files they change: Apache 2.0 for the specification, schema and code, CC BY 4.0 for the registry and documentation ([details](LICENSE-SPEC-AND-DATA.md)).

## Before you open a pull request

- **Sign off every commit** (`git commit -s`).
- **One server per pull request.** Claim it on the [help-wanted issue](https://github.com/TwoMuchLabs/reversibility-classes/issues/5) first, so two people don't do the same one.
- **Use the server people actually install.** That is usually the vendor's official repository, not a fork or an older copy. Say which in `server.source`.
- **Pin what you checked.** Put the commit or release you verified against in `server.version`.
- **Every tool, exact names.** Compare against the server's code (where tools are registered), not only its README.
- **Ask "what does it overwrite or delete?"** A tool that overwrites or removes data with no history in that system is R4, even if it looks routine. A deployment with backups can lower it with an override; the registry records the default.
- **Ask "what undoes this, and who has to agree?"** R1 and R2 are claims that an action can be undone, so every R1 or R2 tool (including one lowered by a `when` rule) must name its undo in `compensation`. Check four things: the undo really exists in that system; it reverses *this* action, not a different one (a mistaken charge is undone by a refund, but a refund is not undone by anything the merchant can do alone); nobody outside the principal has to consent; and what it leaves behind (fees, notifications, triggered runs) is known and bounded. If any check fails, raise the class.
- **Ask "who receives this?"** Anything whose purpose is to deliver content to people or other systems is a disclosure (R4, `disclosure: true`).
- **Flag what doesn't fit.** If a tool doesn't fit the five classes cleanly, say so in the pull request. That's input for the next version of the spec, not a failure.

## Style for rationales

One sentence, plain words, stating the fact that decides the class: *"Fails if the destination exists, so nothing is overwritten."* Not *"Low risk."*
