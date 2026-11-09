# Contributing

## Branches and releases

| Branch | What it is | Release it publishes |
|---|---|---|
| `development` | day-to-day work | none |
| `staging` | things that are ready to try | a **snapshot**, a GitHub pre-release (`vX.Y.Z-snapshot.N`) |
| `main` | the stable version | a **stable release** (`vX.Y.Z`), which HACS installs by default |

Flow: work on `development`, open a pull request into `staging` when something
is ready to try, and once in a while open a pull request from `staging` into
`main` to release everything that has gathered there.

### Snapshots (pull request into `staging`)

1. Set `version` in `custom_components/battery_planner/manifest.json` to the next snapshot
   of the coming release: `0.5.0-snapshot.1`, then `0.5.0-snapshot.2`, and so on.
   The number after `snapshot.` goes up with every pull request; the `0.5.0` part is the
   stable version the snapshots lead to (`0.x.0` for features, `0.x.y` for fixes).
2. Add a `## [0.5.0-snapshot.2] - date` section to `CHANGELOG.md` with what this
   snapshot adds.

CI fails the pull request if the version is not a snapshot, is not higher than
every tag so far, or has no changelog section. Merging publishes a pre-release
with that section as its notes. HACS offers it only to users who turn on
*Show beta versions* for the repository (repository menu in HACS, or
*Redownload* and pick the version).

### Stable releases (pull request from `staging` into `main`)

1. Set `version` to the stable number, e.g. `0.5.0`.
2. Replace the snapshot sections in `CHANGELOG.md` with one `## [0.5.0] - date`
   section that describes the whole release (keep the same headings: Added,
   Changed, Fixed).

CI fails the pull request if the version is a snapshot, is not higher than the
latest *stable* release, or has no changelog section. Merging publishes the
release, which HACS shows as the version and offers to everybody. Afterwards,
merge `main` back into `staging` and `development` so they carry the new version
and changelog.

## Tests

```
uv venv && uv pip install pytest-homeassistant-custom-component ruff
.venv/bin/ruff check custom_components tests .github --select F,E9,I
.venv/bin/python -m pytest
```
