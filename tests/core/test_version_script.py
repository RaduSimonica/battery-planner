"""The release helper that CI uses."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
spec = importlib.util.spec_from_file_location("version_script", ROOT / ".github/scripts/version.py")
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)


def test_semver_ordering():
    assert version.parse("0.10.0") > version.parse("0.9.9")  # numeric, not text, order
    assert version.parse("1.0.0") > version.parse("0.99.99")
    for bad in ("1.0", "v1.0.0", "1.0.0-beta", ""):
        with pytest.raises(SystemExit):
            version.parse(bad)


def test_changelog_sections():
    text = "# Changelog\n\n## [0.2.0] - 2026-12-01\n\n### Added\n- B\n\n## [0.1.0] - 2026-11-08\n\n- A\n"
    assert version.changelog_section(text, "0.2.0") == "### Added\n- B"
    assert version.changelog_section(text, "0.1.0") == "- A"
    assert version.changelog_section(text, "0.3.0") is None
    assert version.changelog_section(text, "0.1") is None


def test_repo_is_consistent():
    """The version in the manifest has a changelog section, as CI will demand."""
    current = version.manifest_version((ROOT / version.MANIFEST).read_text())
    version.parse(current)
    assert version.changelog_section((ROOT / version.CHANGELOG).read_text(), current)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_baseline_is_the_latest_release_tag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")
    assert version.released_version() == "0.0.0"  # first release may be 0.1.0
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    for tag in ("v0.9.0", "v0.10.0", "v0.2.0", "not-a-version", "v1.2"):
        _git(tmp_path, "tag", tag)
    assert version.released_version() == "0.10.0"  # numeric order, junk tags ignored


def test_snapshots_sort_below_their_release():
    order = ["0.4.0-snapshot.1", "0.4.0-snapshot.2", "0.4.0-snapshot.10", "0.4.0", "0.4.1-snapshot.1", "0.5.0"]
    assert sorted(order, key=version.parse) == order  # numeric, not text, order
    assert version.is_snapshot("0.4.0-snapshot.1") and not version.is_snapshot("0.4.0")
    for bad in ("0.4.0-snapshot", "0.4.0-snapshot.", "0.4.0-snapshot.x", "0.4.0-rc.1", "0.4.0b1"):
        with pytest.raises(SystemExit):
            version.parse(bad)


def test_baselines_for_snapshots_and_stable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    for tag in ("v0.3.0", "v0.4.0-snapshot.1", "v0.4.0-snapshot.2"):
        _git(tmp_path, "tag", tag)
    assert version.released_version() == "0.4.0-snapshot.2"  # snapshots count for snapshots
    assert version.released_version(stable_only=True) == "0.3.0"  # but not for stable releases


def _project(tmp_path, monkeypatch, manifest_version, sections, latest=None, stable=None):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(f'{{"version": "{manifest_version}"}}')
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n" + "".join(f"## [{s}] - 2026-11-09\n\n- x\n\n" for s in sections))
    monkeypatch.setattr(version, "MANIFEST", str(manifest))
    monkeypatch.setattr(version, "CHANGELOG", str(changelog))
    monkeypatch.setattr(
        version, "released_version", lambda stable_only=False: (stable if stable_only else latest) or "0.0.0"
    )


def test_staging_accepts_a_new_snapshot(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "0.5.0-snapshot.2", ["0.5.0-snapshot.2"], latest="0.5.0-snapshot.1", stable="0.4.0")
    version.check("staging")


def test_staging_rejects_a_stale_or_stable_version(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "0.5.0-snapshot.1", ["0.5.0-snapshot.1"], latest="0.5.0-snapshot.1")
    with pytest.raises(SystemExit, match="must be higher"):
        version.check("staging")
    _project(tmp_path, monkeypatch, "0.5.0", ["0.5.0"], latest="0.4.0")
    with pytest.raises(SystemExit, match="needs a snapshot"):
        version.check("staging")


def test_main_needs_a_stable_version_above_the_last_stable(tmp_path, monkeypatch):
    # Snapshots do not count: the stable release only has to beat the last stable one.
    _project(tmp_path, monkeypatch, "0.5.0", ["0.5.0"], latest="0.5.0-snapshot.3", stable="0.4.0")
    version.check("main")
    _project(tmp_path, monkeypatch, "0.5.0-snapshot.4", ["0.5.0-snapshot.4"], latest="0.5.0-snapshot.3", stable="0.4.0")
    with pytest.raises(SystemExit, match="needs a stable"):
        version.check("main")
    _project(tmp_path, monkeypatch, "0.4.0", ["0.4.0"], latest="0.5.0-snapshot.3", stable="0.4.0")
    with pytest.raises(SystemExit, match="must be higher"):
        version.check("main")


def test_a_version_needs_its_changelog_section(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "0.5.0-snapshot.1", ["0.4.0"], stable="0.4.0")
    with pytest.raises(SystemExit, match="no '## \\[0.5.0-snapshot.1\\]' section"):
        version.check("staging")


def test_release_refuses_the_wrong_kind_for_the_branch(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "0.5.0-snapshot.1", ["0.5.0-snapshot.1"])
    with pytest.raises(SystemExit, match="publishes stable releases"):
        version.release("main")
    _project(tmp_path, monkeypatch, "0.5.0", ["0.5.0"])
    with pytest.raises(SystemExit, match="publishes snapshots"):
        version.release("staging")


def test_release_creates_a_prerelease_for_snapshots(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "0.5.0-snapshot.1", ["0.5.0-snapshot.1"])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[:3] == ["gh", "release", "view"] else 0)

    monkeypatch.setattr(version.subprocess, "run", fake_run)
    version.release("staging")
    create = next(c for c in calls if c[:3] == ["gh", "release", "create"])
    assert "--prerelease" in create and "v0.5.0-snapshot.1" in create and "--latest" not in create
    calls.clear()
    _project(tmp_path, monkeypatch, "0.5.0", ["0.5.0"])
    version.release("main")
    create = next(c for c in calls if c[:3] == ["gh", "release", "create"])
    assert "--latest" in create and "--prerelease" not in create
