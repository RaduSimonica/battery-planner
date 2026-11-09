#!/usr/bin/env python3
"""Release helper: enforce a version bump on pull requests, publish on merge.

    version.py check     # pull request: version must be higher than the latest release
    version.py release   # push to main: create the GitHub release (once per version)

Releases are the baseline, not the version on main: the first release can be
0.1.0 even though main already carries that number. The version lives in custom_components/battery_planner/manifest.json and every
version needs a "## [X.Y.Z]" section in CHANGELOG.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

MANIFEST = "custom_components/battery_planner/manifest.json"
CHANGELOG = "CHANGELOG.md"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        raise SystemExit(f"'{version}' is not a version like 1.2.3")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def changelog_section(text: str, version: str) -> str | None:
    """Body of the '## [version]' section, or None if there is none."""
    heading = re.compile(rf"^## \[{re.escape(version)}\].*$", re.MULTILINE)
    start = heading.search(text)
    if not start:
        return None
    rest = text[start.end():]
    nxt = re.search(r"^## \[", rest, re.MULTILINE)
    return (rest[: nxt.start()] if nxt else rest).strip()


def manifest_version(raw: str) -> str:
    return json.loads(raw)["version"]


def released_version() -> str:
    """Highest version tag (vX.Y.Z) in the repository; 0.0.0 before the first release."""
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"], capture_output=True, text=True
    ).stdout.split()
    versions = [t[1:] for t in tags if SEMVER.match(t[1:])]
    return max(versions, key=parse, default="0.0.0")


def check() -> None:
    new = manifest_version(Path(MANIFEST).read_text())
    old = released_version()
    if parse(new) <= parse(old):
        raise SystemExit(
            f"Version {new} must be higher than the latest release ({old}). "
            f"Bump 'version' in {MANIFEST} and add a '## [{new}]' section to {CHANGELOG}."
        )
    if changelog_section(Path(CHANGELOG).read_text(), new) is None:
        raise SystemExit(f"{CHANGELOG} has no '## [{new}]' section.")
    print(f"OK: {old} -> {new}")


def release() -> None:
    version = manifest_version(Path(MANIFEST).read_text())
    parse(version)
    tag = f"v{version}"
    exists = subprocess.run(["gh", "release", "view", tag], capture_output=True)
    if exists.returncode == 0:
        print(f"Release {tag} already exists, nothing to do.")
        return
    notes = changelog_section(Path(CHANGELOG).read_text(), version)
    if notes is None:
        raise SystemExit(f"{CHANGELOG} has no '## [{version}]' section.")
    subprocess.run(
        [
            "gh", "release", "create", tag,
            "--target", os.environ.get("GITHUB_SHA", "main"),
            "--title", tag,
            "--notes", notes,
        ],
        check=True,
    )
    print(f"Created release {tag}.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        check()
    elif command == "release":
        release()
    else:
        raise SystemExit(__doc__)
