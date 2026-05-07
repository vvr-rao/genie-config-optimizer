"""Unit tests for patcher.py.

These tests pin every v2 schema invariant we have learned the hard way
(each one corresponds to a real 400 INVALID_PARAMETER_VALUE response from
the Databricks PATCH endpoint). Breaking any of them will reintroduce a
known bug, so failures here are load-bearing.
"""

from __future__ import annotations

import copy

from genie_config_optimizer.patcher import (
    SUPPORTED_KEYS,
    apply_patch,
    patch_summary,
)


def _empty_space() -> dict:
    return {
        "instructions": {"text_instructions": [], "example_question_sqls": []},
        "config": {"sample_questions": []},
        "data_sources": {"tables": []},
    }


# --- supported keys ---------------------------------------------------------


def test_supported_keys_does_not_include_joins():
    # The v2 schema has no relationships category. Claude is told to
    # express joins as instructions or trusted_queries.
    assert "joins" not in SUPPORTED_KEYS
    assert (
        frozenset(
            {
                "instructions",
                "table_descriptions",
                "column_descriptions",
                "suggested_queries",
                "trusted_queries",
            }
        )
        == SUPPORTED_KEYS
    )


# --- apply_patch is non-mutating -------------------------------------------


def test_apply_patch_does_not_mutate_input():
    space = _empty_space()
    snapshot = copy.deepcopy(space)
    apply_patch(space, {"instructions": ["hello"]})
    assert space == snapshot


# --- instructions: at-most-one entry, append to content list ---------------


def test_instructions_creates_single_entry_when_missing():
    space = _empty_space()
    out = apply_patch(space, {"instructions": ["rule one"]})
    text = out["instructions"]["text_instructions"]
    assert len(text) == 1
    entry = text[0]
    assert isinstance(entry["id"], str) and len(entry["id"]) == 32
    assert entry["content"] == ["rule one\n"]


def test_instructions_appends_to_existing_single_entry():
    space = _empty_space()
    space["instructions"]["text_instructions"] = [{"id": "01f1aaa", "content": ["existing rule\n"]}]
    out = apply_patch(space, {"instructions": ["new rule"]})
    text = out["instructions"]["text_instructions"]
    # Still exactly one entry — we never create a second.
    assert len(text) == 1
    assert text[0]["id"] == "01f1aaa"
    assert text[0]["content"] == ["existing rule\n", "new rule\n"]


def test_instructions_dedupes_against_existing_content():
    space = _empty_space()
    space["instructions"]["text_instructions"] = [{"id": "01f1aaa", "content": ["dup rule\n"]}]
    out = apply_patch(space, {"instructions": ["dup rule", "fresh rule"]})
    content = out["instructions"]["text_instructions"][0]["content"]
    assert content == ["dup rule\n", "fresh rule\n"]


def test_instructions_preserves_trailing_newline_convention():
    space = _empty_space()
    out = apply_patch(space, {"instructions": ["already newlined\n", "no newline"]})
    content = out["instructions"]["text_instructions"][0]["content"]
    assert content == ["already newlined\n", "no newline\n"]


# --- table descriptions must be list[str] (not bare str) -------------------


def test_table_description_is_wrapped_as_list():
    space = _empty_space()
    space["data_sources"]["tables"] = [{"identifier": "x.y.t1"}]
    out = apply_patch(space, {"table_descriptions": {"x.y.t1": "hello"}})
    desc = out["data_sources"]["tables"][0]["description"]
    assert desc == ["hello"]


def test_table_description_overwrites_existing_list():
    space = _empty_space()
    space["data_sources"]["tables"] = [{"identifier": "x.y.t1", "description": ["old"]}]
    out = apply_patch(space, {"table_descriptions": {"x.y.t1": "new"}})
    assert out["data_sources"]["tables"][0]["description"] == ["new"]


def test_table_description_creates_table_entry_if_missing():
    space = _empty_space()
    out = apply_patch(space, {"table_descriptions": {"x.y.fresh": "fresh"}})
    tables = out["data_sources"]["tables"]
    assert len(tables) == 1
    assert tables[0]["identifier"] == "x.y.fresh"
    assert tables[0]["description"] == ["fresh"]


