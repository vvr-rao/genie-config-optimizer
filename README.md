# genie-config-optimizer

A Python CLI tool that programmatically optimizes a Databricks Genie space.

You give it a CSV of evaluation questions with the table they should hit and a plain-English description of the expected answer. It runs every question against the live Genie space (one Conversation API call per row), then sends all the answers in one batch to Claude. Claude judges each answer and proposes a single consolidated patch to the space's metadata. The patch is auto-applied via the Update Space API, and a snapshot of the space before and after is archived locally so any change is recoverable.

**One Genie space update per CLI run, after every CSV row has been asked.**

The tool can change five categories of Genie space metadata:

- Instructions (`structured_instructions.text_instructions[]`)
- Table descriptions (`data_sources.tables[].description`)
- Column descriptions (`data_sources.tables[].column_configs[].description`)
- Joins / relationships (`data_sources.relationships[]`)
- Suggested queries (`sample_questions[]`)
- Trusted queries / parameterized examples (`structured_instructions.example_question_sqls[]`)

It will not touch anything outside that list.

---

## Requirements

- Python ≥ 3.10
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- A Databricks workspace with a Genie space you have permission to update
- An [Anthropic API key](https://console.anthropic.com/settings/keys)

---

## Setup

```bash
git clone https://github.com/<you>/genie-config-optimizer.git
cd genie-config-optimizer
uv sync
cp .env.example .env       # then fill in real values
cp .config.example .config # then fill in real values
```

### `.env` (gitignored)

Holds secrets only. Loaded with `python-dotenv`.

```
DATABRICKS_TOKEN=dapixxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
```

| Variable            | Where to get it                                                 |
|---------------------|-----------------------------------------------------------------|
| `DATABRICKS_TOKEN`  | Databricks Console → User Settings → Developer → Access tokens  |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys                     |

### `.config` (gitignored)

Non-secret workspace details. INI format.

```ini
[databricks]
host = https://adb-1234567890123456.7.azuredatabricks.net
workspace_id = 1234567890123456
genie_space_id = 01eeXXXXXXXXXXXXXXXXXXXXXXXX

[anthropic]
model = claude-sonnet-4-6
```

| Key                          | Notes                                                                                         |
|------------------------------|-----------------------------------------------------------------------------------------------|
| `databricks.host`            | Workspace URL including `https://`. No trailing slash.                                        |
| `databricks.workspace_id`    | Numeric workspace ID, visible in the URL of any workspace page.                               |
| `databricks.genie_space_id`  | Open the Genie space in the UI; it's the long hex segment in the URL.                         |
| `anthropic.model`            | Optional. Defaults to `claude-sonnet-4-6`.                                                    |

> Both `.env` and `.config` are listed in `.gitignore` — they will never be committed.

---

## CSV format

UTF-8, header row required. Columns:

| Column           | Required | Description                                                                       |
|------------------|----------|-----------------------------------------------------------------------------------|
| `question`       | yes      | The natural-language question to send to Genie.                                    |
| `tables`         | yes      | Pipe-delimited list of one or more `catalog.schema.table` names the answer should touch. Single-table cells are valid (no pipe needed). |
| `expected_answer`| yes      | Plain-English description of the correct answer or analytical logic. May describe simple aggregations OR complex patterns: conditional aggregations, period-over-period comparisons, correlations, distributions, sentiment / text-mining tasks, segmentation, ratio metrics, top/bottom rankings, or graceful-limitation cases (where Genie should state it cannot answer with the available tables). |

The `tables` list is treated as a *hint* about analytical scope, not a hard gate — Genie answering the question correctly via a different valid table path is still a pass.

Example:

```csv
question,tables,expected_answer
"How many users signed up last week?","main.app.users","COUNT(*) on main.app.users WHERE created_at >= now() - interval 7 day"
"Top 5 products by revenue this quarter","main.sales.orders|main.sales.products","Sum order_total grouped by product_id over the current quarter, joined to products for the name; ordered desc, limit 5"
"Month-over-month revenue growth by region","main.sales.orders|main.sales.customers","Group revenue by month and region, then compute MoM percentage change; rank regions by latest growth rate"
"Correlation between price and units sold","main.sales.orders","Compute correlation between unit price and quantity; explain whether higher prices correlate with higher or lower volume"
"Compare employee satisfaction with sales","main.sales.orders","Graceful limitation: employee satisfaction data is not in the listed tables — Genie should state it cannot answer and suggest sales/review alternatives"
```

A larger reference evaluation set lives at [example_csv/bakehouse_genie_test_scenarios.csv](example_csv/bakehouse_genie_test_scenarios.csv) (64 rows over the Databricks `samples.bakehouse.*` tables).

---

## Usage

```bash
# Real run: ask all rows, then PATCH one consolidated patch to the Genie space
uv run genie-config-optimizer run --csv path/to/questions.csv

# Dry run: ask all rows, judge, write before.json + proposed after.json + meta.json
# + summary.md, but skip the PATCH to Databricks. Recommended for the first run.
uv run genie-config-optimizer run --csv path/to/questions.csv --dry-run

# Override the space ID from .config
uv run genie-config-optimizer run --csv questions.csv --space-id 01eeXXXX...

# Smoke-test on the first 2 rows only
uv run genie-config-optimizer run --csv questions.csv --limit 2 --dry-run

# Roll back the Genie space to a prior snapshot (a previous run folder).
# Mutually exclusive with --csv. Reads <folder>/before.json and PATCHes it back.
uv run genie-config-optimizer run --rollback optimizer_runs/2026-05-03T19-07-51Z

# Dry-run the rollback (write archive but skip the PATCH)
uv run genie-config-optimizer run --rollback optimizer_runs/2026-05-03T19-07-51Z --dry-run
```

`--help` lists every flag.

The Update Space call uses `PATCH /api/2.0/genie/spaces/{space_id}` with body `{"serialized_space": "<json>"}` — only the field being changed. Earlier versions used `PUT`, which Databricks does not expose for Genie spaces.

After judging, the run prints a verdict breakdown line so you can see at a glance how Genie did:

```
  -> verdict breakdown: 63 rows: 40 pass (63.5%), 14 partial (22.2%), 9 fail (14.3%)
```

The same counts and percentages appear at the top of `summary.md` as a `## Verdict breakdown` table, and live in `meta.json` under `verdict_counts`.

---

## What gets archived

Every CLI invocation creates one timestamped subfolder under `optimizer_runs/`:

```
optimizer_runs/
├── notes.md                       # tracked top-level notes (committed)
└── 2026-05-03T14-30-00Z/          # per-run subfolder (gitignored)
    ├── before.json                # serialized_space pulled at the start of the run
    ├── after.json                 # serialized_space after the proposed patch was applied
    ├── meta.json                  # CSV inputs, Genie outputs, Claude verdicts, patch, token usage
    └── summary.md                 # human-readable run summary: verdicts + planned changes
```

`meta.json` contains everything you need to reproduce or audit the run: every question, the SQL Genie returned, Claude's verdict and reasoning per row, the consolidated patch, and the Anthropic token usage. `summary.md` is the same information rendered for quick skimming.

`optimizer_runs/notes.md` is committed; every per-run subfolder is gitignored (see `.gitignore` pattern `optimizer_runs/*/`).

### Rolling back a run

If a patch turns out to be wrong, restore the previous configuration with the built-in rollback flag:

```bash
uv run genie-config-optimizer run --rollback optimizer_runs/<timestamp>
```

This reads `optimizer_runs/<timestamp>/before.json`, snapshots the current space as `before.json` in a new timestamped run folder, and PATCHes the loaded JSON back as `serialized_space`. The new folder's `after.json` is the restored snapshot — so the rollback is itself reversible by pointing at the new folder.

Manual fallback via `curl` (if you ever need to bypass the CLI):

```bash
curl -X PATCH "$HOST/api/2.0/genie/spaces/$SPACE_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"serialized_space\": $(jq -Rs . optimizer_runs/<timestamp>/before.json | jq -r .)}"
```

---

## How the run works

```
1. GET  /api/2.0/genie/spaces/{id}?include_serialized_space=true   (snapshot once)
2. for each CSV row:
       POST /spaces/{id}/start-conversation                          (one fresh conversation)
       poll messages until COMPLETED / FAILED / CANCELLED            (5 s interval, 10 min cap)
       GET attachment query-result                                   (capture SQL + result rows)
3. Single Anthropic call: Claude judges all rows AND proposes one consolidated patch
4. apply_patch -> new serialized_space
5. PATCH /spaces/{id}                                                (one update — skipped if --dry-run)
6. write optimizer_runs/<ISO-timestamp>/{before.json, after.json, meta.json, summary.md}
```

The Genie space is **read** at step 1 and **written** once at step 5. No mutation between rows. Each row is independent, so question N's answer is not influenced by question N-1.

---

## Project layout

```
genie-config-optimizer/
├── pyproject.toml
├── README.md
├── .env.example                  # template (committed)
├── .config.example               # template (committed)
├── .gitignore                    # excludes .env, .config, optimizer_runs/*/, .venv, etc.
├── example_csv/                  # sample CSVs (committed)
├── optimizer_runs/               # per-run snapshots (notes.md committed; subfolders gitignored)
└── src/genie_config_optimizer/
    ├── __init__.py
    ├── __main__.py               # `python -m genie_config_optimizer`
    ├── cli.py                    # argparse entry point
    ├── config.py                 # loads .env (python-dotenv) + .config (configparser)
    ├── csv_loader.py             # reads & validates the input CSV
    ├── databricks_client.py      # Genie Conversation API + Update Space client
    ├── anthropic_client.py       # Claude judge + patch proposer (single batch call)
    ├── prompts.py                # system / user prompt templates
    ├── patcher.py                # merges Claude's patch into serialized_space
    ├── archiver.py               # writes optimizer_runs/<timestamp>/{before,after,meta}.json + summary.md
    └── orchestrator.py           # end-to-end run
```
