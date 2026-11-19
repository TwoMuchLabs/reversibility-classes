# Releasing

For maintainers. Releases are cut from the changelog and built by CI; nothing is built or uploaded by hand.

## Cutting a release

1. Set the new version in `pyproject.toml`, `rclass/__init__.py` and `CITATION.cff` (with `date-released`).
2. In `CHANGELOG.md`, give the section its heading, `## vX.Y.Z — YYYY-MM-DD`, a bold one-line reason, the bullets, and the exact install line.
3. Merge to `main`. [`release.yml`](../.github/workflows/release.yml) runs the tests, tags the commit, creates the GitHub release from the changelog section, and attaches the wheel, the sdist, `SHA256SUMS` and a signed build-provenance attestation.

Merging a pull request that does steps 1 and 2 is what publishes the release, so review it as a release.

## Publishing to PyPI

The `pypi` job in `release.yml` uploads the same wheel and sdist that were attached to the GitHub release. It uses Trusted Publishing, so no API token is stored anywhere. It runs only when the repository variable `PYPI_PUBLISH` is `"true"`.

### One-time setup

Until these are done, releases go to GitHub only.

1. **Reserve the name on PyPI.** Sign in at pypi.org with the maintainer's account (two-factor authentication on), open *Publishing*, and add a **pending publisher** for GitHub Actions:

   | Field | Value |
   |---|---|
   | PyPI project name | `rclass` |
   | Owner | `TwoMuchLabs` |
   | Repository name | `reversibility-classes` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   A pending publisher does not hold the name. The project is created by the first successful upload, and until then anyone else can register `rclass`. On 20 November 2026 the name returned "not found" on PyPI. Check again before relying on it.
2. **Create the `pypi` environment** in this repository's settings. Add the maintainer as a required reviewer, so every upload waits for a person to approve it.
3. **Set the repository variable** `PYPI_PUBLISH` to `true`.

### First upload

The first release cut after the setup is uploaded by the `pypi` job once its environment approval is given. A version can be uploaded to PyPI only once and cannot be replaced, so a mistake means a new version number.

### After each upload, check

```bash
python -m venv /tmp/rclass-check && /tmp/rclass-check/bin/pip install "rclass==X.Y.Z"
/tmp/rclass-check/bin/rclass --version
/tmp/rclass-check/bin/rclass stats            # the bundled registry is inside the package
pip download --no-deps --no-binary :none: "rclass==X.Y.Z" -d /tmp/rclass-dl && sha256sum /tmp/rclass-dl/*
```

Compare the hash with `SHA256SUMS` on the GitHub release. They must match, because both come from the same build.

### Only after the first upload has been checked

Change the install instructions in `README.md` and the changelog's install line to the PyPI form, and tick the item in the [distribution roadmap](roadmap.md). Doing this earlier would tell people to run a command that fails, or that installs someone else's package.
