from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Archive:
    def __init__(self, root: str | Path = "archive"):
        self.root = Path(root)

    def new_run(self) -> "RunDir":
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        path = self.root / ts
        path.mkdir(parents=True, exist_ok=False)
        return RunDir(path)


class RunDir:
    def __init__(self, path: Path):
        self.path = path

    def write_before(self, serialized_space: dict[str, Any]) -> Path:
        return self._write_json("before.json", serialized_space)

    def write_after(self, serialized_space: dict[str, Any]) -> Path:
        return self._write_json("after.json", serialized_space)

    def write_meta(self, meta: dict[str, Any]) -> Path:
        return self._write_json("meta.json", meta)

    def write_summary(self, meta: dict[str, Any]) -> Path:
        out = self.path / "summary.md"
        out.write_text(_render_summary_md(meta, self.path.name), encoding="utf-8")
        return out

    def _write_json(self, name: str, payload: Any) -> Path:
        out = self.path / name
        out.write_text(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return out


def _truncate(text: Any, limit: int = 200) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _render_summary_md(meta: dict[str, Any], run_name: str) -> str:
    lines: list[str] = []
    lines.append(f"# Genie optimizer run — `{run_name}`")
    lines.append("")
    lines.append("## Run metadata")
    lines.append("")
    lines.append(f"- Space ID: `{meta.get('space_id')}`")
    lines.append(f"- Host: `{meta.get('host')}`")
    lines.append(f"- CSV: `{meta.get('csv_path')}`")
    lines.append(f"- Rows processed: {meta.get('row_count')}")
    lines.append(f"- Model: `{meta.get('model')}`")
    lines.append(f"- Dry run: {meta.get('dry_run')}")
    skipped = meta.get("update_skipped_reason")
    err = meta.get("update_error")
    if err:
        outcome = f"ERROR — {err}"
    elif skipped:
        outcome = f"skipped ({skipped})"
    elif meta.get("update_response") is not None:
        outcome = "applied"
    else:
        outcome = "unknown"
    lines.append(f"- Update outcome: {outcome}")

    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    verdicts = meta.get("verdicts") or []
    rows = meta.get("rows") or []
    if verdicts:
        lines.append("| # | Line | Verdict | Question | Reasoning |")
        lines.append("|---|------|---------|----------|-----------|")
        for i, v in enumerate(verdicts):
            csv_line = rows[i].get("csv_line") if i < len(rows) else ""
            q = _truncate(v.get("question"), 100)
            r = _truncate(v.get("reasoning"), 200)
            lines.append(f"| {i + 1} | {csv_line} | {v.get('verdict')} | {q} | {r} |")
    else:
        lines.append("(none)")

    lines.append("")
    lines.append("## Patch summary")
    lines.append("")
    summary = meta.get("patch_summary") or {}
    if summary:
        for k, v in summary.items():
            lines.append(f"- **{k}**: {v}")
    else:
        lines.append("(empty patch — nothing to apply)")

    lines.append("")
    lines.append("## Proposed changes")
    lines.append("")
    patch = meta.get("patch") or {}

    if patch.get("instructions"):
        lines.append("### Instructions to append")
        for s in patch["instructions"]:
            lines.append(f"- {_truncate(s, 300)}")
        lines.append("")

    if patch.get("table_descriptions"):
        lines.append("### Table descriptions")
        for tbl, desc in patch["table_descriptions"].items():
            lines.append(f"- `{tbl}`: {_truncate(desc, 250)}")
        lines.append("")

    if patch.get("column_descriptions"):
        lines.append("### Column descriptions")
        for tbl, cols in patch["column_descriptions"].items():
            lines.append(f"- `{tbl}`")
            for col, desc in cols.items():
                lines.append(f"  - `{col}`: {_truncate(desc, 200)}")
        lines.append("")

    if patch.get("joins"):
        lines.append("### Joins")
        for j in patch["joins"]:
            lines.append(
                f"- `{j.get('from_table')}.{j.get('from_column')}` → "
                f"`{j.get('to_table')}.{j.get('to_column')}` ({j.get('type')})"
            )
        lines.append("")

    if patch.get("suggested_queries"):
        lines.append("### Suggested queries")
        for q in patch["suggested_queries"]:
            lines.append(f"- {_truncate(q.get('question'), 250)}")
        lines.append("")

    if patch.get("trusted_queries"):
        lines.append("### Trusted queries")
        for q in patch["trusted_queries"]:
            lines.append(f"- **{_truncate(q.get('question'), 200)}**")
            sql = _truncate(q.get("sql"), 400)
            if sql:
                lines.append(f"  - SQL: `{sql}`")
        lines.append("")

    usage = meta.get("claude_usage") or {}
    if usage:
        lines.append("## Claude token usage")
        lines.append("")
        for k, v in usage.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
