# Phase 3 Plan: Codex Analysis MVP

## Goal

Phase 3 should make the Phase 2 Codex ingest useful for diagnosis before adding
another adapter.

The target vertical slice is:

```text
ingested Codex session -> deterministic feature extraction -> DuckDB derived rows -> analyze output -> JSON artifact
```

By the end of Phase 3, `session-doctor` should be able to analyze one ingested
Codex session, persist derived feature and classification rows, print a compact
terminal summary, and write a machine-readable JSON artifact by default.

## Current Starting Point

Phase 2 provides:

- Codex JSONL parsing into normalized records
- DuckDB persistence for parsed bundles
- delete-and-replace ingestion by `source_id`
- `session-doctor ingest --agent codex`
- `session-doctor sessions list`
- message provenance metadata for `response_item` versus `event_msg_fallback`
- synthetic parser, store, and CLI tests

The important missing pieces are:

- derived feature schemas
- derived classification schemas
- DuckDB tables and write APIs for analysis output
- deterministic feature extraction over one session
- `session-doctor analyze <session-id>`
- machine-readable analysis artifacts
- tests that prove repeated requests and failure signals are detectable from
  ingested Codex fixture data

## Resolved Implementation Decisions

Phase 3 should use these decisions:

- keep Phase 3 Codex-only
- implement `session-doctor analyze <session-id>` as the first user-facing
  diagnosis command
- persist derived rows by default
- rebuild derived rows for the analyzed `session_id` on every analyze run
- add DuckDB migration support for the derived analysis tables
- use an additive schema migration that preserves existing normalized ingest
  data
- keep the first classification layer small, deterministic, and explainable
- generate machine-readable JSON artifacts by default
- keep writing the default JSON artifact when `--format json` is used unless
  `--no-artifact` is passed
- print a terminal summary/table by default
- continue deferring privacy/redaction work
- avoid LLM calls, ML dependencies, embeddings, or network calls
- keep implementation split into small runnable commit points

## Scope

In scope:

- Codex sessions that have already been ingested into DuckDB
- deterministic message-level features
- deterministic session-level features
- a small deterministic session classification layer
- evidence references back to normalized IDs
- DuckDB persistence for derived rows
- default JSON artifact output
- terminal analysis summary
- synthetic fixtures and CLI/store tests

Out of scope:

- Pi or Claude Code parsing
- cross-agent analysis
- project-level trends
- Markdown reports
- graph projection
- privacy/redaction hardening
- semantic embeddings
- local ML models
- LLM/API calls
- Parquet export

## Analysis Model

Phase 3 should treat analysis output as derived data. Normalized source records
remain the source of truth.

Recommended new derived tables:

```text
message_features
session_features
session_classifications
analysis_runs
```

The first implementation should add these tables through an additive schema
migration. Existing Phase 2 databases should keep their normalized ingest data
when upgraded. Derived analysis rows can still be rebuilt for each session
without deleting raw events, messages, tool records, or session source rows.

`message_features` should store one row per detected message-level feature, not
one wide row with many nullable columns. This keeps the first schema flexible as
signals evolve.

Suggested fields:

```text
message_feature_id
analysis_run_id
session_id
message_id
source_event_id
feature_name
feature_value
score
evidence_json
metadata_json
```

`session_features` should store one row per session-level feature.

Suggested fields:

```text
session_feature_id
analysis_run_id
session_id
feature_name
feature_value
score
evidence_json
metadata_json
```

`session_classifications` should store one row per deterministic label.

Suggested fields:

```text
session_classification_id
analysis_run_id
session_id
label
score
confidence
evidence_event_ids_json
evidence_summary
metadata_json
```

`analysis_runs` should record when analysis was run and where the default JSON
artifact was written.

Suggested fields:

```text
analysis_run_id
session_id
started_at
completed_at
analyzer_version
artifact_path
metadata_json
```

Derived rows should be delete-and-replaced for the target `session_id` each time
`analyze` runs. The old rows should not accumulate unless a later phase adds
explicit historical analysis-run comparison.

## Feature Set

### Repeated Request Similarity

Repeated request detection should not start with exact text hashes. Exact hashes
are useful as a cheap supporting signal, but the first feature should detect
near-duplicate user intent even when the wording changes.

Use a deterministic text-similarity strategy with no external dependencies:

