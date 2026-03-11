from __future__ import annotations

from pathlib import Path

from tools.repo_auditor import RepoAuditor

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def test_detects_python_language():
    report = RepoAuditor(FIXTURE_REPO).audit()
    assert ".py" in report.languages


def test_detects_package_managers():
    report = RepoAuditor(FIXTURE_REPO).audit()
    assert "pip" in report.package_managers
    assert "npm" in report.package_managers


def test_detects_test_frameworks():
    report = RepoAuditor(FIXTURE_REPO).audit()
    assert "pytest" in report.test_frameworks


def test_detects_ci_config():
    report = RepoAuditor(FIXTURE_REPO).audit()
    assert report.ci_configs


def test_marker_files_detected():
    report = RepoAuditor(FIXTURE_REPO).audit()
    assert report.marker_files["pyproject.toml"] is True
    assert report.marker_files["package.json"] is True


def test_skips_ignored_dirs(tmp_path):
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "evil.py").write_text("x = 1\n", encoding="utf-8")
    report = RepoAuditor(tmp_path).audit()
    assert report.languages.get(".py", 0) == 0


def test_root_path_in_report():
    report = RepoAuditor(FIXTURE_REPO).audit()
    assert report.root_path == str(FIXTURE_REPO)
