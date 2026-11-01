"""Where the platform keeps robots, training runs, settings and Studio's built interface."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROBOTS = Path(os.environ.get("ISLA_ROBOTS", ROOT / "robots"))
RUNS = Path(os.environ.get("ISLA_RUNS", ROOT / "runs"))
POLICIES = Path(os.environ.get("ISLA_POLICIES", ROOT / "policies"))
SETTINGS = Path(os.environ.get("ISLA_SETTINGS", ROOT / "settings"))
CACHE = Path(os.environ.get("ISLA_CACHE", ROOT / ".cache"))
UI_DIST = ROOT / "ui" / "dist"
DEFAULT_ROBOT = "luma_1"


def ensure_dirs() -> None:
    for folder in (ROBOTS, RUNS, POLICIES, SETTINGS, CACHE):
        folder.mkdir(parents=True, exist_ok=True)
