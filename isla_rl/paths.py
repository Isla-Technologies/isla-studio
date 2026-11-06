"""Where the platform keeps robots, training runs, settings and Studio's built interface."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLED_ROBOTS = ROOT / "robots"  # The robots that come with Studio.
ROBOTS = Path(os.environ.get("ISLA_ROBOTS", BUNDLED_ROBOTS))
RUNS = Path(os.environ.get("ISLA_RUNS", ROOT / "runs"))
POLICIES = Path(os.environ.get("ISLA_POLICIES", ROOT / "policies"))
SETTINGS = Path(os.environ.get("ISLA_SETTINGS", ROOT / "settings"))
CACHE = Path(os.environ.get("ISLA_CACHE", ROOT / ".cache"))
UI_DIST = ROOT / "ui" / "dist"
EXAMPLES = ROOT / "examples"  # Trained runs that come with Studio, copied into RUNS once.
DEFAULT_ROBOT = "luma_1"


def ensure_dirs() -> None:
    for folder in (ROBOTS, RUNS, POLICIES, SETTINGS, CACHE):
        folder.mkdir(parents=True, exist_ok=True)
