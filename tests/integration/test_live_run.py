"""Live integration test against Databricks Genie + Anthropic.

Opt-in only: pytest collects but skips this unless invoked with `--runlive`.
The test runs the orchestrator with `--dry-run --limit 1` so it touches both
APIs (GET space, ASK Genie one question, judge with Claude) but never calls
the PATCH endpoint, keeping the run safe and idempotent.

Required local files (already documented in README.md):
  - ./.env       : DATABRICKS_TOKEN + ANTHROPIC_API_KEY
  - ./.config    : [databricks] host / workspace_id / genie_space_id

If either file is missing the test self-skips with an explanatory message.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genie_config_optimizer.config import ConfigError, load_config
from genie_config_optimizer.orchestrator import run

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CSV = REPO_ROOT / "example_csv" / "bakehouse_genie_test_scenarios.csv"
ENV_FILE = REPO_ROOT / ".env"
CONFIG_FILE = REPO_ROOT / ".config"


@pytest.mark.integration
def test_live_dry_run_writes_full_archive(tmp_path: Path):
    if not ENV_FILE.exists() or not CONFIG_FILE.exists():
        pytest.skip(f"requires {ENV_FILE.name} and {CONFIG_FILE.name} at repo root")

    try:
        config = load_config(env_path=ENV_FILE, config_path=CONFIG_FILE)
    except ConfigError as e:
        pytest.skip(f"config could not be loaded: {e}")

    archive_dir = tmp_path / "test_runs"
    rc = run(
        config=config,
        csv_path=str(EXAMPLE_CSV),
        archive_dir=str(archive_dir),
        limit=1,
        dry_run=True,
    )
    assert rc == 0, "dry run should exit 0"

    runs = list(archive_dir.iterdir())
    assert len(runs) == 1, f"expected one run folder, got {runs!r}"
    run_path = runs[0]

    for name in ("before.json", "after.json", "meta.json", "summary.md"):
        assert (run_path / name).exists(), f"missing {name} in {run_path}"

    meta = json.loads((run_path / "meta.json").read_text())
    assert meta["dry_run"] is True
    assert meta["update_skipped_reason"] == "dry_run"
    assert meta["update_response"] is None
    assert meta["update_error"] is None
    assert meta["row_count"] == 1
    assert len(meta["verdicts"]) == 1
    assert meta["verdicts"][0]["verdict"] in {"pass", "partial", "fail"}

    # Sanity check: token usage is populated by the Anthropic call.
    usage = meta.get("claude_usage") or {}
    assert usage, "claude_usage should be populated on a live judge call"
    assert usage.get("input_tokens", 0) > 0


@pytest.mark.integration
def test_live_dry_run_summary_contains_verdict_table(tmp_path: Path):
    if not ENV_FILE.exists() or not CONFIG_FILE.exists():
        pytest.skip(f"requires {ENV_FILE.name} and {CONFIG_FILE.name} at repo root")
    try:
        config = load_config(env_path=ENV_FILE, config_path=CONFIG_FILE)
    except ConfigError as e:
        pytest.skip(f"config could not be loaded: {e}")

    archive_dir = tmp_path / "test_runs"
    rc = run(
        config=config,
        csv_path=str(EXAMPLE_CSV),
        archive_dir=str(archive_dir),
        limit=1,
        dry_run=True,
    )
    assert rc == 0
    run_path = next(iter(archive_dir.iterdir()))
    summary = (run_path / "summary.md").read_text()
    assert "## Verdict breakdown" in summary
    assert "## Verdicts" in summary
    assert "Update outcome: skipped (dry_run)" in summary
