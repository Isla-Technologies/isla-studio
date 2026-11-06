"""Build a self-contained Windows installer: python scripts/build-desktop.py.

Requires Node, Rust and Microsoft's C++ Build Tools on the build computer only.
The official portable CPython archive is verified against its published SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
PAYLOAD = DESKTOP / "payload"
CACHE = ROOT / ".scratch" / "desktop-cache"
PYTHON = "3.13.13"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-amd64.zip"
PYTHON_SHA256 = "5f066036aebfaa3aa9aa4d52f852022f24641e7b5bc52f2bf8bb7acbe7bab958"
BUNDLED_ROBOTS = ("luma_1",)


def run(*args: str | Path, cwd: Path = ROOT, env: dict | None = None) -> None:
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def replace_tree(source: Path, target: Path) -> None:
    # Only replace generated payloads, never a source tree or a user's projects.
    if not target.resolve().is_relative_to(PAYLOAD.resolve()):
        raise ValueError(f"Not a generated payload: {target}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def prepare() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / f"python-{PYTHON}-amd64.zip"
    if not archive.exists():
        print(f"Downloading CPython {PYTHON} from python.org…", flush=True)
        urllib.request.urlretrieve(PYTHON_URL, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise ValueError(f"CPython archive checksum failed: remove {archive} and try again")
    runtime = PAYLOAD / "python"
    marker = runtime / ".studio-runtime"
    if not marker.exists() or marker.read_text().strip() != PYTHON:
        if runtime.exists():
            if not runtime.resolve().is_relative_to(PAYLOAD.resolve()):
                raise ValueError("Runtime is outside the generated payload")
            shutil.rmtree(runtime)
        runtime.mkdir(parents=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(runtime)
        run(runtime / "python.exe", "-m", "ensurepip", "--upgrade")
        marker.write_text(PYTHON + "\n", encoding="utf-8", newline="\n")
    run(runtime / "python.exe", "-m", "pip", "install", "--disable-pip-version-check", "-r", ROOT / "requirements.txt")
    studio = PAYLOAD / "studio"
    studio.mkdir(parents=True, exist_ok=True)
    for name in ("isla_rl", "examples"):
        replace_tree(ROOT / name, studio / name)
    # robots/ is also the checkout user's working hangar. Ship an explicit list,
    # so another imported robot or its settings never enters a release by accident.
    robot_target = studio / "robots"
    if robot_target.exists():
        if not robot_target.resolve().is_relative_to(PAYLOAD.resolve()):
            raise ValueError("Robots are outside the generated payload")
        shutil.rmtree(robot_target)
    robot_target.mkdir(parents=True)
    for name in BUNDLED_ROBOTS:
        replace_tree(ROOT / "robots" / name, robot_target / name)
    replace_tree(ROOT / "ui" / "dist", studio / "ui" / "dist")
    for name in ("requirements-gpu.txt", "LICENSE"):
        shutil.copy2(ROOT / name, studio / name)
    # Optional GPU distributions may use .pth files for their DLL directories.
    # Every child interpreter loads them too, including training and pip.
    (runtime / "Lib" / "site-packages" / "sitecustomize.py").write_text(
        'import os, site\n'
        'if path := os.environ.get("PIP_TARGET"):\n'
        '    site.addsitedir(path)\n',
        encoding="utf-8", newline="\n",
    )
    fonts = DESKTOP / "splash" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for name in ("Inter-Regular.ttf", "Michroma-Regular.ttf", "LICENSE-Inter.txt", "OFL-Michroma.txt"):
        shutil.copy2(ROOT / "ui" / "src" / "brand" / "fonts" / name, fonts / name)
    packages = subprocess.check_output([str(runtime / "python.exe"), "-m", "pip", "freeze"], text=True)
    (PAYLOAD / "runtime-packages.txt").write_text(packages, encoding="utf-8", newline="\n")
    (PAYLOAD / "build.json").write_text(json.dumps({"python": PYTHON, "python_sha256": PYTHON_SHA256}, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Prepared {PAYLOAD}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Prepare the portable runtime without compiling Rust")
    parser.add_argument("--skip-ui", action="store_true", help="Use the existing production interface")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The Windows installer must currently be built on Windows")
    npm = shutil.which("npm.cmd")
    if not npm:
        parser.error("Install Node.js 22 or newer on the build computer")
    if not args.skip_ui:
        run(npm, "ci", cwd=ROOT / "ui")
        run(npm, "run", "build", cwd=ROOT / "ui")
    prepare()
    if not args.prepare_only:
        run(npm, "ci", cwd=DESKTOP)
        run(npm, "run", "build", cwd=DESKTOP)
        for installer in (DESKTOP / "target" / "release" / "bundle" / "nsis").glob("*.exe"):
            print(f"Windows installer: {installer}")


if __name__ == "__main__":
    main()
