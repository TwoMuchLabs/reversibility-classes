# Contributing

The registry is only as useful as it is accurate, so contributions are held to two rules: **every tool name is verified against a primary source**, and **every class has a one-sentence reason**.

## Ways to help

1. **Classify a server.** Add a record for an MCP server that isn't in [`registry/`](registry/).
2. **Challenge a class.** Open an issue using the *Classification dispute* template. Disagreements are the most valuable contributions: they are how the spec gets sharper.
3. **Review drafts.** Every machine-proposed entry starts as `review: draft`. Check one against the server's code and change it to `reviewed`.
4. **Maintainers:** if you maintain a server, a record you have checked can be marked `verified`. Please say so in the pull request.
5. **Comment on the spec.** Open questions for v0.2 are listed at the end of [`SPEC.md`](SPEC.md).

## Adding a server

1. Copy an existing file in `registry/` (e.g. `git.yaml`) to `registry/<server-id>.yaml`.
2. Fill in `server.source`, and in `server.evidence` the exact README or source-code URL you read the tool names from. Prefer the source code: READMEs drift. Add the commit or version to `server.version`.
3. List **every** tool the server exposes, with its exact registered name.
4. For each tool, pick the class with the tests in [SPEC section 4](SPEC.md#4-the-five-classes):
   - No effect outside the agent's context → **R0**
   - Fully restorable, nobody outside saw it → **R1**
   - Can't be restored, but a known compensating action exists → **R2**
   - Needs a human or out-of-band recovery, or changes access, credentials, availability or physical state → **R3**
   - Disclosure, unrecoverable destruction, or unrecallable consequence → **R4** (set `disclosure: true` for disclosures)
5. Use the **highest** class the tool can reach as its base. Add `when` rules only to *lower* the class for narrow, positively matched argument forms (an anchored regex, an exact value, or an absent flag), or to raise it.
6. Run the checks:

```bash
pip install -e ".[dev]"
rclass validate registry
pytest -q
```

## Using AI tools

AI-assisted contributions are welcome. Most of this registry was drafted with AI help and then checked. The rule is the same either way: **you are accountable for every tool name and every class you submit.** Say in the pull request which tools you used and what you checked by hand. The evidence links are how reviewers verify the work, so a tool name with no traceable source, or a class an AI proposed that you didn't check against the code, will be sent back.

## Before you open a pull request

- **One server per pull request.** Claim it on the [help-wanted issue](https://github.com/waghmaredb/reversibility-classes/issues/5) first, so two people don't do the same one.
- **Use the server people actually install.** That is usually the vendor's official repository, not a fork or an older copy. Say which in `server.source`.
- **Pin what you checked.** Put the commit or release you verified against in `server.version`.
- **Every tool, exact names.** Compare against the server's code (where tools are registered), not only its README.
- **Ask "what does it overwrite or delete?"** A tool that overwrites or removes data with no history in that system is R4, even if it looks routine. A deployment with backups can lower it with an override; the registry records the default.
- **Ask "who receives this?"** Anything whose purpose is to deliver content to people or other systems is a disclosure (R4, `disclosure: true`).
- **Flag what doesn't fit.** If a tool doesn't fit the five classes cleanly, say so in the pull request. That's input for the next version of the spec, not a failure.

## Style for rationales

One sentence, plain words, stating the fact that decides the class: *"Fails if the destination exists, so nothing is overwritten."* Not *"Low risk."*
