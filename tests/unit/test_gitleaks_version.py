from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GITLEAKS_VERSION = re.compile(
    r"^\s+GITLEAKS_VERSION:\s*(\d+)\.(\d+)\.(\d+)\s*$", re.MULTILINE
)
# The first release that reads the `[[allowlists]]` table.
ALLOWLISTS_TABLE_SINCE = (8, 25, 0)


def test_ci_gitleaks_reads_the_allowlists_table() -> None:
    """An older gitleaks ignores `[[allowlists]]` without a warning, so the
    test placeholders .gitleaks.toml allowlists fail the scan in CI while a
    newer local gitleaks passes them."""
    assert "[[allowlists]]" in (PROJECT_ROOT / ".gitleaks.toml").read_text(
        encoding="utf-8"
    )
    workflow = (PROJECT_ROOT / ".github/workflows/_ci.yml").read_text(encoding="utf-8")

    match = GITLEAKS_VERSION.search(workflow)

    assert match, "_ci.yml must pin GITLEAKS_VERSION for gitleaks-action"
    assert tuple(int(part) for part in match.groups()) >= ALLOWLISTS_TABLE_SINCE
