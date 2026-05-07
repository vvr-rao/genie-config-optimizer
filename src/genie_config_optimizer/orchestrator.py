from __future__ import annotations

import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .anthropic_client import Judge
from .archiver import Archive, RunDir
from .config import AppConfig
from .csv_loader import EvalRow, load_csv
from .databricks_client import AskResult, GenieAPIError, GenieClient
from .patcher import apply_patch, patch_summary

_KNOWN_VERDICTS = ("pass", "partial", "fail")


_CONFIRM_MESSAGE = (
    "\n*** WARNING *** - you are about to overwrite the configuration in "
    "your Genie space. Please review the Summary of the proposed changes "
    "before proceeding. Do you want to proceed (Y/n)? "
)


class _ConfirmOutcome(str, Enum):
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    NO_TERMINAL = "no_terminal"


def _confirm_apply(assume_yes: bool = False) -> _ConfirmOutcome:
    """Strict confirm: CONFIRMED only on exact 'Y', or when assume_yes=True.

    Reads from /dev/tty directly so the prompt blocks for real terminal
    input regardless of how sys.stdin is wired. Returns NO_TERMINAL (not
    DECLINED) when /dev/tty cannot be opened, or when it opens but
    readline() returns EOF immediately — the caller treats that as a
    hard error rather than a silent decline.
    """
    if assume_yes:
        return _ConfirmOutcome.CONFIRMED
    sys.stdout.write(_CONFIRM_MESSAGE)
    sys.stdout.flush()
    try:
        with open("/dev/tty") as tty:
            answer = tty.readline()
    except OSError:
        return _ConfirmOutcome.NO_TERMINAL
    if not answer:
        return _ConfirmOutcome.NO_TERMINAL
    return _ConfirmOutcome.CONFIRMED if answer.strip() == "Y" else _ConfirmOutcome.DECLINED


def _tally_verdicts(verdicts: list) -> dict[str, Any]:
    counts = dict.fromkeys(_KNOWN_VERDICTS, 0)
    other = 0
    for v in verdicts:
        key = (getattr(v, "verdict", "") or "").strip().lower()
        if key in counts:
            counts[key] += 1
        else:
            other += 1
    total = sum(counts.values()) + other
    pct = {k: (counts[k] / total * 100.0 if total else 0.0) for k in counts}
    return {"counts": counts, "other": other, "total": total, "percentages": pct}


def _format_verdict_counts(vc: dict[str, Any]) -> str:
    counts = vc["counts"]
    pct = vc["percentages"]
    total = vc["total"]
    parts = [f"{counts[k]} {k} ({pct[k]:.1f}%)" for k in _KNOWN_VERDICTS]
    if vc["other"]:
        parts.append(f"{vc['other']} other")
    return f"{total} rows: " + ", ".join(parts)


def _trim_rows(rows: list[list[Any]] | None, limit: int = 50) -> list[list[Any]] | None:
    if rows is None:
        return None
    if len(rows) <= limit:
        return rows
    return rows[:limit]


def _ask_result_for_judge(eval_row: EvalRow, ar: AskResult | None, error: str | None) -> dict:
    if error is not None:
        return {
            "question": eval_row.question,
            "expected_tables": eval_row.tables,
            "expected_answer": eval_row.expected_answer,
            "genie_status": "ERROR",
            "genie_sql": None,
            "genie_text_response": None,
            "genie_rows_sample": None,
            "error": error,
        }
    return {
        "question": eval_row.question,
        "expected_tables": eval_row.tables,
        "expected_answer": eval_row.expected_answer,
        "genie_status": ar.status if ar else "",
        "genie_sql": ar.sql if ar else None,
        "genie_query_description": ar.query_description if ar else None,
        "genie_text_response": ar.text_response if ar else None,
        "genie_rows_sample": _trim_rows(ar.rows if ar else None),
    }


