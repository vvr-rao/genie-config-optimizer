from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from .anthropic_client import Judge
from .archiver import Archive, RunDir
from .config import AppConfig
from .csv_loader import EvalRow, load_csv
from .databricks_client import AskResult, GenieAPIError, GenieClient
from .patcher import apply_patch, patch_summary


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
) -> int:
    space_id = space_id_override or config.genie_space_id
    if not space_id:
        print("ERROR: no Genie space ID provided (set in .config or pass --space-id)", file=sys.stderr)
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

    for i, row in enumerate(eval_rows, start=1):
        print(f"[{i}/{len(eval_rows)}] {row.question[:80]!r} ...", flush=True)
        ar: AskResult | None = None
        error: str | None = None
        try:
            ar = genie.ask(space_id, row.question)
            print(f"   status={ar.status}  sql={'yes' if ar.sql else 'no'}")
        except GenieAPIError as e:
            error = str(e)
            print(f"   ERROR: {error}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — orchestrator logs and continues
            error = f"{type(e).__name__}: {e}"
            print(f"   ERROR: {error}", file=sys.stderr)

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

    print("Calling Claude to judge batch and propose patch...")
    batch_result = judge.judge_batch(serialized_space, judge_inputs)
    print(
        "  -> verdicts:",
        ", ".join(f"{v.verdict}" for v in batch_result.verdicts) or "(none)",
    )
    print(f"  -> token usage: {batch_result.usage}")

    summary = patch_summary(batch_result.patch)
    print(f"  -> patch summary: {summary}")

    new_space = apply_patch(serialized_space, batch_result.patch)
    run_dir.write_after(new_space)
    print(f"  -> wrote {run_dir.path / 'after.json'}")

    update_response: dict | None = None
    update_error: str | None = None
    update_skipped_reason: str | None = None

    if dry_run:
        update_skipped_reason = "dry_run"
        print("Dry run: skipping PUT /spaces/{id}.")
    elif new_space == serialized_space:
        update_skipped_reason = "no_changes_proposed"
        print("No changes proposed. Skipping PUT.")
    else:
        print("Applying patch via PUT /spaces/{id}...")
        try:
            body = {**space_response, "serialized_space": json.dumps(new_space)}
            # Strip read-only fields that some workspaces reject on PUT.
            for ro in ("created_at", "updated_at", "creator_user_id"):
                body.pop(ro, None)
            update_response = genie.update_space(space_id, body)
            print("  -> update OK")
        except GenieAPIError as e:
            update_error = str(e)
            print(f"  ERROR applying update: {update_error}", file=sys.stderr)

    meta = {
        "space_id": space_id,
        "host": config.databricks_host,
        "csv_path": str(csv_path),
        "row_count": len(eval_rows),
        "model": config.anthropic_model,
        "rows": per_row_records,
        "verdicts": [asdict(v) for v in batch_result.verdicts],
        "patch": batch_result.patch,
        "patch_summary": summary,
        "claude_usage": batch_result.usage,
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
