# Phase 2 Plan: Codex Parse And Ingest MVP

## Goal

Phase 2 should prove the core pipeline end to end for one agent before adding
classification, reports, or more adapters.

The target vertical slice is:

```text
Codex JSONL source -> Codex adapter -> normalized records -> DuckDB -> sessions list
```

By the end of Phase 2, `session-doctor` should be able to parse a Codex session
file, store normalized records locally, and show the ingested session through the
CLI.

## Current Starting Point

Phase 1 already provides:

- Typer CLI and reserved `ingest`, `analyze`, `report`, and `graph` commands
- `adapters list` and real Codex/Claude/Pi discovery
- Pydantic models for the v0 normalized shape
- DuckDB schema scaffold and `db init` / `db info`
- existing privacy helpers for text hashing, path display, and command
  redaction, though privacy hardening is not a Phase 2 focus
- stable ID helpers
- synthetic tests for CLI, schemas, store initialization, and discovery

The important missing pieces are:

- native Codex parsing
- DuckDB insert/query methods for parsed bundles
- `session-doctor ingest`
- `session-doctor sessions list`
- parser and ingest tests with representative Codex fixtures

## Resolved Implementation Decisions

Phase 2 should use these decisions:

- repeated ingestion of the same source should delete and replace records for
  that `source_id`
- `session-doctor ingest --agent codex` should scan the default Codex root when
  `--source` is omitted
- malformed JSONL lines should emit parse warnings and parsing should continue
- `response_item` messages are the canonical message representation
- `event_msg` user/agent messages should be used only as fallback messages when
  a matching `response_item` message is missing
- every normalized message should make its Codex source representation visible
  through metadata, for example `codex_message_source=response_item` or
  `codex_message_source=event_msg_fallback`
- ingest output and session summaries should expose message source counts so it
  is obvious if `event_msg_fallback` starts becoming the dominant path
- `sessions list` should show full source paths for Phase 2
- manual smoke tests should copy one local Codex session file to `/tmp` before
  ingestion instead of reading directly from the live session store
- implementation should be committed in the small task splits below

## Scope

In scope:

- Codex only
- JSONL parsing from a single file or a discovery root
- normalized `Session`, `RawEvent`, `Message`, `ToolCall`, `ToolResult`,
  `CommandRun`, `FileActivity`, `ModelUsage`, and `ParseWarning` records where
  Codex exposes enough information
- DuckDB persistence for parsed records
- an inspectable `sessions list` command
- deterministic synthetic fixtures
- manual smoke-test instructions using a copied local Codex session

Out of scope:

- Claude Code or Pi parsing
- feature extraction
- classification labels or scores
- sentiment analysis
- reports
- graph projection
- privacy/redaction hardening
- raw-content policy work beyond what the current schema already supports
- LLM/API calls
- training data or ML dependencies

## Parsing Rules

Use `response_item` messages as the canonical source for normalized user,
assistant, and developer messages. Mark normalized records created from this
path with metadata such as:

```text
codex_message_source=response_item
```

Use `event_msg` for richer command and file-edit events:

- `payload.type=exec_command_end` -> `CommandRun`
- `payload.type=patch_apply_end` -> `FileActivity`
- `payload.type=error` -> `ParseWarning` or error metadata, depending on shape

For `event_msg` user/agent messages, use fallback parsing only when no matching
`response_item` message exists. Mark those normalized records with metadata such
as:

```text
codex_message_source=event_msg_fallback
```

The parser should also keep aggregate counts for message source paths in session
or source metadata so later smoke tests can reveal whether Codex format drift is
pushing the parser into fallback mode.

Use `response_item` tool-call records for `ToolCall` and `ToolResult` where
available:

- `function_call`
- `function_call_output`
- `custom_tool_call`
- `custom_tool_call_output`

Use `turn_context` to fill session-level model metadata when available. Use
`session_meta` for native session ID, cwd, agent version, model provider, and
source metadata.

Do not assume:

- every line parses as valid JSON
- every record has `payload`
- every payload has `type`
- every session has a final answer
- command events always have stdout/stderr
- event timestamps are always present or parseable

## Deferred Privacy Work

Phase 2 should not become a privacy/redaction project. The implementation should
use the current normalized schema and avoid adding masking, redaction, raw-output
policy controls, or special privacy modes during this slice.

This means:

- parse and store the fields that the existing schema already supports
- do not add new redaction rules as part of `ingest`
- do not add raw-content configuration flags
- do not block parser progress on deciding the final long-term privacy policy

Privacy hardening can come after the parse-and-ingest path is useful enough to
test against real copied sessions.

## Proposed CLI Shape

```bash
session-doctor ingest --agent codex --source <file-or-directory> --db <path>
session-doctor ingest --agent codex --db <path>
session-doctor sessions list --db <path>
```

`--source` should accept either:

- one Codex JSONL file
- a directory to scan with the existing Codex discovery logic

If `--source` is omitted, `ingest --agent codex` should use the Codex default
root.

## Task Splits And Commit Points

### Commit 1: Codex Parser Fixtures And Bundle Parsing

Deliverables:

