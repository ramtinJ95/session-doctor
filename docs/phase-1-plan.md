# Phase 1 Plan

## Goal

Phase 1 creates the project foundation for `session-doctor` without implementing
full session ingestion yet.

By the end of Phase 1, the repo should have:

- a working Python package
- a working CLI entry point
- strict schema foundations
- DuckDB persistence scaffolding
- adapter interfaces for Codex, Claude Code, and Pi
- test/lint/type-check tooling
- enough structure for Phase 2 schema and adapter work to proceed cleanly

Phase 1 should not try to classify sessions, parse all native formats, or build
reports. It should create the frame those features will use.

## Locked Tech Stack

Use:

```text
Python 3.12+
uv
Typer
Rich
Pydantic v2
DuckDB
NetworkX
pytest
ruff
ty
```

Dependency intent:

- `uv`: project management, lockfile, command execution
- `Typer`: CLI framework
- `Rich`: terminal output
- `Pydantic v2`: validated normalized schemas
- `DuckDB`: local analytical store
- `NetworkX`: graph projection dependency, included lightly in Phase 1
- `pytest`: tests
- `ruff`: linting and formatting
- `ty`: type checking

Do not add Transformers, scikit-learn, or LLM SDKs in Phase 1.

## Key Decisions

- CLI-first product.
- Local-only by default.
- DuckDB is part of the foundation, not an optional later add-on.
- User and assistant text should be stored locally because classification needs
  it.
- Tool outputs, raw diffs, write content, old/new edit strings, and large command
  outputs should not be stored by default.
- Graph support should be planned from the start. Add NetworkX and graph schema
  placeholders, but do not implement graph analysis yet.
- Codex, Claude Code, and Pi are first-class adapter targets.
- Project/package name is `session-doctor`.
- Import package name is `session_doctor`.
- CLI command is `session-doctor`.

## Proposed Package Shape

```text
session-doctor/
  pyproject.toml
  README.md
  docs/
    session-doctor-design.md
    phase-1-plan.md
  src/
    session_doctor/
      __init__.py
      __main__.py
      cli.py
      config.py
      constants.py
      adapters/
        __init__.py
        base.py
        codex.py
        claude.py
        pi.py
      schemas/
        __init__.py
        common.py
        sessions.py
        events.py
        messages.py
        tools.py
        files.py
        usage.py
        graph.py
        warnings.py
      store/
        __init__.py
        duckdb.py
        migrations.py
      privacy.py
      ids.py
  tests/
    test_cli.py
    test_schemas.py
    test_store.py
    test_adapters_base.py
```

This shape can be adjusted during implementation if the code naturally wants
fewer schema modules at first. Keep the package boundaries explicit.

## CLI Surface For Phase 1

Implement only foundation commands:

```bash
session-doctor --help
session-doctor version
session-doctor doctor
session-doctor db init
session-doctor db info
session-doctor adapters list
```

Command intent:

- `version`: print package version
- `doctor`: check local config, Python version, writable data directory, DuckDB
  availability, and known session source path existence
- `db init`: create the local DuckDB file and schema tables
- `db info`: show configured database path and basic table/status information
- `adapters list`: show built-in adapters and their default discovery roots

Do not implement full `ingest`, `analyze`, `report`, or `graph` behavior in
Phase 1. It is fine to reserve those command groups with clear "not implemented
yet" messages if that makes the CLI easier to understand.

## Storage Location

Use a local application data directory by default, with an environment override.

Recommended default:

```text
~/.local/share/session-doctor/session-doctor.duckdb
```

Override:

```text
SESSION_DOCTOR_DB=/path/to/session-doctor.duckdb
```

Do not write into agent session directories.

Do not add `platformdirs` in Phase 1. Keep the path explicit and predictable.

## DuckDB Schema Scaffold

Create the initial tables even if some columns remain unused until later phases.

Initial tables:

