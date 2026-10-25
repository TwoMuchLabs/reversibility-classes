# Security policy

## Reporting a vulnerability

Report privately through GitHub: **[Report a vulnerability](https://github.com/TwoMuchLabs/reversibility-classes/security/advisories/new)** (the repository's Security tab → "Report a vulnerability"). Please do not open a public issue for anything that could be exploited before it is fixed.

You will get an acknowledgement within 5 working days and a first assessment within 10. This is a small project with one maintainer; if you have not heard back in that time, open a public issue that says only that you sent a report, with no details.

## What counts

Report privately:

- A way to make `rclass scan`, `rclass hook` or `rclass policy` **fail open**: treat an unrecognised server as a registry server, allow a call that should ask, crash in a way that skips the check, or start something the user did not consent to.
- A way to make `rclass scan` leak secrets (environment values, arguments, tokens) into its output.
- A problem in the release or CI workflows that could let someone change what is published.
- A classification that is wrong **and** exploitable in a way not yet public, such as a "read-only" tool that can be made to write (compare the [PostgreSQL `COMMIT;` bypass](https://securitylabs.datadoghq.com/articles/mcp-vulnerability-case-study-SQL-injection-in-the-postgresql-mcp-server/)).

Use a public [classification dispute](https://github.com/TwoMuchLabs/reversibility-classes/issues/new?template=classification-dispute.yml) for a class you disagree with when the behaviour is already documented or public. Disputes are how the registry improves.

A vulnerability in an MCP server listed in the registry belongs with that server's maintainers. Tell us too if it changes the server's class.

## Supported versions

Fixes go into the latest release. The specification is versioned separately; a security-relevant change to it is published as a new specification version and noted in the [changelog](CHANGELOG.md).

## Disclosure

Once a fix is released, the report is published as a GitHub security advisory, crediting the reporter unless they prefer not to be named.
