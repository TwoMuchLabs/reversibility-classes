# Governance

Reversibility Classes is a specification, an open registry and a reference tool. This document says who decides what, and how that is meant to change as the project grows.

## Today: an editor model

The project has one maintainer (see [MAINTAINERS.md](MAINTAINERS.md)), who acts as **editor** of the specification and **reviewer** of the registry. The editor merges changes, cuts releases and decides when discussion does not reach agreement. Decisions and their reasons are recorded in public, in issues, pull requests and the [changelog](CHANGELOG.md).

## How decisions are made

| Change | How it is decided |
|---|---|
| **Registry record** (a new server, a class, a rationale) | A pull request with evidence links, reviewed against [CONTRIBUTING.md](CONTRIBUTING.md). Merged when the evidence supports the class. |
| **Classification dispute** | An issue using the dispute template. Decided on the evidence; the outcome and reasoning are recorded in the issue, and judgment calls go into [open questions](docs/open-classification-questions.md). |
| **Specification, editorial** (wording, examples, typos) | A pull request; merged by the editor. |
| **Specification, normative** (any MUST, SHOULD or MAY; a class definition; the record format) | An issue or pull request open for comment for **at least 7 days** before merging, and a new specification version. Breaking changes need a new minor version while the specification is pre-1.0 and a new major version after. |
| **Code and tooling** | A pull request that passes CI. |

Anyone may propose any of these. Every commit must be signed off under the [Developer Certificate of Origin](CONTRIBUTING.md#sign-off-developer-certificate-of-origin).

## Neutrality

The specification and registry are meant to be used by any vendor, gateway or client. No class is assigned, raised or lowered because of who makes a server or who uses the registry. A contributor with a commercial interest in a record they change says so in the pull request. The maintainer's employer is not involved in the project; the maintainer contributes in a personal capacity.

## Becoming a maintainer

A contributor who has made sustained, careful contributions (records that held up in review, disputes argued from evidence, or code) can be invited to become a maintainer by the existing maintainers. Maintainers have merge rights and share the editor's role.

## Where this is going

The project's goal is to be governed by people from several organisations. When there are maintainers from at least two organisations, decisions on normative specification changes move from the editor to a vote of the maintainers, with a simple majority and no single organisation holding more than half the votes. If the project joins a neutral foundation, its governance will follow that foundation's charter, and the project's name, domain and repository will transfer to it.

## Name

"Reversibility Classes" is the name of this project. It is not a registered trademark. Please don't use it to describe a modified classification scheme in a way that suggests it is this one; say "based on Reversibility Classes" instead.
