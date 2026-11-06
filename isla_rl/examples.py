"""The examples that come with Isla Studio: trained runs to watch, test and continue, and robots.

Studio copies each example run into ``runs/`` the first time it starts with it, as design
software opens with sample projects. ``runs/.examples`` remembers which were offered, so a
run you delete stays deleted, and a run of your own with the same name is never replaced.

When Studio keeps its robots outside its own folder (the desktop app keeps them in the
user's documents), the robots it comes with are offered the same way, recorded in
``robots/.bundled``.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from . import paths

RECORD = ".examples"
ROBOTS_RECORD = ".bundled"


def install() -> list[str]:
    """Copy the example runs not offered before into ``runs/``; the names copied."""
    return _offer(paths.EXAMPLES / "runs", paths.RUNS, RECORD, _date)


def install_robots() -> list[str]:
    """Copy the robots Studio comes with, not offered before, into ``robots/``; the names copied."""
    if paths.ROBOTS.resolve() == paths.BUNDLED_ROBOTS.resolve():
        return []
    return _offer(paths.BUNDLED_ROBOTS, paths.ROBOTS, ROBOTS_RECORD)


def _offer(source: Path, target_root: Path, record_name: str, finish: Callable[[Path], None] | None = None) -> list[str]:
    if not source.is_dir():
        return []
    target_root.mkdir(parents=True, exist_ok=True)
    record = target_root / record_name
    offered = set(record.read_text(encoding="utf-8").split()) if record.exists() else set()
    copied = []
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        if folder.name in offered:
            continue
        offered.add(folder.name)
        target = target_root / folder.name
        try:
            target.mkdir()  # Reserve the name without ever replacing a user's folder.
        except FileExistsError:
            continue
        except OSError:
            offered.discard(folder.name)
            continue
        try:
            shutil.copytree(folder, target, dirs_exist_ok=True)
            if finish:
                finish(target)
        except OSError:
            shutil.rmtree(target, ignore_errors=True)
            offered.discard(folder.name)  # Offered again next start.
            continue
        copied.append(folder.name)
    record.write_bytes("".join(f"{name}\n" for name in sorted(offered)).encode("utf-8"))
    return copied


def _date(run: Path) -> None:
    """Date the copy from when it was trained, so runs list in the order they were made."""
    try:
        when = float(json.loads((run / "status.json").read_text(encoding="utf-8"))["updated"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    for file in run.iterdir():
        os.utime(file, (when, when))
