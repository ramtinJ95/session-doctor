# Phase 4 Plan: Pi Adapter

## Goal

Phase 4 should add Pi as the second native adapter after Codex.

The target vertical slices are:

```text
Pi JSONL source -> Pi adapter -> normalized records -> DuckDB -> sessions list
DuckDB normalized records -> deterministic analysis -> derived rows -> analyze output -> JSON artifact
```

By the end of Phase 4, `session-doctor` should be able to parse Pi session
files, store normalized records locally, show ingested Pi sessions through the
CLI, and run the existing deterministic analysis over Pi-derived normalized
records.

## Current Starting Point

Phases 1 through 3 provide:

- Typer CLI and app entry point
- adapter discovery interfaces for Codex, Claude Code, and Pi
- Pi discovery under `~/.pi/agent/sessions`
- Pydantic normalized schema foundations
- DuckDB persistence for parsed bundles
- delete-and-replace ingestion by `source_id`
- `session-doctor ingest --agent codex`
- `session-doctor sessions list`
- `session-doctor analyze <session-id>`
- deterministic feature extraction and classification over normalized records
- default JSON analysis artifacts
- tests for discovery, Codex parsing, store behavior, CLI ingest/list/analyze,
  and deterministic analysis

The important missing pieces are:

- `PiAdapter.parse_source()`
- Pi parser fixtures
- ingestion support for `--agent pi`
- Pi-specific parser tests
- CLI tests proving Pi ingest and analysis work end to end
- README and design-doc updates for the new adapter surface

## Resolved Implementation Decisions

Phase 4 should use these decisions:

- keep Phase 4 Pi-only
- do not start Claude Code parsing in this phase
- replicate the existing Codex capability for Pi
- support `session-doctor ingest --agent pi`
- allow `--source` to point at one Pi JSONL file or a directory
- scan the default Pi session root when `--source` is omitted
- make `sessions list` work without a Pi-specific listing command
- make the existing `analyze` command work for Pi sessions by relying on the
  normalized records already used by Codex
- parse the full currently observed Pi format, not only the easiest message
  subset
- handle every known Pi top-level row type deliberately
- preserve raw-event rows for all parsed records
- normalize all useful known Pi records into existing normalized tables
- keep expected ignored or metadata-only Pi row types visible in session
  metadata
- keep the existing privacy/storage defaults: full format coverage means every
  observed shape is handled deliberately, not that raw tool output, full edit
  bodies, or full write payloads are stored
- emit parse warnings for malformed JSON, invalid record shapes, unsupported
  future record types, and unsupported future message roles
- keep unknown future Pi records non-fatal
- keep privacy/redaction hardening out of the phase
- avoid LLM calls, ML dependencies, embeddings, or network calls
- keep implementation split into small runnable commit points

## Scope

In scope:

- Pi only
- JSONL parsing from one file or a discovery root
- normalized `Session`, `RawEvent`, `Message`, `ToolCall`, `ToolResult`,
  `CommandRun`, `FileActivity`, `ModelUsage`, and `ParseWarning` records where
  Pi exposes enough information
- complete deliberate handling for all currently observed Pi row types:
  `session`, `message`, `thinking_level_change`, `model_change`, `compaction`,
  `custom`, `branch_summary`, `session_info`, `custom_message`, and `label`
- complete deliberate handling for all currently observed Pi message roles:
  `user`, `assistant`, `toolResult`, and `bashExecution`
- assistant content blocks for `thinking`, `text`, and `toolCall`
- observed tool-call/result names including `bash`, `read`, `edit`, `write`,
  `webfetch`, `websearch`, `todo`, `subagent`, `deep_research`, and
  `deep_research_lite`
- model usage extraction from assistant messages
- command-run extraction from `bashExecution` rows and bash tool results where
  enough data exists
- file-activity extraction from read/edit/write tool activity where enough data
  exists
- DuckDB persistence through the existing parsed-bundle store API
- CLI ingest support for Pi
- analysis over ingested Pi sessions
- synthetic fixtures and tests
- manual smoke-test instructions using copied local Pi session files

Out of scope:

- Claude Code parsing
- new analysis labels or Pi-specific classification rules
- graph projection
- project-level trends
- Markdown reports
- privacy/redaction hardening
- semantic embeddings
- local ML models
- LLM/API calls
- Parquet export
- a new storage schema unless the Pi parser exposes a necessary normalized-field
  gap that cannot be represented with current tables and metadata
- raw-content storage for full tool outputs, diffs, write contents, old strings,
  or new strings

## Current Pi Format Evidence