- representative synthetic Codex JSONL fixtures under `tests/fixtures/codex/`
- `CodexAdapter.parse_source()` implemented for the core event shapes
- helper functions for text extraction, timestamp parsing, content block
  summaries, and warning creation
- parser tests covering messages, tool calls, command runs, patch/file
  activity, compacted records, malformed JSON, and unsupported records
- parser tests asserting `codex_message_source` metadata for canonical
  `response_item` messages and fallback `event_msg` messages
- parser tests asserting message source counts are captured in session or source
  metadata

Validation:

```bash
uv run pytest tests/test_codex_adapter.py -q
uv run ruff check src/session_doctor/adapters tests/test_codex_adapter.py
uv run ty check
```

Clean commit point:

```text
Codex fixture parsing works without touching DuckDB or CLI ingest.
```

### Commit 2: DuckDB Bundle Persistence

Deliverables:

- `DuckDBStore.insert_parsed_bundle(...)` or equivalent write API
- table-specific insert helpers for normalized records
- idempotent behavior for repeated ingestion of the same source
- store tests that insert a parsed synthetic bundle and assert table counts
- clear handling for schema initialization before inserts

Validation:

```bash
uv run pytest tests/test_store.py -q
uv run pytest tests/test_codex_adapter.py tests/test_store.py -q
uv run ruff check src/session_doctor/store tests/test_store.py
uv run ty check
```

Clean commit point:

```text
Synthetic parsed bundles can be written to and queried from DuckDB.
```

### Commit 3: `session-doctor ingest` For Codex

Deliverables:

- real `ingest` command replacing the Phase 1 placeholder
- `--agent codex`
- `--source <file-or-directory>`
- `--db <path>`
- terminal summary with source count, session count, message count, command
  count, file-activity count, warning count, skipped source count, and message
  source counts
- CLI tests using temporary fixture files and a temporary DuckDB path

Validation:

```bash
uv run session-doctor db init --db /tmp/session-doctor-phase2.duckdb
uv run session-doctor ingest --agent codex \
  --source tests/fixtures/codex/basic-session.jsonl \
  --db /tmp/session-doctor-phase2.duckdb
uv run pytest tests/test_cli.py tests/test_codex_adapter.py tests/test_store.py -q
uv run ruff check src tests
uv run ty check
```

Clean commit point:

```text
The CLI can ingest one Codex fixture into DuckDB.
```

### Commit 4: `session-doctor sessions list`

Deliverables:

- `sessions` Typer subcommand group
- `sessions list --db <path>`
- table output with session ID, agent, started/ended timestamps, cwd/project,
  message count, response-item message count, event-message fallback count,
  command count, warning count, and source path
- CLI tests that ingest a fixture and then list it
- store query method for session summaries

Validation:

```bash
uv run session-doctor sessions list --db /tmp/session-doctor-phase2.duckdb
uv run pytest tests/test_cli.py tests/test_store.py -q
uv run ruff check src tests
uv run ty check
```

Clean commit point:

```text
An ingested Codex session is visible through a read-only CLI command.
```

### Commit 5: Manual Smoke Test And Documentation

Deliverables:

- README usage updated for Phase 2 commands
- this plan updated if implementation decisions changed
- manual smoke test against a copied local Codex session file, not a live file
- final full quality gate

Suggested manual smoke test:

```bash
cp ~/.codex/sessions/YYYY/MM/DD/<session>.jsonl /tmp/session-doctor-codex-smoke.jsonl
rm -f /tmp/session-doctor-phase2.duckdb
uv run session-doctor db init --db /tmp/session-doctor-phase2.duckdb
uv run session-doctor ingest --agent codex \
  --source /tmp/session-doctor-codex-smoke.jsonl \
  --db /tmp/session-doctor-phase2.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase2.duckdb
```

Copying the file first keeps the smoke test focused on one stable snapshot. The
live Codex session store can change while testing, and scanning it directly can
accidentally turn a single-file smoke test into a broad ingest of many private
sessions.

Final validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Clean commit point:

```text
Phase 2 behavior is documented and the full quality gate passes.
```

## Recommended Implementation Order

1. Parser fixtures first.
2. Codex parser second.
3. Store write API third.
4. CLI ingest fourth.
5. Session listing fifth.
6. README and smoke-test documentation last.

This keeps each commit independently testable and avoids mixing parser work,
database writes, and CLI behavior in one large change.

## Acceptance Criteria

Phase 2 is complete when:

- `CodexAdapter.parse_source()` returns normalized records for representative
  Codex JSONL fixtures
- malformed or unsupported records produce parse warnings instead of crashing the
  full ingest
- `session-doctor ingest --agent codex --source <fixture> --db <tmpdb>` writes
  normalized records to DuckDB
- repeated ingestion of the same source does not duplicate records
- `session-doctor sessions list --db <tmpdb>` shows the ingested session
- normalized messages record whether they came from `response_item` or
  `event_msg_fallback`
- ingest output and `sessions list` make fallback usage visible
- tests do not depend on real files under `~/.codex`
- no new privacy/redaction system is introduced in Phase 2
- the full quality gate passes

## Remaining Questions

There are no known product decisions blocking Phase 2 implementation. Any new
questions should come from parser evidence while implementing the Codex vertical
slice.