1. Extract user messages in timeline order.
2. Normalize each message:
   - lowercase
   - collapse whitespace
   - strip punctuation that does not affect meaning
   - drop very short tokens and a small built-in stopword set
   - keep code-ish tokens, paths, command names, and error names
3. Build request signatures:
   - normalized token set
   - token bigrams
   - optional character 4-grams for short messages
4. Compare each user message to earlier user messages in the same session.
5. Score similarity with a weighted deterministic overlap:
   - token Jaccard overlap
   - token-bigram Jaccard overlap
   - exact normalized-text match as a boost, not the primary detector
6. Mark `repeat_request_similarity` when the score crosses the
   fixture-calibrated threshold and store the prior message IDs as evidence.

The analyzer should emit enough evidence to show which earlier user message a
message repeated. This matters more than finding every fuzzy match perfectly.

Fixture calibration does not mean training a model or auto-tuning against a
large dataset. It means creating a small, explicit set of examples that define
the behavior expected from the deterministic scorer:

- positive repeated-request pairs that should score above the threshold
- negative pairs that should score below the threshold
- near-miss pairs that share topic vocabulary but are not the same request

Example positive pairs:

```text
Can you update the phase 3 plan with these decisions?
Please update the phase-3 document to reflect what we decided.

The warnings are too noisy, can we parse token_count properly?
I think token_count should become ModelUsage, not a warning.
```

Example negative pairs:

```text
Can you update the phase 3 plan with these decisions?
Can you run the full test suite?

The warnings are too noisy, can we parse token_count properly?
Please create a PR and merge it.
```

Example near-miss pair:

```text
Update the phase 3 plan with the migration decision.
Explain what an additive migration means.
```

The threshold should be selected only after the scorer produces visible score
margins for these fixture pairs. The tests should assert that the lowest
positive score remains above the chosen threshold and the highest negative or
near-miss score remains below it.

A healthy calibration result would look like:

```text
positive scores: 0.86, 0.79, 0.74
negative scores: 0.41, 0.38, 0.29
near-miss scores: 0.55, 0.50
chosen threshold: 0.70
```

An unhealthy result would look like:

```text
positive scores: 0.67, 0.62
negative scores: 0.59, 0.55
```

In that case, the implementation should improve the deterministic scorer or
emit lower-confidence evidence instead of pretending that a threshold solves the
ambiguity.

The first fixture set should stay small and reviewable:

```text
8-12 positive repeated-request pairs
8-12 negative pairs
4-6 near-miss pairs
```

Useful starting constants:

```text
repeat_request_similarity_threshold = 0.35
exact_normalized_text_boost = 0.10
minimum_comparable_token_count = 4
```

The first implementation selected `0.35` from the curated fixture score margins.
The lowest positive fixture score is above the threshold, the highest negative
or near-miss score is below it, and the tests keep a visible margin between the
two groups.

### Correction Markers

Detect user messages that correct or reject the assistant's current path.

Initial marker examples:

```text
no
not what i asked
that is not what i meant
we already tried
i meant
you misunderstood
why are you
stop doing
wrong
still broken
```

Store matched marker families, not just a boolean.

### Frustration Markers

Detect domain-specific frustration signals. This is intentionally not generic
sentiment analysis.

Initial marker examples:

```text
still broken
this is wrong
why
again
already tried
too many warnings
not good
be thorough
very important
```

### Scope Boundary Markers

Detect when the user constrains or redirects the agent.

Initial marker examples:

```text
don't
dont
do not
only
just
before you
not yet
no need to
keep it
defer
small commits
```

### Failed Command And Tool Ratio

Use `command_runs.exit_code` and `tool_results.is_error` where available.

Session-level features:

```text
command_count
failed_command_count
failed_command_ratio
tool_result_count
failed_tool_result_count
failed_tool_result_ratio
```

### Repeated Failure Detection

Detect repeated failures by stable output fingerprints:

- repeated non-null command `stdout_hash`
- repeated non-null command `stderr_hash`
- repeated non-null tool result `output_hash`
- repeated command text with failing exit code

The first implementation should not parse stack traces deeply. Store hash/count
evidence first, then expand later if needed.

### Same-File Edit Repetition

Use `file_activities.path` and count repeated edit operations against the same
path within a session.

Session-level features:

```text
edited_file_count
same_file_edited_repeatedly_count
max_edits_to_single_file
```

