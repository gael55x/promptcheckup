"""The version is declared once and every release file agrees with it."""

import re
import tomllib
from pathlib import Path

import promptcheckup

ROOT = Path(__file__).resolve().parents[1]


def test_version_has_one_source():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "src/promptcheckup/__init__.py"


def test_changelog_and_citation_match_the_version():
    version = promptcheckup.__version__
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")

    assert re.search(r"^## \[(.+?)\]", changelog, re.M)[1] == version
    assert re.search(r"^version: (.+)$", citation, re.M)[1] == version


def test_release_workflow_pins_every_action_to_a_commit():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses: (\S+)", workflow)

    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref) for ref in uses)
    assert "id-token: write" in workflow and "password" not in workflow
