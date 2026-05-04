from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config
from .orchestrator import run, run_rollback


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genie-config-optimizer",
        description=(
            "Run an evaluation CSV against a Databricks Genie space. Claude judges every "
            "answer and proposes a single consolidated patch to the space's metadata, "
            "which is then auto-applied. Pre/post snapshots are archived locally."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the optimizer over a CSV, or roll back to a prior snapshot.")
    mode = p_run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--csv", help="Path to the evaluation CSV (normal optimization run).")
    mode.add_argument(
        "--rollback",
        metavar="FOLDER",
        help=(
            "Path to a previous run folder containing before.json. Restores the "
            "Genie space to that snapshot via PATCH and archives a new run dir "
            "with the pre-rollback state as before.json and the restored state "
            "as after.json. Mutually exclusive with --csv."
        ),
    )
    p_run.add_argument(
        "--space-id",
        default=None,
        help="Override the genie_space_id from .config.",
    )
    p_run.add_argument(
        "--archive-dir",
        default="optimizer_runs",
        help="Root directory for per-run archive folders. Defaults to ./optimizer_runs.",
    )
    p_run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N rows (smoke-test the pipeline cheaply).",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the PATCH /spaces/{id} call. Still writes before.json/after.json/meta.json/summary.md.",
    )
    p_run.add_argument(
        "--env",
        default=".env",
        help="Path to the .env file (default: ./.env).",
    )
    p_run.add_argument(
        "--config",
        default=".config",
        help="Path to the .config file (default: ./.config).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "run":
        try:
            cfg = load_config(env_path=args.env, config_path=args.config)
        except ConfigError as e:
            print(f"Config error: {e}", file=sys.stderr)
            return 2

        if args.rollback:
            return run_rollback(
                cfg,
                rollback_folder=args.rollback,
                space_id_override=args.space_id,
                archive_dir=args.archive_dir,
                dry_run=args.dry_run,
            )

        return run(
            cfg,
            csv_path=args.csv,
            space_id_override=args.space_id,
            archive_dir=args.archive_dir,
            limit=args.limit,
            dry_run=args.dry_run,
        )

    parser.error(f"Unknown command: {args.cmd}")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    raise SystemExit(main())
