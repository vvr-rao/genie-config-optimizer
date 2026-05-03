"""Pure functions that merge a Claude-proposed patch into a serialized_space dict.

Only the five user-agreed metadata categories are honored. Any other keys in the
proposed patch are ignored (they show up in the archive's meta.json so you can
see what was dropped).

`apply_patch` does NOT mutate its inputs — it returns a new dict.
"""

from __future__ import annotations

import copy
from typing import Any


SUPPORTED_KEYS = frozenset(
    {
        "instructions",
        "table_descriptions",
        "column_descriptions",
        "joins",
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

    if joins := patch.get("joins"):
        _append_joins(space, joins)

    if suggested := patch.get("suggested_queries"):
        _append_suggested_queries(space, suggested)

    if trusted := patch.get("trusted_queries"):
        _append_trusted_queries(space, trusted)

    return space


def _ensure(d: dict, key: str, default):
    if key not in d or d[key] is None:
        d[key] = default
    return d[key]


def _append_instructions(space: dict, instructions: list[str]) -> None:
    si = _ensure(space, "structured_instructions", {})
    existing = _ensure(si, "text_instructions", [])
    seen = {x for x in existing if isinstance(x, str)}
    for instr in instructions:
        if isinstance(instr, str) and instr.strip() and instr not in seen:
            existing.append(instr)
            seen.add(instr)


def _set_table_descriptions(space: dict, table_descs: dict[str, str]) -> None:
    ds = _ensure(space, "data_sources", {})
    tables = _ensure(ds, "tables", [])
    existing_by_id = {t.get("identifier"): t for t in tables if isinstance(t, dict)}
    for identifier, description in table_descs.items():
        if not isinstance(description, str):
            continue
        target = existing_by_id.get(identifier)
        if target is None:
            target = {"identifier": identifier}
            tables.append(target)
            existing_by_id[identifier] = target
        target["description"] = description


def _set_column_descriptions(
    space: dict, col_descs: dict[str, dict[str, str]]
) -> None:
    ds = _ensure(space, "data_sources", {})
    tables = _ensure(ds, "tables", [])
    existing_by_id = {t.get("identifier"): t for t in tables if isinstance(t, dict)}
    for identifier, cols in col_descs.items():
        if not isinstance(cols, dict):
            continue
        target = existing_by_id.get(identifier)
        if target is None:
            target = {"identifier": identifier}
            tables.append(target)
            existing_by_id[identifier] = target
        configs = _ensure(target, "column_configs", [])
        configs_by_name = {
            c.get("name"): c for c in configs if isinstance(c, dict)
        }
        for col_name, desc in cols.items():
            if not isinstance(desc, str):
                continue
            cfg = configs_by_name.get(col_name)
            if cfg is None:
                cfg = {"name": col_name}
                configs.append(cfg)
                configs_by_name[col_name] = cfg
            cfg["description"] = desc


def _append_joins(space: dict, joins: list[dict]) -> None:
    ds = _ensure(space, "data_sources", {})
    relationships = _ensure(ds, "relationships", [])
    seen_keys = {_join_key(r) for r in relationships if isinstance(r, dict)}
    for j in joins:
        if not isinstance(j, dict):
            continue
        required = ("from_table", "from_column", "to_table", "to_column", "type")
        if not all(j.get(k) for k in required):
            continue
        key = _join_key(j)
        if key in seen_keys:
            continue
        relationships.append(j)
        seen_keys.add(key)


def _append_suggested_queries(space: dict, suggested: list[dict]) -> None:
    sample = _ensure(space, "sample_questions", [])
    seen_questions = {
        q.get("question") for q in sample if isinstance(q, dict)
    }
    for s in suggested:
        if not isinstance(s, dict):
            continue
        question = s.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        if question in seen_questions:
            continue
        sample.append({"question": question})
        seen_questions.add(question)


def _append_trusted_queries(space: dict, trusted: list[dict]) -> None:
    si = _ensure(space, "structured_instructions", {})
    examples = _ensure(si, "example_question_sqls", [])
    seen = {
        (e.get("question"), e.get("sql"))
        for e in examples
        if isinstance(e, dict)
    }
    for t in trusted:
        if not isinstance(t, dict):
            continue
        question = t.get("question")
        sql = t.get("sql")
        if not (isinstance(question, str) and isinstance(sql, str)):
            continue
        if not question.strip() or not sql.strip():
            continue
        if (question, sql) in seen:
            continue
        entry = {"question": question, "sql": sql}
        for opt in ("description", "usage_guidance"):
            if isinstance(t.get(opt), str) and t[opt].strip():
                entry[opt] = t[opt]
        examples.append(entry)
        seen.add((question, sql))


def _join_key(d: dict) -> tuple:
    return (
        d.get("from_table"),
        d.get("from_column"),
        d.get("to_table"),
        d.get("to_column"),
        d.get("type"),
    )


def patch_summary(patch: dict[str, Any]) -> dict[str, Any]:
    """Compact human-readable summary of a patch (counts per category)."""
    summary = {}
    for k in SUPPORTED_KEYS:
        v = patch.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            summary[k] = len(v)
        elif isinstance(v, dict):
            summary[k] = sum(
                len(inner) if isinstance(inner, dict) else 1 for inner in v.values()
            ) if k == "column_descriptions" else len(v)
    dropped = sorted(set(patch.keys()) - SUPPORTED_KEYS)
    if dropped:
        summary["_dropped_keys"] = dropped
    return summary
