# Phase 7 Plan: Aggregate Summary MVP

Status: complete.

Implemented scope:

- Added a read-only `session-doctor summary` command.
- Added terminal and JSON aggregate summary output.
- Added optional `--agent`, `--project`, and global `--limit` filters.
- Added DuckDB aggregate queries for session totals, analysis coverage, agent
  counts, project/cwd counts, classification counts, recent risky sessions,
  failed commands, and repeated files in problematic sessions.
- Added deterministic next-step recommendations.
- Required an existing database for summary runs.
- Kept project filtering tied to normalized `sessions.project_path` and
  `sessions.cwd` rather than source log paths.
- Kept Phase 7 read-only: no artifacts, derived writes, schema migration,
  graph projection, project trends, Markdown reports, LLM calls, embeddings, or
  ML dependencies.

## Goal

Phase 7 should add the first aggregate, read-only view over the local DuckDB
store before graph projection, project trends, or polished Markdown reports.

The target vertical slice is:

```text
ingested Codex/Pi sessions + optional existing analysis rows
  -> aggregate DuckDB queries
  -> compact terminal summary + machine-readable JSON
```

By the end of Phase 7, a user or agent should be able to ask the local store:

- how many sessions are present?
- how many are analyzed?
- which agents and projects are represented?
- which classifications appear most often?
- which recent sessions look stuck, blocked, or looping?
- which commands fail most often?
- which files are repeatedly edited in problematic sessions?
- where should I inspect next?

The phase should be query-oriented and deterministic. It should reuse the
normalized tables and Phase 6 analysis rows instead of introducing graph
projection, report generation, trend storage, or an LLM-assisted layer.

## Starting Point Before Implementation

Phases 1 through 6 provide:

- Typer CLI and nested command apps in `src/session_doctor/cli.py`
- option validation helpers in `src/session_doctor/cli_options.py`
- Rich terminal renderers in `src/session_doctor/cli_renderers.py`
- Codex and Pi parsing into normalized Pydantic bundles
- DuckDB persistence split across `store/readers.py`, `store/writers.py`,
  `store/row_loaders.py`, and `store/row_mappers.py`
- session listing through `DuckDBStore.list_session_summaries()`
- single-session analysis through `analyze_session()` and
  `DuckDBStore.replace_analysis_rows()`
- Phase 6 session score features:
  - `friction_score`
  - `stuckness_score`
  - `prompt_clarity_risk`
  - `agent_fit_risk`
  - `project_complexity_signal`
- Phase 6 classification labels:
  - `user_stuck`
  - `tooling_blocked`
  - `agent_looping`
  - `resolved_after_corrections`
  - `healthy`
  - `agent_misunderstood`
  - `prompt_ambiguous`
  - `task_too_large`
  - `repo_complexity_high`
  - `abandoned_or_stopped`
- privacy helpers for display redaction in `src/session_doctor/privacy.py`
- tests for CLI behavior, store initialization/round-tripping, ingestion,
  analysis persistence, JSON artifacts, and feature/classification behavior

Existing graph tables are still placeholders and should not be populated in
Phase 7.

## Codepaths To Focus On

Phase 7 should primarily touch these paths:

```text
src/session_doctor/cli.py
src/session_doctor/cli_options.py
src/session_doctor/cli_renderers.py
src/session_doctor/privacy.py
src/session_doctor/store/duckdb.py
src/session_doctor/store/models.py
src/session_doctor/store/readers.py
tests/test_cli.py
tests/test_store.py
README.md
docs/session-doctor-design.md
```

Recommended new store module:

```text
src/session_doctor/store/summary_readers.py
```

`summary_readers.py` should keep aggregate SQL separate from the existing
single-session bundle loaders. `DuckDBStore` should expose a thin method that
delegates to that module, matching the existing pattern used by `readers.py`
and `writers.py`.

No adapter modules, parser schemas, analysis formulas, classification rules,
graph schemas, or migration DDL should need changes unless implementation
proves a small additive model is necessary.

## Resolved Implementation Decisions

Phase 7 should use these decisions:

- add a top-level `summary` command
- require an existing database for `summary`; missing database paths should fail
  clearly instead of rendering as an empty store
- keep `summary` read-only; it must not ingest, analyze, or write artifacts
- do not auto-run analysis for sessions that are missing analysis rows
- report analysis coverage clearly so missing analysis is visible
- rank risky sessions only from existing analysis rows
- count all ingested sessions even when they have not been analyzed
- use existing normalized tables and analysis tables; do not add a migration
- avoid graph projection and leave `session-doctor graph <session-id>` as a
  reserved not-implemented command
