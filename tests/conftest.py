"""Tests read the robots in the repository but write only to temporary folders."""

from __future__ import annotations

import pytest

from isla_rl import paths


@pytest.fixture(autouse=True)
def scratch_folders(tmp_path_factory, monkeypatch):
    """Caches, runs, policies and settings in a temporary folder, per test session."""
    root = tmp_path_factory.getbasetemp() / "isla"
    for name in ("CACHE", "RUNS", "POLICIES", "SETTINGS"):
        folder = root / name.lower()
        folder.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(paths, name, folder)
    return root


@pytest.fixture
def hangar(tmp_path, monkeypatch):
    """An empty hangar (robots folder) of the test's own."""
    folder = tmp_path / "robots"
    folder.mkdir()
    monkeypatch.setattr(paths, "ROBOTS", folder)
    return folder
