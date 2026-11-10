#!/usr/bin/env python3
"""Release helper: enforce a version bump on pull requests, publish on merge.

    version.py check     # pull request: the version must be new and of the right kind
    version.py release   # push to main or staging: create the GitHub release

Two kinds of release:

* **Snapshots** (``X.Y.Z-snapshot.N``) come from the ``staging`` branch and are
  published as GitHub pre-releases. HACS offers them only to users who turn on
  *Show beta versions* for the repository.
* **Stable releases** (``X.Y.Z``) come from ``main``.

A pull request into ``staging`` must carry a snapshot version higher than every
tag so far; a pull request into ``main`` must carry a stable version higher than
the latest stable release. Every version needs a "## [version]" section in
CHANGELOG.md. The version lives in custom_components/battery_planner/manifest.json.
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
STAGING = "staging"
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-snapshot\.(\d+))?$")


def parse(version: str) -> tuple[int, int, int, int, int]:
    """Sortable form: a snapshot sorts below the stable release it leads up to."""
    match = VERSION.match(version)
    if not match:
        raise SystemExit(
            f"'{version}' is not a version like 1.2.3 (stable) or 1.2.3-snapshot.1 (snapshot)"
        )
    major, minor, patch, snapshot = match.groups()
    if snapshot is None:
        return int(major), int(minor), int(patch), 1, 0
    return int(major), int(minor), int(patch), 0, int(snapshot)


def is_snapshot(version: str) -> bool:
    parse(version)
    return "-snapshot." in version


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


def released_version(stable_only: bool = False) -> str:
    """Highest version tag (vX.Y.Z or vX.Y.Z-snapshot.N); 0.0.0 before the first release."""
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"], capture_output=True, text=True
    ).stdout.split()
    versions = [t[1:] for t in tags if VERSION.match(t[1:])]
    if stable_only:
        versions = [v for v in versions if not is_snapshot(v)]
    return max(versions, key=parse, default="0.0.0")


def check(target: str = "main") -> None:
    """Validate the manifest version for a pull request into ``target``."""
    new = manifest_version(Path(MANIFEST).read_text())
    snapshot = target == STAGING
    kind = "snapshot (X.Y.Z-snapshot.N)" if snapshot else "stable (X.Y.Z)"
    if is_snapshot(new) != snapshot:
        raise SystemExit(
            f"A pull request into '{target}' needs a {kind} version, but {MANIFEST} has {new}."
        )
    old = released_version(stable_only=not snapshot)
    if parse(new) <= parse(old):
        raise SystemExit(
            f"Version {new} must be higher than the latest release ({old}). "
            f"Bump 'version' in {MANIFEST} and add a '## [{new}]' section to {CHANGELOG}."
        )
    if changelog_section(Path(CHANGELOG).read_text(), new) is None:
        raise SystemExit(f"{CHANGELOG} has no '## [{new}]' section.")
    print(f"OK: {old} -> {new}")


def release(branch: str = "main") -> None:
    version = manifest_version(Path(MANIFEST).read_text())
    snapshot = branch == STAGING
    if is_snapshot(version) != snapshot:
        raise SystemExit(
            f"Branch '{branch}' publishes {'snapshots' if snapshot else 'stable releases'}, "
            f"but {MANIFEST} has {version}."
        )
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
            "--target", os.environ.get("GITHUB_SHA", branch),
            "--title", f"{tag} (snapshot)" if snapshot else tag,
            "--notes", notes,
            "--prerelease" if snapshot else "--latest",
        ],
        check=True,
    )
    print(f"Created {'pre-release' if snapshot else 'release'} {tag}.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        check(os.environ.get("GITHUB_BASE_REF") or "main")
    elif command == "release":
        release(os.environ.get("GITHUB_REF_NAME") or "main")
    else:
        raise SystemExit(__doc__)
