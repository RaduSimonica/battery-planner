# Contributing

## Versions

Every pull request into `main` is a new version:

1. Bump `version` in `custom_components/battery_planner/manifest.json`
   (`0.x.0` for features, `0.x.y` for fixes, `1.0.0` when the settings are stable).
2. Add a `## [x.y.z] - date` section to `CHANGELOG.md`.

CI fails the pull request if the version is not higher than the latest release
or has no changelog section. When the pull request is merged, a GitHub release
`vx.y.z` is created automatically with that section as its notes. HACS shows it
as the version, and users can update to it from HACS.

## Tests

```
uv venv && uv pip install pytest-homeassistant-custom-component ruff
.venv/bin/ruff check custom_components tests .github --select F,E9,I
.venv/bin/python -m pytest
```