- avoid Markdown reports and leave `session-doctor report <session-id>` as a
  reserved not-implemented command
- avoid project trend time-series semantics; Phase 8 should own week/month trend
  views
- keep output local-only, deterministic, and explainable
- avoid LLM calls, embeddings, ML dependencies, and network calls
- redact commands and home-relative paths before display and JSON output
- keep raw message text, raw command output, raw tool output, and raw diffs out
  of summary output
- add JSON output because aggregate summaries are useful to agents and tests,
  but do not write default summary artifacts in this phase
- filter `--project` only with stored `sessions.project_path` and `sessions.cwd`,
  not with `session_sources.source_path`
- use one global `--limit` in Phase 7 for ranked/detail sections; defer
  section-specific limits until the output proves it needs them

## Proposed CLI Shape

Add:

```bash
session-doctor summary [--db PATH]
session-doctor summary [--db PATH] --project /path/to/project
session-doctor summary [--db PATH] --agent codex
session-doctor summary [--db PATH] --agent pi
session-doctor summary [--db PATH] --limit 10
session-doctor summary [--db PATH] --format terminal
session-doctor summary [--db PATH] --format json
```

Default behavior:

- `--format terminal`
- `--limit 10`
- no project filter
- no agent filter

`--agent` should filter sessions by stored `sessions.agent_name`. Unlike
`ingest --agent`, summary does not need to reject discovered-but-unparsed agents
if a future database somehow contains them. It should still reject unknown agent
names clearly.

`--project` should filter sessions by normalized project/cwd path:

```text
sessions.project_path == project
OR sessions.project_path is under project
OR sessions.cwd == project
OR sessions.cwd is under project
```

Do not derive project identity from encoded agent session directory names.

`summary` should require the database path to exist. A missing database should
be reported as a missing store, not as a valid empty summary.

`--limit` is a row cap for ranked/detail sections. It does not limit the number
of sessions included in aggregate counts. In Phase 7 the same value should cap
projects, classifications, recent risky sessions, failed commands, and repeated
files. Section-specific limits can be added later if one global cap proves too
coarse.

## Summary Data Model

Add small dataclasses in `src/session_doctor/store/models.py` or a focused
summary model module. Keep them plain dataclasses unless Pydantic validation is
needed by implementation.

Suggested models:

```python
@dataclass(frozen=True)
class SummaryFilters:
    agent_name: str | None = None
    project_path: str | None = None
    limit: int = 10


@dataclass(frozen=True)
class AggregateSummary:
    filters: SummaryFilters
    total_sessions: int
    analyzed_sessions: int
    unanalyzed_sessions: int
    agent_counts: tuple[AgentSessionCount, ...]
    project_counts: tuple[ProjectSessionCount, ...]
    classification_counts: tuple[ClassificationCount, ...]
    recent_risk_sessions: tuple[RecentRiskSession, ...]
    failed_commands: tuple[FailedCommandSummary, ...]
    repeated_files: tuple[RepeatedFileSummary, ...]
    recommendations: tuple[str, ...]
```

Keep JSON conversion explicit in the CLI/payload layer so terminal display and
machine-readable output can share the same source object without relying on
dataclass internals leaking through Typer/Rich.

## Query Semantics

### Base Session Set

All summary sections should start from the same filtered session set:

```sql
SELECT s.*
FROM sessions s
WHERE optional_agent_filter
  AND optional_project_filter
```

The `project_path` and `cwd` fields are the project filter source of truth.
`source_path` should be shown as provenance only, not used to infer project.

### Analysis Coverage

Count analyzed sessions by joining filtered sessions to `analysis_runs`:

```sql
COUNT(DISTINCT analysis_runs.session_id)
```

Current write behavior keeps one analysis run per session, but the summary
queries should still use a latest-run CTE so the code remains safe if future
history support appears:

```sql
WITH latest_analysis AS (
  SELECT session_id, max(completed_at) AS completed_at
  FROM analysis_runs
  GROUP BY session_id
)
```

### Classification Counts

Group labels from `session_classifications` for analyzed sessions in the
filtered set. Sort by count descending, then label ascending.

Only count labels attached to the current/latest analysis run when that can be
determined. If the current schema makes that cumbersome, document that Phase 7
relies on the existing delete-and-replace invariant and add a regression test
around `replace_analysis_rows()` plus summary counts.

