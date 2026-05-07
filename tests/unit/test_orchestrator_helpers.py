"""Unit tests for the pure helpers in orchestrator.py.

We deliberately don't exercise `run()` or `run_rollback()` here — those are
covered by the live integration test. This file pins the small functions
that are easy to break and hard to debug if they're wrong.
"""

from __future__ import annotations

import builtins
import io
from dataclasses import dataclass
from pathlib import Path

from genie_config_optimizer.csv_loader import EvalRow
from genie_config_optimizer.databricks_client import AskResult
from genie_config_optimizer.orchestrator import (
    _ask_result_for_judge,
    _confirm_apply,
    _format_verdict_counts,
    _tally_verdicts,
    _trim_rows,
)


@dataclass
class _V:
    """Tiny stand-in for the Verdict dataclass — only `verdict` matters here."""

    verdict: str


# --- _tally_verdicts -------------------------------------------------------


def test_tally_verdicts_counts_known_categories():
    vs = [_V("pass"), _V("pass"), _V("partial"), _V("fail")]
    out = _tally_verdicts(vs)
    assert out["counts"] == {"pass": 2, "partial": 1, "fail": 1}
    assert out["other"] == 0
    assert out["total"] == 4
    assert out["percentages"]["pass"] == 50.0
    assert out["percentages"]["partial"] == 25.0


def test_tally_verdicts_handles_unknown_verdict_strings():
    vs = [_V("pass"), _V("weird"), _V("")]
    out = _tally_verdicts(vs)
    assert out["counts"] == {"pass": 1, "partial": 0, "fail": 0}
    assert out["other"] == 2
    assert out["total"] == 3


def test_tally_verdicts_empty_list():
    out = _tally_verdicts([])
    assert out["total"] == 0
    assert out["percentages"] == {"pass": 0.0, "partial": 0.0, "fail": 0.0}


# --- _format_verdict_counts ------------------------------------------------


def test_format_verdict_counts_includes_all_three_categories():
    vc = _tally_verdicts([_V("pass"), _V("partial"), _V("fail"), _V("fail")])
    formatted = _format_verdict_counts(vc)
    assert formatted.startswith("4 rows: ")
    assert "1 pass" in formatted
    assert "1 partial" in formatted
    assert "2 fail" in formatted


def test_format_verdict_counts_appends_other_when_present():
    vc = _tally_verdicts([_V("pass"), _V("weird")])
    formatted = _format_verdict_counts(vc)
    assert "1 other" in formatted


# --- _trim_rows ------------------------------------------------------------


def test_trim_rows_passes_through_when_under_limit():
    rows = [[1], [2], [3]]
    assert _trim_rows(rows, limit=10) == rows


def test_trim_rows_truncates_when_over_limit():
    rows = [[i] for i in range(100)]
    out = _trim_rows(rows, limit=5)
    assert len(out) == 5
    assert out == [[0], [1], [2], [3], [4]]


def test_trim_rows_handles_none():
    assert _trim_rows(None) is None


# --- _ask_result_for_judge -------------------------------------------------


def test_ask_result_for_judge_with_error():
    row = EvalRow(
        question="q?",
        tables=["x.y.t"],
        expected_answer="e",
        line_number=2,
    )
    out = _ask_result_for_judge(row, ar=None, error="boom")
    assert out["question"] == "q?"
    assert out["expected_tables"] == ["x.y.t"]
    assert out["genie_status"] == "ERROR"
    assert out["genie_sql"] is None
    assert out["error"] == "boom"


def test_ask_result_for_judge_with_success():
    row = EvalRow(question="q?", tables=["t"], expected_answer="e", line_number=2)
    ar = AskResult(
        conversation_id="c1",
        message_id="m1",
        status="COMPLETED",
        message={},
        sql="SELECT 1",
        query_description="ones",
        text_response="Here you go",
        rows=[[1]],
    )
    out = _ask_result_for_judge(row, ar=ar, error=None)
    assert out["genie_status"] == "COMPLETED"
    assert out["genie_sql"] == "SELECT 1"
    assert out["genie_text_response"] == "Here you go"
    assert out["genie_rows_sample"] == [[1]]


# --- _confirm_apply --------------------------------------------------------


def _patch_open_for_tty(monkeypatch, tty_response: str | None, raise_oserror: bool = False):
    """Intercept open('/dev/tty') so we can test _confirm_apply without a TTY.

    `tty_response` is the line the fake tty produces (None means EOF).
    `raise_oserror` simulates a non-interactive environment with no controlling
    terminal (e.g. cron).
    """
    real_open = builtins.open

    def fake_open(path, mode="r", *args, **kwargs):
        if path == "/dev/tty":
            if raise_oserror:
                raise OSError("no controlling terminal")
            return io.StringIO("" if tty_response is None else tty_response)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_confirm_apply_returns_true_on_uppercase_Y(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, "Y\n")
    assert _confirm_apply() is True


def test_confirm_apply_rejects_lowercase_y(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, "y\n")
    assert _confirm_apply() is False


def test_confirm_apply_rejects_n(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, "n\n")
    assert _confirm_apply() is False


def test_confirm_apply_rejects_blank(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, "\n")
    assert _confirm_apply() is False


def test_confirm_apply_handles_eof(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, None)
    assert _confirm_apply() is False


def test_confirm_apply_fails_safe_when_no_tty(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, "", raise_oserror=True)
    assert _confirm_apply() is False


def test_confirm_apply_prints_warning_message(monkeypatch, capsys):
    _patch_open_for_tty(monkeypatch, "Y\n")
    _confirm_apply()
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "Y/n" in captured.out


# Belt-and-suspenders: confirm we're really pulling from /dev/tty in tests.
def test_repo_layout_smoke():
    # Anchors REPO_ROOT/Path math — no functional value beyond that.
    assert (Path(__file__).resolve().parents[2] / "pyproject.toml").exists()