### Unresolved Ending Signal

This signal means the session appears to end while the task may still be
unresolved. It should be treated as weak evidence, not a definitive label.

Initial deterministic indicators should be evaluated over an ending window, not
only the final event. Use event order as the primary fallback because some
records may lack timestamps.

Recommended starting window:

```text
last max(5, 20%) normalized timeline events
cap at 20 events
also include events within the last 10 minutes when timestamps are reliable
```

Signals inside that window:

- the final user message is a correction, frustration marker, stop/pause marker,
  or repeated request
- a late user message is a correction, frustration marker, stop/pause marker,
  or repeated request
- a late command failed and there is no later assistant final answer
- the session ends without an assistant `final_answer`
- the session has parse warnings or failed commands near the end

This is useful because many stuck sessions do not have an explicit "failed"
event. The signal should be named and explained clearly so it does not imply the
tool knows the true outcome.

## Initial Classification Layer

Phase 3 should include a small deterministic classification layer, but the label
rules should stay transparent and easy to revise.

Recommended first labels:

```text
user_stuck
tooling_blocked
agent_looping
resolved_after_corrections
```

Do not add broad labels like `healthy`, `prompt_ambiguous`, or
`repo_complexity_high` yet unless the feature evidence is strong enough to make
them meaningful.

Example starting rules:

```text
user_stuck:
  repeat_request_count >= 2
  OR correction_count >= 2
  OR unresolved_ending_signal with correction/frustration evidence

tooling_blocked:
  failed_command_ratio >= 0.50
  OR repeated_failure_count >= 2

agent_looping:
  repeat_request_count >= 2
  AND same_file_edited_repeatedly_count >= 1
  OR repeated_failure_count >= 2 with repeated command text

resolved_after_corrections:
  correction_count >= 1
  AND final assistant message has phase final_answer
  AND no failed command after the last correction
```

Each classification row must include:

- score
- confidence
- evidence event IDs
- evidence summary
- rule metadata

Scores should be deterministic numbers between `0.0` and `1.0`. Confidence
should use the existing confidence vocabulary where possible.

## Default Artifact

`analyze` should write a JSON artifact by default. This gives agents and tests a
stable machine-readable surface from the beginning.

Suggested default location:

```text
<database-parent>/artifacts/<session-id>-analysis.json
```

For a default app-data database, this keeps artifacts beside the local DuckDB
store. For a temporary `--db /tmp/session-doctor-test.duckdb`, artifacts should
land under `/tmp/artifacts/`.

`analyze` should also support:

```bash
session-doctor analyze <session-id> --artifact <path>
session-doctor analyze <session-id> --no-artifact
session-doctor analyze <session-id> --format json
```

`--format json` should print JSON to stdout. The default terminal view should
remain human-readable. Output format should only change stdout presentation; it
should not disable artifact generation.

The artifact should include:

```text
session
summary_metrics
message_features
session_features
classifications
evidence
analysis_run
```

## Proposed CLI Shape

```bash
session-doctor analyze <session-id> --db <path>
session-doctor analyze <session-id> --db <path> --format json
session-doctor analyze <session-id> --db <path> --artifact <path>
session-doctor analyze <session-id> --db <path> --no-artifact
```

Default behavior:

- read normalized records from DuckDB
- rebuild derived rows for the session
- write the default JSON artifact
- print a terminal summary

The command should fail clearly when:

- the database does not exist
- the schema is too old or too new
- the session ID is not found
- no normalized records are available for the session
- the artifact path cannot be written

## Task Splits And Commit Points

### Commit 1: Design Doc And Phase 3 Plan

Deliverables:

- update `docs/session-doctor-design.md` so the current state reflects Phase 2
- add this `docs/phase-3-plan.md`
- update roadmap sequencing so Phase 3 is Codex Analysis MVP
- keep Phase 2 and future-adapter context intact

Validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Clean commit point:

```text
Phase 3 can be reviewed as a concrete implementation plan before code changes.
```

### Commit 2: Analysis Schemas And DuckDB Migration

Deliverables:

- schema models for message features, session features, session
  classifications, and analysis runs
- additive schema migration support for the new derived tables
- store methods to delete and insert derived rows for one `session_id`
- store read methods needed by analysis
- tests for migration and derived-row replacement

Validation:

