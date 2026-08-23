from __future__ import annotations

import importlib.metadata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI job
    import tomli as tomllib

import augmentworks_target

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_and_module_versions_match() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]

    assert project["name"] == "augmentworks-target"
    assert project["dynamic"] == ["version"]
    assert importlib.metadata.version("augmentworks-target") == augmentworks_target.__version__


def test_release_metadata_is_explicit() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert project["requires-python"] == ">=3.10"