The design doc already contains an earlier Pi inspection. A fresh read-only
sample before this plan found the same overall shape:

```text
root: ~/.pi/agent/sessions
files: 147 JSONL files
layout: ~/.pi/agent/sessions/<cwd-derived-folder>/<timestamp>_<uuid>.jsonl
```

Recent sampled top-level types:

```text
message
thinking_level_change
session
model_change
custom
compaction
branch_summary
```

Previously observed additional top-level types that should remain covered:

```text
session_info
custom_message
label
```

Recent sampled message roles:

```text
toolResult
assistant
user
bashExecution
```

Recent sampled assistant content block types:

```text
text
toolCall
thinking
```

Recent sampled tool names:

```text
bash
read
edit
write
webfetch
websearch
todo
subagent
deep_research
deep_research_lite
```

The implementation should still use synthetic fixtures in tests and copied
local session files for manual smoke tests. Tests should not read directly from
`~/.pi`.

Full coverage of the currently observed Pi format should follow the design
doc's privacy and storage defaults. The parser should preserve hashes, lengths,
content block types, safe paths, error flags, truncation flags, and selected
metadata for large or sensitive Pi payloads. It should not store raw full tool
outputs, diffs, write contents, or full edit bodies by default.

## Parsing Model

### Source And Session Identity

Pi event IDs are unique within a file but not globally unique. Normalized IDs
must include the `source_id` or session identity when deriving stable IDs.

Use the `session` row as the primary source for:

```text
native_session_id
cwd
agent_version
started_at
```

Keep filename-derived UUID and timestamp metadata separately because the
filename UUID does not always match the native session ID.

Do not derive `cwd` from the containing folder. The folder name is lossy and
only useful as discovery metadata.

If a file lacks a usable `session` row, create a session from the source path and
parse warnings. The parser should still preserve raw events and any records that
can be normalized safely.

### Raw Events

Every successfully parsed Pi JSON object should produce one `RawEvent`.

Use:

```text
event_id = stable ID derived from source/session plus native event id or record index
native_event_type = top-level type
native_event_id = top-level id
native_parent_id = parentId
timestamp = top-level timestamp when parseable
payload_hash = hash of the raw parsed object
metadata_json = Pi-specific metadata needed for traceability
```

Malformed JSONL lines and non-object records should emit `ParseWarning` rows and
not stop parsing the rest of the file.

### Messages

Map `message.role=user` to normalized user messages.

Extract user text from `message.content` text blocks. Preserve content block
types in `content_block_types` and store Pi-specific source metadata such as:

```text
pi_message_role=user
pi_message_source=message
```

Map `message.role=assistant` to normalized assistant messages.

Extract assistant display text from `text` content blocks. Preserve `thinking`
and `toolCall` content types in metadata, but do not put thinking text into the
normalized assistant message text by default. Thinking content can be large and
is not the user-facing assistant answer.

When an assistant text block or signature metadata exposes `phase=final_answer`,
preserve that phase in `message.metadata`. The existing unresolved-ending and
resolved-after-correction analysis logic depends on final-answer evidence when
available.

Unsupported future message roles should emit parse warnings and preserve the raw
event row.

### Tool Calls

Assistant `toolCall` content blocks should become `ToolCall` records.

Use:

```text
tool_call_id = stable ID derived from session/source plus Pi tool call id
native_tool_call_id = content block id
name = content block name
arguments_hash = hash of parsed arguments when available, otherwise partialJson
metadata_json = tool name, argument-shape hints, partialJson presence
```

Prefer parsed `arguments` when present. Keep `partialJson` metadata only when it
is needed for traceability. Do not fail if partial JSON cannot be parsed.

### Tool Results

`message.role=toolResult` should become `ToolResult` records.

Use:

```text
tool_result_id = stable ID derived from session/source plus event id
tool_call_id = normalized ID matching message.toolCallId when present
native_tool_call_id = message.toolCallId
is_error = message.isError or tool-specific error metadata
output_hash = hash of content/details output
output_length = text length of extracted result output
metadata_json = toolName, details shape, truncation/error hints
```

Tool results should link back to assistant tool calls when the native
`toolCallId` is present. Missing links should not fail parsing; they should stay
visible in metadata or warnings depending on severity.

### Command Runs

`message.role=bashExecution` should become `CommandRun`.

Use:

```text
command = message.command
exit_code = message.exitCode
stdout_hash = hash of message.output when present
output_length = text length of message.output
metadata_json = cancelled, truncated, excludeFromContext
```

Bash `toolResult` records may also contain command execution details. When a
tool result has enough structured bash details, create a `CommandRun` linked to
the normalized tool call. If the shape is ambiguous, keep the result as
`ToolResult` only and preserve enough metadata for future refinement.

