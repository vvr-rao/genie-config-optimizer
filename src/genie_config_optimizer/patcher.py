"""Pure functions that merge a Claude-proposed patch into a serialized_space dict.

Targets the Databricks Genie space schema version 2, which uses these paths:

    space.instructions.text_instructions[]       {id, content: list[str]}
    space.instructions.example_question_sqls[]   {id, question, sql, ...}
    space.config.sample_questions[]              {id, question: list[str]}
    space.data_sources.tables[].description      list[str] (optional on each table)
    space.data_sources.tables[].column_configs[] {column_name, description: list[str], ...}

The schema has no first-class joins/relationships category. Claude is instructed
to express join knowledge as text instructions or trusted queries instead, so
this module only honors five patch categories.

`apply_patch` does NOT mutate its inputs — it returns a new dict.
"""

from __future__ import annotations

import copy
import secrets
from typing import Any


SUPPORTED_KEYS = frozenset(
    {
        "instructions",
        "table_descriptions",
        "column_descriptions",
        "suggested_queries",
        "trusted_queries",
    }
)


def apply_patch(serialized_space: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    space = copy.deepcopy(serialized_space)

    if instructions := patch.get("instructions"):
        _append_instructions(space, instructions)

    if table_descs := patch.get("table_descriptions"):
        _set_table_descriptions(space, table_descs)

    if col_descs := patch.get("column_descriptions"):
        _set_column_descriptions(space, col_descs)

    if suggested := patch.get("suggested_queries"):
        _append_suggested_queries(space, suggested)

    if trusted := patch.get("trusted_queries"):
        _append_trusted_queries(space, trusted)

    _finalize_sort(space)
    return space


def _finalize_sort(space: dict) -> None:
    """The Databricks export-proto validator requires:
      - instructions.text_instructions[]      sorted by id
      - instructions.example_question_sqls[]  sorted by id
      - config.sample_questions[]             sorted by id
      - each table's column_configs[]         sorted by column_name
    Apply all four invariants here so callers don't have to.
    """
    inst = space.get("instructions")
    if isinstance(inst, dict):
        for key in ("text_instructions", "example_question_sqls"):
            arr = inst.get(key)
            if isinstance(arr, list):
                arr.sort(key=lambda x: x.get("id", "") if isinstance(x, dict) else "")
    cfg = space.get("config")
    if isinstance(cfg, dict):
        arr = cfg.get("sample_questions")
        if isinstance(arr, list):
            arr.sort(key=lambda x: x.get("id", "") if isinstance(x, dict) else "")
    ds = space.get("data_sources")
    if isinstance(ds, dict):
        tables = ds.get("tables")
        if isinstance(tables, list):
            for t in tables:
                if not isinstance(t, dict):
                    continue
                cc = t.get("column_configs")
                if isinstance(cc, list):
                    cc.sort(
                        key=lambda c: c.get("column_name", "") if isinstance(c, dict) else ""
                    )


def _ensure(d: dict, key: str, default):
    if key not in d or d[key] is None:
        d[key] = default
    return d[key]


def _new_id() -> str:
    """32-char hex id, matching the format Databricks uses for existing entries."""
    return secrets.token_hex(16)


def _as_string_list(s: Any) -> list[str]:
    if isinstance(s, str):
        return [s]
    if isinstance(s, list):
        return [x for x in s if isinstance(x, str)]
    return []


def _append_instructions(space: dict, instructions: list[str]) -> None:
    # text_instructions is constrained to at most ONE entry. All rules live as
    # individual bullet strings inside that single entry's `content` list, so
    # we append new bullets to the existing entry rather than creating new ones.
    inst = _ensure(space, "instructions", {})
    arr = _ensure(inst, "text_instructions", [])
    if arr and isinstance(arr[0], dict):
        target = arr[0]
    else:
        target = {"id": _new_id(), "content": []}
        arr.clear()
        arr.append(target)
    content = _ensure(target, "content", [])
    seen = {s.strip() for s in content if isinstance(s, str)}
    for instr in instructions:
        if not isinstance(instr, str):
            continue
        text = instr.strip()
        if not text or text in seen:
            continue
        content.append(instr if instr.endswith("\n") else instr + "\n")
        seen.add(text)


def _set_table_descriptions(space: dict, table_descs: dict[str, str]) -> None:
    ds = _ensure(space, "data_sources", {})
    tables = _ensure(ds, "tables", [])
    by_id = {t.get("identifier"): t for t in tables if isinstance(t, dict)}
    for identifier, description in table_descs.items():
        if not isinstance(description, str):
            continue
        target = by_id.get(identifier)
        if target is None:
            target = {"identifier": identifier}
            tables.append(target)
            by_id[identifier] = target
        # Schema requires description to be a list[str], not a single string.
        # Violation: "Expected an array for description but found <str>".
        target["description"] = [description]


def _set_column_descriptions(
    space: dict, col_descs: dict[str, dict[str, str]]
) -> None:
    ds = _ensure(space, "data_sources", {})
    tables = _ensure(ds, "tables", [])
    by_id = {t.get("identifier"): t for t in tables if isinstance(t, dict)}
    for identifier, cols in col_descs.items():
        if not isinstance(cols, dict):
            continue
        target = by_id.get(identifier)
        if target is None:
            target = {"identifier": identifier}
            tables.append(target)
            by_id[identifier] = target
        configs = _ensure(target, "column_configs", [])
        by_col = {
            c.get("column_name"): c for c in configs if isinstance(c, dict)
        }
        for col_name, desc in cols.items():
            if not isinstance(col_name, str) or not isinstance(desc, str):
                continue
            cfg = by_col.get(col_name)
            if cfg is None:
                cfg = {"column_name": col_name}
                configs.append(cfg)
                by_col[col_name] = cfg
            cfg["description"] = [desc]


def _append_suggested_queries(space: dict, suggested: list[dict]) -> None:
    cfg = _ensure(space, "config", {})
    arr = _ensure(cfg, "sample_questions", [])
    seen: set[str] = set()
    for q in arr:
        if isinstance(q, dict):
            for s in _as_string_list(q.get("question")):
                seen.add(s.strip())
    for item in suggested:
        if not isinstance(item, dict):
            continue
        text = item.get("question")
        if not isinstance(text, str):
            continue
        norm = text.strip()
        if not norm or norm in seen:
            continue
        arr.append({"id": _new_id(), "question": [text]})
        seen.add(norm)


def _append_trusted_queries(space: dict, trusted: list[dict]) -> None:
    inst = _ensure(space, "instructions", {})
    arr = _ensure(inst, "example_question_sqls", [])
    seen: set[tuple[str, str]] = set()
    for e in arr:
        if isinstance(e, dict):
            q = "".join(_as_string_list(e.get("question"))).strip()
            s = "".join(_as_string_list(e.get("sql"))).strip()
            seen.add((q, s))
    for t in trusted:
        if not isinstance(t, dict):
            continue
        question = t.get("question")
        sql = t.get("sql")
        if not (isinstance(question, str) and isinstance(sql, str)):
            continue
        if not question.strip() or not sql.strip():
            continue
        key = (question.strip(), sql.strip())
        if key in seen:
            continue
        # ExampleQuestionSql proto only accepts id, question, sql.
        # description / usage_guidance are not valid fields there.
        entry: dict[str, Any] = {
            "id": _new_id(),
            "question": [question],
            "sql": [sql],
        }
        arr.append(entry)
        seen.add(key)


def patch_summary(patch: dict[str, Any]) -> dict[str, Any]:
    """Compact human-readable summary of a patch (counts per category)."""
    summary: dict[str, Any] = {}
    for k in SUPPORTED_KEYS:
        v = patch.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            summary[k] = len(v)
        elif isinstance(v, dict):
            summary[k] = (
                sum(len(inner) if isinstance(inner, dict) else 1 for inner in v.values())
                if k == "column_descriptions"
                else len(v)
            )
    dropped = sorted(set(patch.keys()) - SUPPORTED_KEYS)
    if dropped:
        summary["_dropped_keys"] = dropped
    return summary
