"""The version lives in three places and must match; CHANGELOG.md must cover it."""

import json
import re
import tomllib

import aionuvo

from .test_zone import ROOT


def manifest_version() -> str:
    return json.loads((ROOT / "custom_components/nuvo_player/manifest.json").read_text())["version"]


def test_versions_match():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert manifest_version() == pyproject == aionuvo.__version__


def test_changelog_has_current_version():
    headings = re.findall(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert headings and headings[0] == manifest_version()
