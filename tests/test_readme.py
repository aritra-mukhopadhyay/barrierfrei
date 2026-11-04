"""The README is the project's front door: its commands and file paths must stay real."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_commands_exist():
    modules = set(re.findall(r"python -m barrierfrei\.(\w+)", README))
    assert modules, "the README should show how to run the pipeline"
    for name in modules:
        assert (ROOT / "barrierfrei" / f"{name}.py").exists(), name


def test_readme_paths_exist():
    for path in set(re.findall(r"`((?:barrierfrei|hello|tools|tests)/[\w/]+\.py)`", README)):
        assert (ROOT / path).exists(), path


def test_readme_links_licence_and_video():
    assert "[LICENSE](LICENSE)" in README
    assert "Video (3 min): https://youtu.be/" in README
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
