#!/bin/sh
# Start Isla Studio on Linux or macOS. The first start sets it up (needs the internet).
# Options pass through, for example:  ./run.sh --host 0.0.0.0
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PYTHON=""
  for candidate in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      PYTHON=$candidate
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    echo "Isla Studio needs Python 3.10 or newer: https://www.python.org/downloads/"
    exit 1
  fi
  echo "Setting up Isla Studio for the first time with $($PYTHON --version). This takes a few minutes."
  if ! "$PYTHON" -m venv .venv; then
    echo "Creating the environment failed. On Ubuntu or Debian, install venv first: sudo apt install python3-venv"
    rm -rf .venv
    exit 1
  fi
fi

# Install, or update, the packages when requirements.txt has changed since the last start.
if ! cmp -s requirements.txt .venv/requirements.installed; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  cp requirements.txt .venv/requirements.installed
fi

exec .venv/bin/python -m isla_rl.server "$@"