### File Activity

Read/edit/write tool activity should become `FileActivity` records when the path
and operation can be identified without guessing.

Suggested operation mapping:

```text
read tool -> read
edit tool -> update
write tool -> write
```

The parser should not invent paths from unstructured prose. If a tool result or
arguments payload lacks a reliable path, preserve the tool call/result metadata
and skip `FileActivity` for that event.

### Model Usage

Assistant messages should produce `ModelUsage` records when usage data is
present.

Map:

```text
message.provider -> provider
message.model -> model
message.usage.input -> input_tokens
message.usage.output -> output_tokens
message.usage.cacheRead -> cache_read_tokens
message.usage.cacheWrite -> cache_write_tokens
message.usage.totalTokens -> total_tokens
message.usage.cost.total -> cost when numeric
```

`model_change` rows should update session/model metadata and be preserved in raw
event metadata. If multiple model changes exist, keep the latest parseable
provider/model in session metadata and preserve the history as metadata.

### Metadata-Only Row Types

The parser should deliberately handle these known Pi row types:

```text
thinking_level_change
model_change
compaction
custom
branch_summary
session_info
custom_message
label
```

If a row type does not map cleanly to a normalized table, it should still be
counted in session metadata under a Pi-specific expected row count, for example:

```text
pi_expected_metadata_only_counts
```

This avoids noisy warnings for known benign records while making format drift
visible. Unknown future top-level types should emit parse warnings.

## CLI Shape

Phase 4 should extend the existing `ingest` command:

```bash
session-doctor ingest --agent pi --source <file-or-directory> --db <path>
session-doctor ingest --agent pi --db <path>
session-doctor sessions list --db <path>
session-doctor analyze <pi-session-id> --db <path>
```

`--source` should accept either:

- one Pi JSONL file
- a directory to scan with Pi discovery

If `--source` is omitted, `ingest --agent pi` should use the Pi default root.

The ingest implementation should avoid Codex-specific helper names and summary
labels once Pi is supported. The summary can expose agent-neutral counts:

```text
Database
Agent
Sources
Skipped sources
Sessions
Messages
Commands
File activities
Tool calls
Tool results
Model usage rows
Warnings
```

Codex-specific response-item and event-message fallback counts can stay in the
summary only when the ingested agent is Codex, or move into an adapter metadata
section if that keeps the output cleaner.

## Task Splits And Commit Points

### Commit 1: Phase 4 Plan And Roadmap Docs

Deliverables:

- add this `docs/phase-4-plan.md`
- update `docs/session-doctor-design.md` to point at the Phase 4 plan
- update README design references
- keep Phase 4 marked planned, not complete

Validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Clean commit point:

```text
Phase 4 can be reviewed as a concrete Pi adapter implementation plan before code changes.
```

### Commit 2: Pi Parser Fixtures And Raw Bundle Parsing

Deliverables:

- representative synthetic Pi fixtures under `tests/fixtures/pi/`
- fixtures covering all currently observed top-level row types
- fixtures covering user, assistant, toolResult, and bashExecution message roles
- fixtures covering text, thinking, and toolCall assistant content blocks
- fixtures covering malformed JSON and unsupported future records
- `PiAdapter.parse_source()` implemented for session metadata, raw events,
  user messages, assistant messages, metadata-only row counts, and warnings
- parser tests for stable IDs, source/session metadata, message text extraction,
  final-answer phase preservation, raw event preservation, and warning behavior

Validation:

```bash
uv run pytest tests/test_pi_adapter.py -q
uv run ruff check src/session_doctor/adapters tests/test_pi_adapter.py
uv run ty check
```

Clean commit point:

```text
Pi session, raw event, and message parsing works without touching CLI ingest.
```

### Commit 3: Pi Tools, Commands, Files, And Usage

Deliverables:

- normalize assistant `toolCall` content blocks into `ToolCall`
- normalize `toolResult` rows into `ToolResult`
- link tool results to tool calls when `toolCallId` is available
- normalize `bashExecution` rows into `CommandRun`
- normalize structured bash tool results into `CommandRun` when reliable
- normalize read/edit/write activity into `FileActivity` when reliable path
  evidence exists
- normalize assistant usage payloads into `ModelUsage`
- preserve model-change history in session metadata
- parser tests for tool calls, tool results, command runs, file activities, and
  model usage

Validation:

```bash
uv run pytest tests/test_pi_adapter.py -q
uv run ruff check src/session_doctor/adapters tests/test_pi_adapter.py
uv run ty check
```

