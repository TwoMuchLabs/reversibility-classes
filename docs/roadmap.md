# Distribution roadmap (proposed)

> Status: **proposal, 20 November 2026.** Nothing here is built unless it says so. Items become commitments when a maintainer accepts them; until then they are open for comment like any other pull request.

This page covers one question only: how people get Reversibility Classes into the tools they already use. Changes to the specification are tracked in [SPEC section 12](../SPEC.md#12-open-questions-for-v03) and the [changelog](../CHANGELOG.md), not here.

## Where we are today

- `rclass scan`, `rclass hook` (a Claude Code PreToolUse hook), `rclass audit` and policy exports for Cedar, CEL and Claude Code settings exist and are tested.
- Installing means a git URL (`pipx install "git+https://github.com/TwoMuchLabs/reversibility-classes"`).
- Turning the hook on means generating `hook.json` and merging it into Claude Code settings by hand.
- The hook exists for one agent host.

The classes are only useful where they are enforced, so each manual step above is a place where adoption stops.

## Now

- [ ] **Publish to PyPI**, so the install is `pipx install <name>` and a first scan is one line with no clone. Confirm the package name is available before promising it anywhere. The build and the one-time setup are prepared; see [releasing](releasing.md). Not published yet.
- [ ] **Claude Code plugin marketplace entry** that installs the PreToolUse hook in one command, in place of the manual settings merge. The hook's behaviour does not change: any error still asks, and a hook can still only make the host stricter.
- [x] **A shareable scan summary** (`rclass scan --summary`, unreleased). An opt-in `rclass scan` output that fits on one screen and is safe to paste in public: counts per class and the names of public registry records only, with no arguments, environment values, internal server names or paths.

**Gate for each item:** the conformance vectors pass, and a test shows that an unrecognised server or tool is still treated as R4 through the new install path.

## Next

- [ ] **Survey other agent hosts** for a pre-tool-call hook that passes the call's arguments (candidates: Codex CLI, Gemini CLI, OpenCode, GitHub Copilot CLI, Hermes Agent). Record what each one exposes before writing any code.
- [ ] **Port the hook only where arguments are visible.** Where a host cannot show arguments, generate static policy from each tool's worst-case class, as the Cedar and CEL exports do today, and say so in that host's documentation.
- [ ] **Agent Skill packaging** of the five classes and the three questions, for hosts that read `SKILL.md`. A skill is guidance to the model, not enforcement, and its description must say that.

**Gate for each host:** the same two tests as above, plus a documented statement of what the integration cannot see.

## Later

- [ ] A way for third-party implementations to state which version of the conformance vectors they pass.

## Not doing

- **Our own agent or coding harness.** The project is a specification, a registry and a reference classifier that plug into other people's tools.
- **Trusting a server's own annotations to make installation smoother.** They remain a hint.
- **Telemetry from installs.** Adoption is counted from what people choose to tell us ([ADOPTERS.md](../ADOPTERS.md)).