def run(
    config: AppConfig,
    csv_path: str,
    *,
    space_id_override: str | None = None,
    archive_dir: str = "archive",
    limit: int | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> int:
    space_id = space_id_override or config.genie_space_id
    if not space_id:
        print(
            "ERROR: no Genie space ID provided (set in .config or pass --space-id)", file=sys.stderr
        )
        return 2

    eval_rows = load_csv(csv_path)
    if limit is not None:
        eval_rows = eval_rows[:limit]

    print(f"Loaded {len(eval_rows)} row(s) from {csv_path}")
    print(f"Target Genie space: {space_id} on {config.databricks_host}")

    archive = Archive(archive_dir)
    run_dir: RunDir = archive.new_run()
    print(f"Archive directory: {run_dir.path}")

    genie = GenieClient(config.databricks_host, config.databricks_token)
    judge = Judge(config.anthropic_api_key, model=config.anthropic_model)

    print("Fetching current space configuration...")
    space_response = genie.get_space(space_id, include_serialized=True)
    serialized_space_raw = space_response.get("serialized_space")
    if isinstance(serialized_space_raw, str):
        serialized_space = json.loads(serialized_space_raw)
    elif isinstance(serialized_space_raw, dict):
        serialized_space = serialized_space_raw
    else:
        print(
            "ERROR: response did not include serialized_space. "
            f"Got keys: {list(space_response.keys())}",
            file=sys.stderr,
        )
        return 3

    run_dir.write_before(serialized_space)
    print(f"  -> wrote {run_dir.path / 'before.json'}")

    judge_inputs: list[dict] = []
    per_row_records: list[dict] = []

    with tqdm(total=len(eval_rows), desc="Asking Genie", unit="row") as pbar:
        for i, row in enumerate(eval_rows, start=1):
            q_short = row.question[:40]
            pbar.set_postfix({"state": "starting", "q": q_short})
            ar: AskResult | None = None
            error: str | None = None
            try:
                ar = genie.ask(
                    space_id,
                    row.question,
                    on_poll=lambda status, _q=q_short: pbar.set_postfix({"state": status, "q": _q}),
                )
                tqdm.write(
                    f"[{i}/{len(eval_rows)}] {row.question[:80]!r}  "
                    f"status={ar.status}  sql={'yes' if ar.sql else 'no'}"
                )
            except GenieAPIError as e:
                error = str(e)
                tqdm.write(f"[{i}/{len(eval_rows)}] ERROR: {error}", file=sys.stderr)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                tqdm.write(f"[{i}/{len(eval_rows)}] ERROR: {error}", file=sys.stderr)

            judge_inputs.append(_ask_result_for_judge(row, ar, error))
            per_row_records.append(
                {
                    "csv_line": row.line_number,
                    "question": row.question,
                    "expected_tables": row.tables,
                    "expected_answer": row.expected_answer,
                    "genie": {
                        "status": ar.status if ar else None,
                        "conversation_id": ar.conversation_id if ar else None,
                        "message_id": ar.message_id if ar else None,
                        "sql": ar.sql if ar else None,
                        "text_response": ar.text_response if ar else None,
                        "rows_sample": _trim_rows(ar.rows if ar else None),
                    },
                    "error": error,
                }
            )
            pbar.update(1)

    print("Calling Claude to judge batch and propose patch...")
    batch_result = judge.judge_batch(serialized_space, judge_inputs)
    print(
        "  -> verdicts:",
        ", ".join(f"{v.verdict}" for v in batch_result.verdicts) or "(none)",
    )
    print(f"  -> token usage: {batch_result.usage}")

    verdict_counts = _tally_verdicts(batch_result.verdicts)
    print(f"  -> verdict breakdown: {_format_verdict_counts(verdict_counts)}")

    summary = patch_summary(batch_result.patch)
    print(f"  -> patch summary: {summary}")

    new_space = apply_patch(serialized_space, batch_result.patch)
    run_dir.write_after(new_space)
    print(f"  -> wrote {run_dir.path / 'after.json'}")

    # Write summary.md BEFORE prompting so the user has something to review.
    # update_skipped_reason is filled in below once the user decides; the
    # final write at the end of this function reflects the actual outcome.
    interim_meta: dict[str, Any] = {
        "space_id": space_id,
        "host": config.databricks_host,
        "csv_path": str(csv_path),
        "row_count": len(eval_rows),
        "model": config.anthropic_model,
        "rows": per_row_records,
        "verdicts": [asdict(v) for v in batch_result.verdicts],
        "verdict_counts": verdict_counts,
        "patch": batch_result.patch,
        "patch_summary": summary,
        "claude_usage": batch_result.usage,
        "dry_run": dry_run,
        "update_skipped_reason": "pending_user_confirmation",
        "update_response": None,
        "update_error": None,
    }
    run_dir.write_summary(interim_meta)
    print(f"  -> wrote {run_dir.path / 'summary.md'} (review before proceeding)")

    update_response: dict | None = None
    update_error: str | None = None
    update_skipped_reason: str | None = None

    if dry_run:
        update_skipped_reason = "dry_run"
        print("Dry run: skipping PATCH /spaces/{id}.")
    elif new_space == serialized_space:
        update_skipped_reason = "no_changes_proposed"
        print("No changes proposed. Skipping PATCH.")
    else:
        outcome = _confirm_apply(assume_yes=assume_yes)
        if outcome == _ConfirmOutcome.NO_TERMINAL:
            print(
                "\nERROR: cannot prompt for confirmation — no interactive "
                "terminal is available (/dev/tty could not be opened, or "
                "returned EOF immediately).\n"
                "  Re-run from an interactive terminal, or pass --yes to "
                "confirm non-interactively.\n"
                f"  (Diagnostics: stdin.isatty={sys.stdin.isatty()}, "
                f"stdout.isatty={sys.stdout.isatty()})",
                file=sys.stderr,
            )
            update_skipped_reason = "no_terminal"
            update_error = "no interactive terminal available for confirmation prompt"
        elif outcome == _ConfirmOutcome.DECLINED:
            update_skipped_reason = "user_declined"
            print("User declined. Skipping PATCH.")
        else:
            print("Applying patch via PATCH /spaces/{id}...")
            try:
                body = {"serialized_space": json.dumps(new_space)}
                update_response = genie.update_space(space_id, body)
                print("  -> update OK")
            except GenieAPIError as e:
                update_error = str(e)
                print(f"  ERROR applying update: {update_error}", file=sys.stderr)

    meta = {
        **interim_meta,
        "update_skipped_reason": update_skipped_reason,
        "update_response": update_response,
        "update_error": update_error,
    }
    run_dir.write_meta(meta)
    print(f"  -> wrote {run_dir.path / 'meta.json'}")

    run_dir.write_summary(meta)
    print(f"  -> wrote {run_dir.path / 'summary.md'}")

    if update_error:
        return 4
    return 0


def run_rollback(
    config: AppConfig,
    rollback_folder: str,
    *,
    space_id_override: str | None = None,
    archive_dir: str = "optimizer_runs",
    dry_run: bool = False,
) -> int:
    space_id = space_id_override or config.genie_space_id
    if not space_id:
        print(
            "ERROR: no Genie space ID provided (set in .config or pass --space-id)",
            file=sys.stderr,
        )
        return 2

    src = Path(rollback_folder)
    before_path = src / "before.json"
    if not before_path.exists():
        print(f"ERROR: {before_path} does not exist", file=sys.stderr)
        return 2

    try:
        target_space = json.loads(before_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: {before_path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    print(f"Rolling back Genie space {space_id} to snapshot at {before_path}")
    print(f"Target Genie space: {space_id} on {config.databricks_host}")

    archive = Archive(archive_dir)
    run_dir: RunDir = archive.new_run()
    print(f"Archive directory: {run_dir.path}")

    genie = GenieClient(config.databricks_host, config.databricks_token)

    print("Fetching current space configuration (pre-rollback snapshot)...")
    space_response = genie.get_space(space_id, include_serialized=True)
    raw = space_response.get("serialized_space")
    if isinstance(raw, str):
        current_space = json.loads(raw)
    elif isinstance(raw, dict):
        current_space = raw
    else:
        print(
            "ERROR: response did not include serialized_space. "
            f"Got keys: {list(space_response.keys())}",
            file=sys.stderr,
        )
        return 3

    run_dir.write_before(current_space)
    print(f"  -> wrote {run_dir.path / 'before.json'}")

    run_dir.write_after(target_space)
    print(f"  -> wrote {run_dir.path / 'after.json'}")

    update_response: dict | None = None
    update_error: str | None = None
    update_skipped_reason: str | None = None

    if dry_run:
        update_skipped_reason = "dry_run"
        print("Dry run: skipping PATCH /spaces/{id}.")
    elif current_space == target_space:
        update_skipped_reason = "no_changes_needed"
        print("Current space already matches the snapshot. Skipping PATCH.")
    else:
        print("Applying rollback via PATCH /spaces/{id}...")
        try:
            body = {"serialized_space": json.dumps(target_space)}
            update_response = genie.update_space(space_id, body)
            print("  -> rollback applied")
        except GenieAPIError as e:
            update_error = str(e)
            print(f"  ERROR applying rollback: {update_error}", file=sys.stderr)

    meta = {
        "mode": "rollback",
        "rollback_source": str(src),
        "rollback_source_before_json": str(before_path),
        "space_id": space_id,
        "host": config.databricks_host,
        "model": config.anthropic_model,
        "row_count": 0,
        "rows": [],
        "verdicts": [],
        "patch": {},
        "patch_summary": {},
        "claude_usage": {},
        "dry_run": dry_run,
        "update_skipped_reason": update_skipped_reason,
        "update_response": update_response,
        "update_error": update_error,
    }
    run_dir.write_meta(meta)
    print(f"  -> wrote {run_dir.path / 'meta.json'}")

    run_dir.write_summary(meta)
    print(f"  -> wrote {run_dir.path / 'summary.md'}")

    if update_error:
        return 4
    return 0