```text
schema_migrations
session_sources
sessions
raw_events
messages
tool_calls
tool_results
command_runs
file_activities
model_usage
parse_warnings
graph_nodes
graph_edges
```

The schema should reflect `Minimal Common Schema v0` from
`docs/session-doctor-design.md`.

Migration behavior for Phase 1 can be simple:

- create database if missing
- create tables if missing
- insert a migration version row
- fail clearly if the existing schema version is newer than the code expects

No destructive migrations in Phase 1.

## Pydantic Schema Scope

Define v0 models for:

```text
AgentName
Confidence
NormalizedRole
SessionSource
Session
RawEvent
Message
ToolCall
ToolResult
CommandRun
FileActivity
ModelUsage
ParseWarning
GraphNode
GraphEdge
```

The schema should support:

- optional timestamps
- missing native IDs
- sidechain/subsession metadata
- content block type summaries
- raw text for user/assistant messages
- structural metadata for tool outputs without storing raw output
- graph projection placeholders

Add tests for:

- model creation with minimal valid fields
- enum validation
- serialization to plain dictionaries
- stable synthetic ID helper behavior
- text hash helper behavior

## Adapter Interfaces

Create adapter interfaces but do not fully parse all formats yet.

The base adapter should define:

```text
name
version
default_roots()
discover()
parse_source()
```

Expected outputs:

```text
discover() -> list[SessionSource]
parse_source() -> ParsedSessionBundle
```

`ParsedSessionBundle` can contain:

```text
session
raw_events
messages
tool_calls
tool_results
command_runs
file_activities
model_usage
parse_warnings
```

For Phase 1, Codex/Claude/Pi adapters can return discovered source records and
raise a clear `NotImplementedError` for full parsing.

Discovery should be real enough to support `session-doctor doctor` and
`session-doctor adapters list`.

## Privacy Helpers

Add a small `privacy.py` foundation.

Phase 1 helpers:

```text
hash_text(text) -> str
text_length(text) -> int
redact_home(path) -> str
looks_sensitive_key(key) -> bool
redact_command_for_display(command) -> str
```

Keep these conservative and test them. More advanced redaction can come later.

## Graph Placeholders

Add graph schema models and database tables in Phase 1.

Do not implement graph analysis yet.

The placeholder should make this future command natural:

```bash
session-doctor graph <session-id>
```

Graph entities should align with the design doc:

```text
GraphNode
  node_id
  session_id
  node_type
  label
  source_event_id
  metadata

GraphEdge
  edge_id
  session_id
  source_node_id
  target_node_id
  edge_type
  confidence
  source_event_id
  metadata
```

Use typed core graph columns plus a JSON metadata column in DuckDB:

```text
graph_nodes:
  node_id
  session_id
  node_type
  label
  source_event_id
  metadata_json

graph_edges:
  edge_id
  session_id
  source_node_id
  target_node_id
  edge_type
  confidence
  source_event_id
  metadata_json
```

This keeps core graph queries simple while allowing edge-specific details to
evolve after the graph projection semantics are clearer.

## Doctor And Adapter Scan Behavior

`doctor` should warn when default session roots are missing, not fail.

Example:

```text
Codex sessions: found
Claude sessions: found
Pi sessions: missing
Database path: writable
Result: ok with warnings
```

`doctor` should fail only for problems that make the CLI foundation unusable:

- unsupported Python version
- DuckDB import failure
- invalid or unwritable database path
- invalid configuration

`adapters list` should not count live session files by default.

Default behavior:

```bash
session-doctor adapters list
```

shows adapter names, default roots, and whether the root exists.

Optional scan behavior:

```bash
session-doctor adapters list --scan
```

counts candidate files and classifies discovered source kinds. This avoids slow
startup as local session stores grow.

## Test Fixture Policy

Phase 1 tests should use synthetic fixtures only.

Do not depend on real files under:

```text
~/.codex
~/.claude
~/.pi
```

