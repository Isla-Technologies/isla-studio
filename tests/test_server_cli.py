"""The desktop owns its server through a pipe; losing the parent must cleanly close the app."""

from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace

import pytest

from isla_rl import paths
from isla_rl.server import __main__ as launcher


@pytest.mark.parametrize("stdin", [SimpleNamespace(buffer=io.BytesIO(b"parent data")), None])
def test_stdin_closure_requests_a_clean_stop(monkeypatch, stdin):
    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(sys, "stdin", stdin)
    launcher._stop_when_closed(server)
    assert server.should_exit


@pytest.mark.parametrize("early_close", [False, True])
def test_losing_the_desktop_runs_lifespan_shutdown(tmp_path, early_close):
    # A tiny app tests the real Uvicorn lifecycle without opening hardware or the user's data.
    events = tmp_path / "lifespan.txt"
    script = """
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from isla_rl.server import app, __main__

events = Path(sys.argv[1])
@asynccontextmanager
async def lifespan(application):
    events.write_text('starting\\n', encoding='utf-8')
    await asyncio.sleep(0.15)
    print('STARTED', flush=True)
    yield
    with events.open('a', encoding='utf-8') as output:
        output.write('stopped\\n')

app.create_app = lambda robot: FastAPI(lifespan=lifespan)
raise SystemExit(__main__.main(['--port', '0', '--no-browser', '--stop-with-stdin']))
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(events)],
        cwd=paths.ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if not early_close:
            for line in process.stdout:
                if line.strip() == "STARTED":
                    break
            else:
                pytest.fail(f"Server exited before startup: {process.stderr.read()}")
            # Bytes on an open pipe do not end the server; only closing it does.
            process.stdin.write("parent still alive\n")
            process.stdin.flush()
            assert process.poll() is None
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        assert events.read_text(encoding="utf-8") == "starting\nstopped\n"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
