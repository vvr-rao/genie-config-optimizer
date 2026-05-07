"""Unit tests for archiver.py.

The archive layout (one folder per run, before/after/meta/summary inside)
is part of the user-visible contract — these tests freeze that.
"""

from __future__ import annotations

import json
from pathlib import Path

from genie_config_optimizer.archiver import Archive, RunDir


def _meta(verdict: str = "pass") -> dict:
    return {
        "space_id": "01f1abc",
        "host": "https://example.cloud.databricks.com",
        "csv_path": "example.csv",
        "row_count": 1,
        "model": "claude-sonnet-4-6",
        "rows": [{"csv_line": 2}],
        "verdicts": [
            {
                "question": "What is X?",
                "verdict": verdict,
                "reasoning": "because Y",
            }
        ],
        "verdict_counts": {
            "counts": {"pass": 1, "partial": 0, "fail": 0},
            "other": 0,
            "total": 1,
            "percentages": {"pass": 100.0, "partial": 0.0, "fail": 0.0},
        },
        "patch": {"instructions": ["new rule"]},
        "patch_summary": {"instructions": 1},
        "claude_usage": {"input_tokens": 10, "output_tokens": 5},
        "dry_run": False,
        "update_skipped_reason": None,
        "update_response": {"ok": True},
        "update_error": None,
    }


def test_new_run_creates_isodate_folder(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir = archive.new_run()
    assert run_dir.path.exists()
    assert run_dir.path.is_dir()
    # Folder name is an ISO-ish UTC timestamp (YYYY-MM-DDTHH-MM-SSZ).
    assert run_dir.path.name.endswith("Z")
    assert "T" in run_dir.path.name


def test_write_before_after_meta_round_trip(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir: RunDir = archive.new_run()
    before = {"data_sources": {"tables": []}}
    after = {"data_sources": {"tables": [{"identifier": "x.y.t"}]}}
    meta = _meta()

    run_dir.write_before(before)
    run_dir.write_after(after)
    run_dir.write_meta(meta)
    run_dir.write_summary(meta)

    assert json.loads((run_dir.path / "before.json").read_text()) == before
    assert json.loads((run_dir.path / "after.json").read_text()) == after
    assert json.loads((run_dir.path / "meta.json").read_text()) == meta
    assert (run_dir.path / "summary.md").exists()


def test_summary_md_renders_verdict_breakdown(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir = archive.new_run()
    run_dir.write_summary(_meta())
    text = (run_dir.path / "summary.md").read_text()
    assert "## Verdict breakdown" in text
    assert "| pass | 1 | 100.0% |" in text
    assert "## Verdicts" in text
    assert "What is X?" in text


def test_summary_md_renders_rollback_header_and_no_verdicts_table(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir = archive.new_run()
    rollback_meta = {
        "mode": "rollback",
        "rollback_source": "optimizer_runs/2025-01-01T00-00-00Z",
        "space_id": "01f1abc",
        "host": "https://example.cloud.databricks.com",
        "model": "claude-sonnet-4-6",
        "row_count": 0,
        "rows": [],
        "verdicts": [],
        "patch": {},
        "patch_summary": {},
        "claude_usage": {},
        "dry_run": False,
        "update_skipped_reason": None,
        "update_response": {"ok": True},
        "update_error": None,
    }
    run_dir.write_summary(rollback_meta)
    text = (run_dir.path / "summary.md").read_text()
    assert text.startswith("# Genie rollback")
    assert "Rollback source" in text
    assert "## Verdict breakdown" not in text


def test_summary_md_reports_applied_when_update_response_present(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir = archive.new_run()
    run_dir.write_summary(_meta())
    text = (run_dir.path / "summary.md").read_text()
    assert "Update outcome: applied" in text


def test_summary_md_reports_skipped_with_reason(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir = archive.new_run()
    meta = _meta()
    meta["update_response"] = None
    meta["update_skipped_reason"] = "user_declined"
    run_dir.write_summary(meta)
    text = (run_dir.path / "summary.md").read_text()
    assert "Update outcome: skipped (user_declined)" in text


def test_summary_md_reports_error_outcome(tmp_path: Path):
    archive = Archive(tmp_path / "runs")
    run_dir = archive.new_run()
    meta = _meta()
    meta["update_response"] = None
    meta["update_error"] = "400 INVALID_PARAMETER_VALUE"
    run_dir.write_summary(meta)
    text = (run_dir.path / "summary.md").read_text()
    assert "Update outcome: ERROR — 400 INVALID_PARAMETER_VALUE" in text
