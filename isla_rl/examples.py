"""The example runs that come with Isla Studio: trained policies to watch, test and continue.

Studio copies each one into ``runs/`` the first time it starts with it, as design software
opens with sample projects. ``runs/.examples`` remembers which were offered, so a run you
delete stays deleted, and a run of your own with the same name is never replaced.
"""

from __future__ import annotations

import json
import os
import shutil

from . import paths

RECORD = ".examples"


def install() -> list[str]:
    """Copy the examples not offered before into ``runs/``; the names copied."""
    source = paths.EXAMPLES / "runs"
    if not source.is_dir():
        return []
    record = paths.RUNS / RECORD
    offered = set(record.read_text(encoding="utf-8").split()) if record.exists() else set()
    copied = []
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        if folder.name in offered:
            continue
        offered.add(folder.name)
        target = paths.RUNS / folder.name
        if target.exists():
            continue
        try:
            shutil.copytree(folder, target)
            _date(target)
        except OSError:
            shutil.rmtree(target, ignore_errors=True)
            offered.discard(folder.name)  # Offered again next start.
            continue
        copied.append(folder.name)
    paths.RUNS.mkdir(parents=True, exist_ok=True)
    record.write_text("".join(f"{name}\n" for name in sorted(offered)), encoding="utf-8")
    return copied


def _date(run) -> None:
    """Date the copy from when it was trained, so runs list in the order they were made."""
    try:
        when = float(json.loads((run / "status.json").read_text(encoding="utf-8"))["updated"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    for file in run.iterdir():
        os.utime(file, (when, when))
