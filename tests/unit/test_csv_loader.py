"""Unit tests for csv_loader.py.

Asserts the parser handles the canonical example CSV unchanged and rejects
malformed inputs with a clear error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genie_config_optimizer.csv_loader import CSVLoadError, EvalRow, load_csv

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CSV = REPO_ROOT / "example_csv" / "bakehouse_genie_test_scenarios.csv"


def test_loads_example_csv():
    rows = load_csv(EXAMPLE_CSV)
    assert len(rows) > 0
    assert all(isinstance(r, EvalRow) for r in rows)


def test_example_csv_columns_are_populated():
    rows = load_csv(EXAMPLE_CSV)
    for r in rows:
        assert r.question
        assert r.expected_answer
        assert r.tables
        assert all(isinstance(t, str) and t for t in r.tables)


def test_pipe_delimited_tables_parsed_into_list():
    rows = load_csv(EXAMPLE_CSV)
    multi = [r for r in rows if len(r.tables) > 1]
    assert multi, "example CSV should include at least one multi-table row"
    for r in multi:
        # Pipe was the chosen delimiter; no fragment should still contain one.
        assert all("|" not in t for t in r.tables)


def test_line_numbers_are_one_based_including_header(tmp_path: Path):
    p = tmp_path / "tiny.csv"
    p.write_text(
        "question,tables,expected_answer\nq1,a.b.c,e1\nq2,a.b.d,e2\n",
        encoding="utf-8",
    )
    rows = load_csv(p)
    assert [r.line_number for r in rows] == [2, 3]


def test_blank_rows_are_skipped(tmp_path: Path):
    p = tmp_path / "blanks.csv"
    p.write_text(
        "question,tables,expected_answer\n,,\nq1,a.b.c,e1\n,,\n",
        encoding="utf-8",
    )
    rows = load_csv(p)
    assert len(rows) == 1
    assert rows[0].question == "q1"


def test_missing_required_column_raises(tmp_path: Path):
    p = tmp_path / "bad.csv"
    p.write_text(
        "question,expected_answer\nq1,e1\n",
        encoding="utf-8",
    )
    with pytest.raises(CSVLoadError) as exc:
        load_csv(p)
    assert "tables" in str(exc.value)


def test_row_missing_tables_raises(tmp_path: Path):
    p = tmp_path / "missing_tables.csv"
    p.write_text(
        "question,tables,expected_answer\nq1,,e1\n",
        encoding="utf-8",
    )
    with pytest.raises(CSVLoadError) as exc:
        load_csv(p)
    assert "tables" in str(exc.value)


def test_nonexistent_path_raises(tmp_path: Path):
    with pytest.raises(CSVLoadError):
        load_csv(tmp_path / "does_not_exist.csv")


def test_duplicate_tables_in_same_row_dedup(tmp_path: Path):
    p = tmp_path / "dup.csv"
    p.write_text(
        "question,tables,expected_answer\nq1,a.b.c|a.b.c|a.b.d,e1\n",
        encoding="utf-8",
    )
    rows = load_csv(p)
    assert rows[0].tables == ["a.b.c", "a.b.d"]


def test_case_insensitive_column_headers(tmp_path: Path):
    p = tmp_path / "upper.csv"
    p.write_text(
        "Question,TABLES,Expected_Answer\nq1,a.b.c,e1\n",
        encoding="utf-8",
    )
    rows = load_csv(p)
    assert rows[0].question == "q1"
    assert rows[0].tables == ["a.b.c"]