Clean commit point:

```text
Pi parser emits the normalized records needed by listing and analysis.
```

### Commit 4: Agent-Neutral Ingest

Deliverables:

- extend `session-doctor ingest` to accept `--agent pi`
- replace Codex-only ingest branching with adapter lookup where appropriate
- keep unsupported agents rejected clearly
- make source handling work for Pi files and directories
- make ingest summary agent-neutral while preserving Codex provenance counts
- CLI tests for Pi ingest from a fixture file
- CLI tests for repeated Pi ingest delete-and-replace behavior

Validation:

```bash
uv run pytest tests/test_cli.py tests/test_pi_adapter.py tests/test_store.py -q
uv run ruff check src/session_doctor tests
uv run ty check
```

Clean commit point:

```text
Pi fixtures can be ingested into DuckDB and listed through the CLI.
```

### Commit 5: Pi Analysis End To End

Deliverables:

- Pi fixture that exercises analysis-relevant normalized records
- CLI test from Pi fixture ingest through `analyze`
- assertions that `analysis_runs`, `session_features`, and classifications are
  persisted for a Pi session
- assertions that JSON artifact output works for Pi
- no Pi-specific analysis fork unless a normalized-data bug is uncovered

Validation:

```bash
rm -f /tmp/session-doctor-phase4.duckdb
uv run session-doctor ingest --agent pi \
  --source tests/fixtures/pi/basic-session.jsonl \
  --db /tmp/session-doctor-phase4.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase4.duckdb
uv run session-doctor analyze <fixture-session-id> \
  --db /tmp/session-doctor-phase4.duckdb
uv run pytest tests/test_cli.py tests/test_analysis.py tests/test_store.py -q
uv run ruff check src tests
uv run ty check
```

Clean commit point:

```text
An ingested Pi session can be analyzed through the same command path as Codex.
```

### Commit 6: Docs And Manual Smoke Test

Deliverables:

- README usage for `ingest --agent pi`
- final design-doc current-state updates for Phase 4 implementation
- manual smoke-test notes using copied local Pi sessions
- final full quality gate

Suggested manual smoke test:

```bash
cp ~/.pi/agent/sessions/<project-folder>/<session>.jsonl /tmp/session-doctor-pi-phase4.jsonl
rm -f /tmp/session-doctor-phase4.duckdb
uv run session-doctor ingest --agent pi \
  --source /tmp/session-doctor-pi-phase4.jsonl \
  --db /tmp/session-doctor-phase4.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase4.duckdb
uv run session-doctor analyze <session-id> --db /tmp/session-doctor-phase4.duckdb
```

Final validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Clean commit point:

```text
Phase 4 behavior is documented and validated end to end.
```

## Recommended Implementation Order

1. Add the Phase 4 plan and roadmap references.
2. Build Pi parsing for session, raw events, messages, metadata-only rows, and
   warnings.
3. Add Pi tool, command, file, and usage normalization.
4. Generalize ingest from Codex-only to Codex plus Pi.
5. Prove Pi analysis works through the existing normalized analysis path.
6. Update README/design docs and run copied-local-session smoke tests.

This keeps parser behavior, CLI behavior, and analysis verification separately
reviewable.

## Acceptance Criteria

Phase 4 is complete when:

- `session-doctor ingest --agent pi --source <fixture>` works
- `session-doctor ingest --agent pi --db <tmpdb>` scans the default Pi root
  when run manually
- `sessions list` shows ingested Pi sessions with source paths and normalized
  counts
- repeated ingestion of the same Pi source delete-and-replaces old records
- `session-doctor analyze <pi-session-id> --db <tmpdb>` runs against an
  ingested Pi fixture
- Pi analysis persists derived rows and writes a JSON artifact by default
- all currently observed Pi top-level row types are handled deliberately
- all currently observed Pi message roles are handled deliberately
- assistant text, tool calls, tool results, bash execution, model usage, and
  read/edit/write file activity are covered by parser tests
- metadata-only Pi rows are counted without noisy warnings
- unknown future Pi row types and message roles emit parse warnings without
  stopping the file
- tests do not depend on live files under `~/.pi`
- no Claude parser, graph projection, privacy/redaction system, LLM call, ML
  dependency, or new analysis-label layer is introduced in Phase 4
- the full quality gate passes

## Open Questions For Implementation Review

There are no known product decisions blocking Phase 4 implementation.

Implementation may still uncover field-level questions in copied local Pi
sessions. Those should be resolved with small fixture additions and parser tests
rather than expanding Phase 4 into Claude parsing or graph projection.