### Risk Session Ranking

Rank recent risky sessions from existing analysis rows using Phase 6 score
features and labels.

Useful score features:

```text
friction_score
stuckness_score
prompt_clarity_risk
agent_fit_risk
project_complexity_signal
```

Useful risk labels:

```text
user_stuck
tooling_blocked
agent_looping
agent_misunderstood
prompt_ambiguous
task_too_large
repo_complexity_high
abandoned_or_stopped
```

Sort by:

1. highest max risk score
2. presence of `user_stuck`, `tooling_blocked`, or `agent_looping`
3. newest `sessions.started_at`
4. stable `session_id`

Do not include message text in the risk table. Include session ID, agent,
started time, project/cwd display path, labels, and score values.

### Failed Commands

Group failed commands over the filtered session set:

```text
exit_code IS NOT NULL AND exit_code != 0
```

Also treat obvious cancellation/interruption metadata as failure-like if it is
already present in `command_runs.metadata_json`, but keep this conservative and
tested.

Output fields:

- redacted command text
- failed run count
- distinct session count
- agents involved
- most recent failure timestamp
- example session ID

Use `redact_command_for_display()` before returning data to the renderer or JSON
payload. The summary output should not expose raw unredacted command text.

### Repeated Files In Problematic Sessions

Define problematic sessions as analyzed sessions with at least one risk label or
a high risk score. Candidate high-risk threshold:

```text
max(friction_score, stuckness_score, agent_fit_risk) >= 0.55
```

Group mutating file activity paths in those sessions. Exclude pure reads by
default.

Candidate mutating operations:

```text
edit
update
write
patch
move
delete
```

Output fields:

- home-redacted path
- edit/activity count
- distinct session count
- agents involved
- most recent activity timestamp
- example session ID

Use `redact_home()` before returning display/JSON payload data.

### Recommendations / Where To Look Next

Generate a small deterministic list from aggregate facts. Example rules:

- if no sessions are ingested: suggest ingesting Codex or Pi sessions
- if many sessions are unanalyzed: suggest analyzing recent sessions first
- if `tooling_blocked` is common: suggest inspecting top failed commands
- if `agent_looping` is common: suggest inspecting repeated commands/files
- if one failed command dominates: suggest opening the example session
- if one file dominates problematic sessions: suggest reviewing that file's
  recent sessions
- otherwise, suggest inspecting the highest-risk recent session

Keep wording conservative. These are pointers, not diagnoses.

## Terminal Output

Default terminal output should be compact and table-oriented.

Suggested sections:

```text
Aggregate summary
  Database
  Filters
  Sessions
  Analyzed
  Unanalyzed

Agents
  Agent | Sessions | Analyzed

Projects
  Project/CWD | Sessions | Analyzed

Classifications
  Label | Sessions

Recent risky sessions
  Session ID | Agent | Started | Labels | Stuck | Friction | Fit | Project/CWD

Failed commands
  Command | Failures | Sessions | Agents | Recent | Example session

Repeated files in problematic sessions
  Path | Activities | Sessions | Agents | Recent | Example session

Where to look next
  - ...
```

If a section has no rows, render a short `none` or `no analyzed sessions yet`
message rather than omitting the section silently.

## JSON Output

`summary --format json` should return one object with stable keys:

```json
{
  "filters": {
    "agent": null,
    "project": null,
    "limit": 10
  },
  "totals": {
    "sessions": 0,
    "analyzed_sessions": 0,
    "unanalyzed_sessions": 0
  },
  "agents": [],
  "projects": [],
  "classifications": [],
  "recent_risk_sessions": [],
  "failed_commands": [],
  "repeated_files": [],
  "recommendations": []
}
```

Do not write default artifacts for summary output in Phase 7.

## Privacy And Storage Defaults

Phase 7 should not store anything new by default.

Summary output should not include:

- raw message text
- raw tool output
- raw command stdout/stderr
- raw diffs
- raw write/edit content
- full raw metadata payloads

Summary output may include:

- session IDs
- agent names
- timestamps
- classification labels
- risk scores
- redacted command text
- home-redacted paths
- counts
- event/session IDs as provenance where useful

Add tests that prove obvious command secrets are redacted before terminal and
JSON output.

## Task Splits And Commit Points

### Commit 1: Phase 7 Plan And Roadmap Docs

Deliverables:

- add this `docs/phase-7-plan.md`
- update `docs/session-doctor-design.md` to link to this plan after review
- update README design references after review
- keep Phase 7 marked planned, not complete
- do not touch runtime code in this commit

