"""Smoke test so the PR and main checks have at least one passing test.

pytest exits 5 when no tests are collected. The team's check runs under
`bash -e`, so that exit code fails the step before its allowance for 5 is
read. One passing test keeps the exit code at 0.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_exists():
    assert (REPO_ROOT / "README.md").is_file()
