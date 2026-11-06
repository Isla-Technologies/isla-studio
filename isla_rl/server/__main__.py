"""Start the platform: ``python -m isla_rl.server`` then open http://localhost:8848.

    --host 0.0.0.0     let other devices on the network open it (tablets, the lab PC)
    --port 8848
    --robot luma_1
    --no-browser       do not open a browser window
    --stop-with-stdin  stop, cleanly, when standard input closes: how the desktop app
                       ends its server, and why a crashed app never leaves one behind
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m isla_rl.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8848)
    parser.add_argument("--robot", default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop-with-stdin", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    from .app import create_app

    app = create_app(args.robot)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"Isla Studio: {url}", flush=True)

    class StudioServer(uvicorn.Server):
        async def main_loop(self) -> None:
            if args.stop_with_stdin:
                # Start after startup: older Uvicorn versions skip lifespan shutdown
                # if should_exit is set earlier. A closed pipe still reads EOF here.
                threading.Thread(target=_stop_when_closed, args=(self,), name="stop-with-stdin", daemon=True).start()
            await super().main_loop()

    server = StudioServer(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", ws_ping_interval=20))
    server.run()
    return 0


def _stop_when_closed(server) -> None:
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError, AttributeError):
        pass  # No standard input at all: stop as if it closed.
    server.should_exit = True


if __name__ == "__main__":
    raise SystemExit(main())