Expected tests:

- full existing suite remains green because this commit is docs-only

Validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Clean commit point:

```text
Phase 7 can be reviewed as a concrete aggregate-summary plan before code changes.
```

### Commit 2: Summary Store Models And Read Queries

Deliverables:

- add summary dataclasses
- add `store/summary_readers.py`
- add a thin `DuckDBStore.aggregate_summary(filters)` method
- query total/analyzed sessions, agent counts, project counts, classification
  counts, recent risk sessions, failed commands, and repeated files
- keep queries read-only and parameterized
- tests for unfiltered, agent-filtered, and project-filtered summaries

Likely files:

```text
src/session_doctor/store/models.py
src/session_doctor/store/summary_readers.py
src/session_doctor/store/duckdb.py
tests/test_store.py
```

Implementation sketch:

```python
def aggregate_summary(database_path: Path, filters: SummaryFilters) -> AggregateSummary:
    if not database_path.exists():
        return empty_summary(filters)
    with read_connection(database_path) as connection:
        return AggregateSummary(
            filters=filters,
            total_sessions=count_sessions(connection, filters),
            analyzed_sessions=count_analyzed_sessions(connection, filters),
            agent_counts=agent_counts(connection, filters),
            project_counts=project_counts(connection, filters),
            classification_counts=classification_counts(connection, filters),
            recent_risk_sessions=recent_risk_sessions(connection, filters),
            failed_commands=failed_commands(connection, filters),
            repeated_files=repeated_files(connection, filters),
            recommendations=(),
        )
```

Expected tests:

- empty initialized database returns zero totals and empty sections
- ingested sessions count even when no analysis rows exist
- analyzed coverage increases after `analyze_session()` or direct
  `replace_analysis_rows()` setup
- classification labels are counted correctly
- recent risk sessions sort by risk score and recency
- agent and project filters affect every section consistently

Validation:

```bash
uv run pytest tests/test_store.py -q
uv run ruff check src/session_doctor/store tests/test_store.py
uv run ty check
```

Clean commit point:

```text
Aggregate summary data can be queried from DuckDB without adding a CLI command.
```

### Commit 3: Summary Payload, Redaction, And Recommendations

Deliverables:

- add explicit summary JSON payload conversion
- apply `redact_command_for_display()` to failed-command output
- apply `redact_home()` to project, cwd, source, and file-path display fields
- add deterministic recommendations from aggregate facts
- tests for terminal-safe and JSON-safe redaction

Likely files:

```text
src/session_doctor/privacy.py
src/session_doctor/store/summary_readers.py
possibly src/session_doctor/summary_payload.py
tests/test_store.py
```

Implementation sketch:

```python
def summary_payload(summary: AggregateSummary) -> dict[str, object]:
    return {
        "filters": filters_payload(summary.filters),
        "totals": totals_payload(summary),
        "agents": [agent_count_payload(row) for row in summary.agent_counts],
        "projects": [project_count_payload(row) for row in summary.project_counts],
        "classifications": [classification_payload(row) for row in summary.classification_counts],
        "recent_risk_sessions": [risk_session_payload(row) for row in summary.recent_risk_sessions],
        "failed_commands": [failed_command_payload(row) for row in summary.failed_commands],
        "repeated_files": [repeated_file_payload(row) for row in summary.repeated_files],
        "recommendations": list(summary.recommendations),
    }
```

Expected tests:

- commands containing `TOKEN=...`, `api_key=...`, or `password=...` are redacted
- home directory prefixes are shown as `~`
- recommendations mention missing analysis when unanalyzed sessions exist
- recommendations point to top failed commands or top risky sessions when useful

Validation:

```bash
uv run pytest tests/test_store.py -q
uv run ruff check src tests/test_store.py
uv run ty check
```

Clean commit point:

```text
Summary data is safe to render and has deterministic next-step guidance.
```

### Commit 4: CLI Command And Terminal Renderer

Deliverables:

- add top-level `session-doctor summary`
- add `--db`, `--project`, `--agent`, `--limit`, and `--format` options
- add summary option validation helpers
- add terminal renderer sections
- add JSON output path with stable keys
- do not add artifacts

Likely files:

```text
src/session_doctor/cli.py
src/session_doctor/cli_options.py
src/session_doctor/cli_renderers.py
tests/test_cli.py
```

Implementation sketch:

```python
@app.command()
def summary(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    agent: Annotated[str | None, typer.Option("--agent")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 10,
    output_format: Annotated[str, typer.Option("--format")] = "terminal",
) -> None:
    require_summary_output_format(output_format)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    filters = summary_filters(agent=agent, project=project, limit=limit)
    aggregate = DuckDBStore(database_path).aggregate_summary(filters)
    if output_format == "json":
        typer.echo(json.dumps(summary_payload(aggregate), indent=2, sort_keys=True, default=str))
        return
    render_summary(aggregate, database_path, console)
```

Expected tests:

- `summary --help` shows the new command options
- `summary --db <missing>` exits clearly instead of rendering an empty summary
- `summary --db <initialized-empty>` renders zero totals
- summary after fixture ingestion shows sessions and agents
- summary after fixture analysis shows analyzed count and classifications
- `summary --agent pi` filters out Codex rows
- `summary --project <path>` filters by cwd/project prefix
- `summary --format json` emits parseable stable keys
- invalid `--format` and invalid `--agent` exit with code 2
- `report` and `graph` remain not implemented

Validation:

```bash
uv run pytest tests/test_cli.py tests/test_store.py -q
uv run ruff check src/session_doctor tests
uv run ty check
```

Clean commit point:

```text
The aggregate summary command works for terminal users and agents.
```

### Commit 5: End-To-End Fixture Coverage And Docs

Deliverables:

- add end-to-end CLI tests with both Codex and Pi fixture sessions in one DB
- update README usage with `summary` examples
- update `docs/session-doctor-design.md` current state and Phase 7 status after
  implementation is complete
- keep Phase 8 project trends and Phase 9 reports/graph deferred
- final full quality gate

Likely files:

```text
README.md
docs/session-doctor-design.md
docs/phase-7-plan.md
tests/test_cli.py
```

Suggested manual smoke test:

```bash
rm -f /tmp/session-doctor-phase7.duckdb
uv run session-doctor ingest --agent codex \
  --source tests/fixtures/codex/repeated-failure-session.jsonl \
  --db /tmp/session-doctor-phase7.duckdb
uv run session-doctor ingest --agent pi \
  --source tests/fixtures/pi/repeated-failure-session.jsonl \
  --db /tmp/session-doctor-phase7.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase7.duckdb
uv run session-doctor analyze <codex-session-id> --db /tmp/session-doctor-phase7.duckdb
uv run session-doctor analyze <pi-session-id> --db /tmp/session-doctor-phase7.duckdb
uv run session-doctor summary --db /tmp/session-doctor-phase7.duckdb
uv run session-doctor summary --db /tmp/session-doctor-phase7.duckdb --format json
uv run session-doctor summary --db /tmp/session-doctor-phase7.duckdb --agent pi
uv run session-doctor summary --db /tmp/session-doctor-phase7.duckdb --project <fixture-cwd>
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
Phase 7 aggregate summary behavior is documented and validated end to end.
```

## Recommended Implementation Order

1. Review and approve this plan.
2. Add store summary models and read-only aggregate queries.
3. Add redacted payload conversion and deterministic recommendations.
4. Add the CLI command and terminal renderer.
5. Add end-to-end fixture coverage and docs.
6. Run a copied/local-session smoke test only after fixture behavior is stable.

## Acceptance Criteria

Phase 7 is complete when:

- `session-doctor summary --db <path>` works on an initialized database
- `summary` reports total, analyzed, and unanalyzed session counts
- `summary` reports agent counts
- `summary --project <path>` filters by stored `project_path`/`cwd` prefix
- `summary --agent codex` and `summary --agent pi` filter consistently
- common classification labels are counted from analysis rows
- recent risky sessions are ranked using Phase 6 score/classification evidence
- failed commands are grouped without exposing obvious command secrets
- repeated files in problematic sessions are grouped with home-redacted paths
- deterministic recommendations are present in terminal and JSON output
- `summary --format json` emits stable machine-readable keys
- the command does not write artifacts or derived rows
- no schema migration is added unless a small additive change is explicitly
  justified during implementation
- report and graph remain deferred
- project trends remain deferred to Phase 8
- all tests and quality gates pass

## Open Questions Before Implementation

None. Review resolved these decisions:

- `summary` requires an existing database and should fail clearly for missing
  database paths.
- `summary --format json` is included in Phase 7.
- `summary --project` filters only by normalized `project_path`/`cwd` fields,
  not by source log paths.
- Phase 7 uses one global `--limit` with default `10` for ranked/detail
  sections, with section-specific limits deferred unless needed later.