# --- column descriptions: column_name (not name), description as list ------


def test_column_description_uses_column_name_key_and_list_value():
    space = _empty_space()
    space["data_sources"]["tables"] = [{"identifier": "x.y.t1"}]
    out = apply_patch(space, {"column_descriptions": {"x.y.t1": {"col_a": "describes a"}}})
    cfgs = out["data_sources"]["tables"][0]["column_configs"]
    assert len(cfgs) == 1
    assert cfgs[0]["column_name"] == "col_a"
    assert "name" not in cfgs[0]  # canonical field is column_name
    assert cfgs[0]["description"] == ["describes a"]


def test_column_description_updates_existing_config_in_place():
    space = _empty_space()
    space["data_sources"]["tables"] = [
        {
            "identifier": "x.y.t1",
            "column_configs": [
                {"column_name": "col_a", "description": ["old"]},
            ],
        }
    ]
    out = apply_patch(space, {"column_descriptions": {"x.y.t1": {"col_a": "new"}}})
    cfgs = out["data_sources"]["tables"][0]["column_configs"]
    assert len(cfgs) == 1
    assert cfgs[0]["description"] == ["new"]


# --- suggested_queries: append to config.sample_questions, dedup -----------


def test_suggested_queries_appends_to_config_sample_questions():
    space = _empty_space()
    out = apply_patch(space, {"suggested_queries": [{"question": "What is X?"}]})
    sample_qs = out["config"]["sample_questions"]
    assert len(sample_qs) == 1
    assert sample_qs[0]["question"] == ["What is X?"]
    assert isinstance(sample_qs[0]["id"], str) and len(sample_qs[0]["id"]) == 32


def test_suggested_queries_deduplicates_against_existing():
    space = _empty_space()
    space["config"]["sample_questions"] = [{"id": "01f1aaa", "question": ["What is X?"]}]
    out = apply_patch(
        space,
        {
            "suggested_queries": [
                {"question": "What is X?"},
                {"question": "What is Y?"},
            ]
        },
    )
    # Order depends on the (random) id assigned to the new entry — sample_questions
    # is sorted by id at the end of apply_patch. Test set-membership instead.
    questions = {q["question"][0] for q in out["config"]["sample_questions"]}
    assert questions == {"What is X?", "What is Y?"}


# --- trusted_queries: only {id, question, sql}; drop extra fields ----------


def test_trusted_queries_only_emits_id_question_sql():
    space = _empty_space()
    out = apply_patch(
        space,
        {
            "trusted_queries": [
                {
                    "question": "Top customers?",
                    "sql": "SELECT * FROM t",
                    # Anything else MUST be silently dropped — the proto
                    # rejects extra fields.
                    "description": "drop me",
                    "usage_guidance": "drop me too",
                }
            ]
        },
    )
    eqs = out["instructions"]["example_question_sqls"]
    assert len(eqs) == 1
    entry = eqs[0]
    assert set(entry.keys()) == {"id", "question", "sql"}
    assert entry["question"] == ["Top customers?"]
    assert entry["sql"] == ["SELECT * FROM t"]


def test_trusted_queries_dedup_handles_existing_list_question_and_sql():
    # Existing entries have list-valued question/sql; the dedup key must
    # cope with both string- and list-shaped sources.
    space = _empty_space()
    space["instructions"]["example_question_sqls"] = [
        {
            "id": "01f1aaa",
            "question": ["Top customers?"],
            "sql": ["SELECT * FROM t"],
        }
    ]
    out = apply_patch(
        space,
        {
            "trusted_queries": [
                {"question": "Top customers?", "sql": "SELECT * FROM t"},
                {"question": "Different?", "sql": "SELECT 1"},
            ]
        },
    )
    eqs = out["instructions"]["example_question_sqls"]
    assert len(eqs) == 2
    questions = sorted(e["question"][0] for e in eqs)
    assert questions == ["Different?", "Top customers?"]


