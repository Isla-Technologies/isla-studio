"""Train from the command line, or as the web app's training process; try what it learned.

    python -m isla_rl.learn train runs/walk-1 [--device gpu|cpu|auto] [--iterations N]
    python -m isla_rl.learn evaluate runs/walk-1 [--seed 0]

Progress is printed as one JSON object per line. Create ``runs/<name>/stop`` (or
press Ctrl+C) to stop with a checkpoint; running the same command continues.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .. import paths
from .device import DEVICES
from .trainer import STOP, Run, train


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m isla_rl.learn")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("train", help="Train a run in its folder")
    run_cmd.add_argument("run", help="The run's folder (or its name under runs/)")
    run_cmd.add_argument("--device", choices=DEVICES, default="auto")
    run_cmd.add_argument("--iterations", type=int, default=None)
    run_cmd.add_argument("--threads", type=int, default=None)
    run_cmd.add_argument("--no-swarm", action="store_true", help="Do not report practice robots' motion")
    try_cmd = commands.add_parser("evaluate", help="Try a run's policy in the live physics; writes evaluation.json")
    try_cmd.add_argument("run", help="The run's folder (or its name under runs/)")
    try_cmd.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    folder = Path(args.run)
    if not folder.exists():
        folder = paths.RUNS / args.run
    run = Run(folder)
    if args.command == "evaluate":
        return _evaluate(run, args.seed)
    stop_file = run.path(STOP)
    stop_file.unlink(missing_ok=True)

    def report(message: dict) -> None:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    try:
        train(
            run,
            report=report,
            should_stop=stop_file.exists,
            iterations=args.iterations,
            device=args.device,
            threads=args.threads,
            swarm=not args.no_swarm,
        )
    except KeyboardInterrupt:
        report({"type": "stopped", "reason": "interrupted"})
        return 0
    except Exception as error:  # Reported to the web app, then raised for the log.  # noqa: BLE001
        report({"type": "error", "message": str(error), "trace": traceback.format_exc()})
        return 1
    return 0


def _evaluate(run: Run, seed: int) -> int:
    from .evaluate import describe, evaluate

    policy = run.policy()
    if policy is None:
        print(f"{run.name} has no policy yet: it saves one after its first round of practice.", file=sys.stderr)
        return 1
    report = evaluate(policy, run.profile(), seed=seed)
    report["steps"] = run.summary()["steps"]  # How far the run had trained when its policy was tested.
    report["tested"] = datetime.now().isoformat(timespec="seconds")
    run.path("evaluation.json").write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    print(describe(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