The CLI may inspect those paths during interactive `doctor` or
`adapters list --scan` runs, but automated tests must be reproducible and
privacy-safe.

Fixture goals:

- small synthetic Codex-style JSONL file
- small synthetic Claude-style JSONL file
- small synthetic Pi-style JSONL file
- synthetic Claude-style subagent and tool-results paths for discovery tests
- no real user prompts, real command output, secrets, diffs, or source content

## Deliverable Tasks And Commit Plan

Use small commits during Phase 1. Suggested commit boundaries:

### Commit 1: Project Skeleton

Deliverables:

- `pyproject.toml`
- `uv.lock`
- `src/session_doctor/__init__.py`
- `src/session_doctor/__main__.py`
- basic package metadata
- basic `README.md`

Validation:

```bash
uv sync
uv run python -m session_doctor --help
```

### Commit 2: CLI Foundation

Deliverables:

- Typer app in `cli.py`
- Rich console setup
- `version`
- `doctor`
- `adapters list`
- placeholder command groups for future `ingest`, `analyze`, `report`, `graph`

Validation:

```bash
uv run session-doctor --help
uv run session-doctor version
uv run session-doctor doctor
uv run session-doctor adapters list
```

### Commit 3: Core Schemas

Deliverables:

- Pydantic base enums and models
- schema modules under `schemas/`
- ID helpers
- privacy hash/length helpers
- tests for minimal schemas

Validation:

```bash
uv run pytest tests/test_schemas.py -q
uv run ty check
uv run ruff check .
```

### Commit 4: DuckDB Store Scaffold

Deliverables:

- DuckDB store module
- schema migration bootstrap
- `db init`
- `db info`
- tests using temporary DuckDB files

Validation:

```bash
uv run session-doctor db init --db /tmp/session-doctor-test.duckdb
uv run session-doctor db info --db /tmp/session-doctor-test.duckdb
uv run pytest tests/test_store.py -q
```

### Commit 5: Adapter Interfaces And Discovery

Deliverables:

- base adapter protocol/classes
- Codex discovery for `~/.codex/sessions/**/*.jsonl`
- Claude discovery that distinguishes root sessions, subagents, metadata,
  persisted tool results, memory files, and ignored auxiliary files
- Pi discovery for `~/.pi/agent/sessions/**/*.jsonl`
- adapter list includes default roots and availability
- tests with temporary fixture directories

Validation:

```bash
uv run session-doctor adapters list
uv run pytest tests/test_adapters_base.py -q
```

### Commit 6: Tooling And Quality Gate

Deliverables:

- Ruff config
- ty config if needed
- pytest config
- final README usage for Phase 1 commands
- docs cross-link from README to design docs

Validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

## Phase 1 Acceptance Criteria

Phase 1 is complete when:

- `uv sync` succeeds
- `session-doctor --help` works
- `session-doctor doctor` works and reports session source availability
- `session-doctor db init` creates a DuckDB database
- `session-doctor db info` reports schema/database state
- `session-doctor adapters list` shows Codex, Claude Code, and Pi
- `session-doctor adapters list --scan` can count synthetic fixture sources
- Pydantic schemas exist for the v0 common model
- DuckDB tables exist for normalized timeline, tool, file, usage, warning, and
  graph placeholder data
- tests pass
- ruff and ty pass
- no command stores or prints raw tool outputs/diffs/write content by default

## Explicit Non-Goals For Phase 1

- full native session parsing
- ingestion into all normalized tables from real logs
- classification scoring
- sentiment analysis
- graph generation
- project-level trend reports
- LLM/API calls
- MCP server
- skill installation
- UI or HTML reports

## Deferred Questions

These do not block Phase 1:

- Should raw user/assistant text storage become configurable once real ingestion
  begins?
- What exact thresholds should separate `repeat_request` from normal iterative
  clarification?
- Should future graph rendering support Graphviz, HTML, both, or neither?
- Should future project-level trend reports live only in the CLI, or also expose
  an MCP/query surface?
