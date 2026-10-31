"""Start the platform: ``python -m isla_rl.server`` then open http://localhost:8848.

    --host 0.0.0.0   let other devices on the network open it (tablets, the lab PC)
    --port 8848
    --robot luma_1
    --no-browser     do not open a browser window
"""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m isla_rl.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8848)
    parser.add_argument("--robot", default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    from .app import create_app

    app = create_app(args.robot)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"Isla Studio: {url}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", ws_ping_interval=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