# --- sort invariants enforced by _finalize_sort ----------------------------


def test_text_instructions_sorted_by_id():
    space = _empty_space()
    space["instructions"]["text_instructions"] = [{"id": "zzzz", "content": ["z"]}]
    # Append-instructions only ever produces one entry, so the only way to
    # get multiple is via pre-existing data. _finalize_sort still runs.
    out = apply_patch(space, {})
    ids = [e["id"] for e in out["instructions"]["text_instructions"]]
    assert ids == sorted(ids)


def test_example_question_sqls_sorted_by_id():
    space = _empty_space()
    space["instructions"]["example_question_sqls"] = [
        {"id": "01f1zzz", "question": ["q1"], "sql": ["s1"]},
        {"id": "01f1aaa", "question": ["q0"], "sql": ["s0"]},
    ]
    out = apply_patch(space, {})
    ids = [e["id"] for e in out["instructions"]["example_question_sqls"]]
    assert ids == ["01f1aaa", "01f1zzz"]


def test_sample_questions_sorted_by_id():
    space = _empty_space()
    space["config"]["sample_questions"] = [
        {"id": "01f1zzz", "question": ["sq2"]},
        {"id": "01f1aaa", "question": ["sq1"]},
    ]
    out = apply_patch(space, {})
    ids = [q["id"] for q in out["config"]["sample_questions"]]
    assert ids == ["01f1aaa", "01f1zzz"]


def test_column_configs_sorted_by_column_name():
    space = _empty_space()
    space["data_sources"]["tables"] = [
        {
            "identifier": "x.y.t1",
            "column_configs": [
                {"column_name": "z_last", "description": ["z"]},
                {"column_name": "a_first", "description": ["a"]},
            ],
        }
    ]
    out = apply_patch(space, {"column_descriptions": {"x.y.t1": {"m_middle": "m"}}})
    names = [c["column_name"] for c in out["data_sources"]["tables"][0]["column_configs"]]
    assert names == ["a_first", "m_middle", "z_last"]


def test_column_configs_sort_is_independent_per_table():
    space = _empty_space()
    space["data_sources"]["tables"] = [
        {
            "identifier": "x.y.t1",
            "column_configs": [
                {"column_name": "c"},
                {"column_name": "a"},
                {"column_name": "b"},
            ],
        },
        {
            "identifier": "x.y.t2",
            "column_configs": [
                {"column_name": "z"},
                {"column_name": "y"},
            ],
        },
    ]
    out = apply_patch(space, {"column_descriptions": {"x.y.t2": {"m": "added"}}})
    t1 = [c["column_name"] for c in out["data_sources"]["tables"][0]["column_configs"]]
    t2 = [c["column_name"] for c in out["data_sources"]["tables"][1]["column_configs"]]
    assert t1 == ["a", "b", "c"]
    assert t2 == ["m", "y", "z"]


# --- patch_summary ---------------------------------------------------------


def test_patch_summary_counts_each_supported_category():
    patch = {
        "instructions": ["a", "b"],
        "table_descriptions": {"x.y.t1": "d"},
        "column_descriptions": {
            "x.y.t1": {"a": "a", "b": "b"},
            "x.y.t2": {"c": "c"},
        },
        "suggested_queries": [{"question": "q"}],
        "trusted_queries": [{"question": "q", "sql": "s"}],
    }
    summary = patch_summary(patch)
    assert summary["instructions"] == 2
    assert summary["table_descriptions"] == 1
    assert summary["column_descriptions"] == 3  # flattened across tables
    assert summary["suggested_queries"] == 1
    assert summary["trusted_queries"] == 1
    assert "_dropped_keys" not in summary


def test_patch_summary_records_dropped_unsupported_keys():
    patch = {
        "instructions": ["a"],
        "joins": [{"from_table": "t1"}],  # not supported in v2
        "noise": "ignored",
    }
    summary = patch_summary(patch)
    assert summary["instructions"] == 1
    assert summary["_dropped_keys"] == ["joins", "noise"]