```bash
uv run pytest tests/test_schemas.py tests/test_store.py -q
uv run ruff check src/session_doctor/schemas src/session_doctor/store tests
uv run ty check
```

Clean commit point:

```text
Derived analysis rows can be persisted and rebuilt without a CLI command.
```

### Commit 3: Deterministic Feature Extractor

Deliverables:

- `session_doctor/analysis/` package
- deterministic request-similarity helpers
- fixture-calibrated repeated-request threshold tests with positive, negative,
  and near-miss pairs
- message marker detection helpers
- command/tool/file feature extraction
- evidence model helpers
- tests covering repeated request similarity, corrections, frustration, scope
  boundaries, failed commands, repeated failures, same-file edit repetition, and
  unresolved ending signal

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check
```

Clean commit point:

```text
Feature extraction works from in-memory normalized records without touching the CLI.
```

### Commit 4: Classification Rules

Deliverables:

- deterministic rules for the first label set
- score and confidence calculation helpers
- evidence summaries for each emitted label
- tests for `user_stuck`, `tooling_blocked`, `agent_looping`, and
  `resolved_after_corrections`

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check
```

Clean commit point:

```text
Feature rows can be converted into a small explainable label set.
```

### Commit 5: `session-doctor analyze`

Deliverables:

- replace the `analyze` placeholder
- read one ingested session from DuckDB
- rebuild derived rows for that session
- write the default JSON artifact
- support `--format json`, `--artifact`, and `--no-artifact`
- print a terminal summary with labels, scores, feature counts, and artifact
  path
- CLI tests from fixture ingest through analyze

Validation:

```bash
rm -f /tmp/session-doctor-phase3.duckdb
uv run session-doctor ingest --agent codex \
  --source tests/fixtures/codex/basic-session.jsonl \
  --db /tmp/session-doctor-phase3.duckdb
uv run session-doctor analyze <fixture-session-id> \
  --db /tmp/session-doctor-phase3.duckdb
uv run pytest tests/test_cli.py tests/test_analysis.py tests/test_store.py -q
uv run ruff check src tests
uv run ty check
```

Clean commit point:

```text
An ingested Codex session can be analyzed through the CLI and emits stored derived rows plus JSON.
```

### Commit 6: Fixtures, Docs, And Smoke Test

Deliverables:

- richer Codex fixture for repeated/corrected/failing-session behavior
- README usage for `analyze`
- manual smoke-test notes using a copied local Codex session
- final full quality gate

Suggested manual smoke test:

```bash
cp ~/.codex/sessions/YYYY/MM/DD/<session>.jsonl /tmp/session-doctor-codex-phase3.jsonl
rm -f /tmp/session-doctor-phase3.duckdb
uv run session-doctor ingest --agent codex \
  --source /tmp/session-doctor-codex-phase3.jsonl \
  --db /tmp/session-doctor-phase3.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase3.duckdb
uv run session-doctor analyze <session-id> --db /tmp/session-doctor-phase3.duckdb
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
Phase 3 behavior is documented and validated end to end.
```

## Recommended Implementation Order

1. Update docs and agree on the analysis shape.
2. Add the derived schema and store layer.
3. Build deterministic feature extraction against in-memory records.
4. Add the small classification layer.
5. Wire `analyze` through the CLI.
6. Add richer fixtures, README updates, and smoke-test documentation.

This keeps storage, feature logic, classification, and CLI behavior separately
reviewable.

## Acceptance Criteria

Phase 3 is complete when:

- `session-doctor analyze <session-id> --db <tmpdb>` runs against an ingested
  Codex fixture
- analysis persists derived rows and replaces old rows on repeat runs
- analysis writes a JSON artifact by default
- `--format json` prints machine-readable output
- terminal output shows classifications, scores, feature counts, and evidence
  summaries
- repeated request detection uses deterministic similarity, not only exact text
  hashes
- repeated request tests include positive, negative, and near-miss fixture pairs
  with visible score margins around the chosen threshold
- correction, frustration, scope-boundary, failed-command, repeated-failure,
  same-file-edit, and unresolved-ending signals are covered by tests
- classification labels remain small and explainable
- tests do not depend on live files under `~/.codex`
- no privacy/redaction system, LLM call, ML dependency, graph projection, or
  second adapter is introduced in Phase 3
- the full quality gate passes

## Open Questions For Implementation Review

There are no known product decisions blocking Phase 3 implementation.
