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


def test_check_demands_a_bump(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(version, "released_version", lambda: "0.0.0")
    version.check()  # first release
    monkeypatch.setattr(version, "released_version", lambda: version.manifest_version((ROOT / version.MANIFEST).read_text()))
    with pytest.raises(SystemExit, match="must be higher"):
        version.check()
