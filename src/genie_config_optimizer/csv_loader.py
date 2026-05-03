from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


REQUIRED_COLUMNS = ("question", "table", "expected_answer")


@dataclass
class EvalRow:
    question: str
    table: str
    expected_answer: str
    line_number: int  # 1-based, including header


class CSVLoadError(RuntimeError):
    pass


def load_csv(path: str | Path) -> list[EvalRow]:
    path = Path(path)
    if not path.exists():
        raise CSVLoadError(f"CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise CSVLoadError(f"CSV {path} is empty")
        normalized = [c.strip().lower() for c in reader.fieldnames]
        missing = [c for c in REQUIRED_COLUMNS if c not in normalized]
        if missing:
            raise CSVLoadError(
                f"CSV {path} is missing required columns: {', '.join(missing)}. "
                f"Got: {', '.join(reader.fieldnames)}"
            )

        # Map normalized -> original column name so we can read by case-insensitive key.
        col_map = {n: orig for n, orig in zip(normalized, reader.fieldnames)}

        rows: list[EvalRow] = []
        for i, raw in enumerate(reader, start=2):
            question = (raw.get(col_map["question"]) or "").strip()
            table = (raw.get(col_map["table"]) or "").strip()
            expected = (raw.get(col_map["expected_answer"]) or "").strip()
            if not question:
                continue  # skip blank rows
            if not table or not expected:
                raise CSVLoadError(
                    f"CSV {path} line {i}: 'table' and 'expected_answer' are required."
                )
            rows.append(
                EvalRow(
                    question=question,
                    table=table,
                    expected_answer=expected,
                    line_number=i,
                )
            )

    if not rows:
        raise CSVLoadError(f"CSV {path} contains no data rows")
    return rows
