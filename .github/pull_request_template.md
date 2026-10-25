## What this adds or changes

<!-- e.g. "Adds a record for redis/mcp-redis (N tools)" or "Raises github/fork_repository from R1 to R2" -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`), certifying the [DCO](https://developercertificate.org/)
- [ ] One server (or one dispute) per pull request
- [ ] `server.source` is the repository people actually install, usually the vendor's official one
- [ ] `server.version` pins the commit or release I verified against
- [ ] `server.packages` lists how people install it (no versions), so `rclass scan` recognises it
- [ ] Every tool the server exposes is listed, with names checked against where the code registers them
- [ ] Every class has a one-sentence rationale stating the fact that decides it
- [ ] Overwrites and deletes with no history are R4; anything that delivers content to others has `disclosure: true`
- [ ] Every R1/R2 tool names its undo in `compensation`, and I checked that the undo exists, reverses this action (not another one), and needs no one outside the principal to agree
- [ ] `when` rules only use argument names the tool really accepts, and only lower a class for narrow, positively matched values
- [ ] `rclass scan <config> --details` against the real server shows every tool recognised
- [ ] `rclass validate --strict registry` and `pytest -q` pass locally

## AI assistance

<!-- Which tools did you use, and what did you check by hand? AI help is welcome; you are accountable for every name and class. -->

## Anything that didn't fit the five classes

<!-- Optional, but valuable: tools whose class you weren't sure about, and why. -->
