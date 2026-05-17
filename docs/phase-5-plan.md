# Phase 5 Plan: Deterministic Feature Hardening

Status: planned.

## Goal

Phase 5 should make the existing Codex and Pi analysis more reliable before the
tool adds aggregate summaries, project trends, reports, or graph views.

The target vertical slice is:

```text
ingested Codex/Pi session -> corrected timeline ordering -> stronger deterministic features -> richer evidence -> analyze output/artifact
```

By the end of Phase 5, `session-doctor analyze <session-id>` should produce
better evidence for why a session is classified as stuck, tooling-blocked,
looping, or resolved. The phase should focus on correctness and signal quality,
not on new user-facing surfaces.

## Current Starting Point

Phases 1 through 4 provide:

- Typer CLI and app entry point
- adapter discovery for Codex, Claude Code, and Pi
- Codex and Pi JSONL parsing into normalized records
- DuckDB persistence for parsed bundles
- delete-and-replace ingestion by `source_id`
- `session-doctor ingest --agent codex`
- `session-doctor ingest --agent pi`
- `session-doctor sessions list`
- `session-doctor analyze <session-id>`
- deterministic feature extraction and classification over normalized records
- persisted analysis rows and default JSON analysis artifacts
- graph placeholder schemas/tables without graph projection
- tests for Codex/Pi adapters, store behavior, CLI ingest/list/analyze, and
  deterministic analysis

The important missing pieces are:

- hardened analysis ordering when timestamps are missing or ambiguous
- improved false-positive handling for marker features
- stronger repeated-failure evidence for future summaries/reports/graphs
- clearer distinction between repeated tool failures and repeated command loops
- more precise unresolved-ending evidence
- richer feature evidence payloads with source event IDs wherever possible
- focused regression tests for known Phase 5 analysis edge cases

## Resolved Implementation Decisions

Phase 5 should use these decisions:

- keep Phase 5 analysis-only for already ingested Codex and Pi sessions
- do not add Claude Code parsing in this phase
- do not add aggregate summary, trend, report, or graph commands in this phase
- keep analysis local-only, deterministic, and explainable
- avoid LLM calls, embeddings, ML dependencies, or network calls
- preserve the current normalized storage model unless a small additive change
  is clearly required for analysis correctness
- prefer richer evidence payloads over more labels
- keep the current label set unless a correctness fix requires narrowing when a
  label is emitted
- keep output backward-compatible where practical: existing JSON artifacts may
  gain evidence fields, but should not lose the current top-level shape
- split implementation into small reviewable commits with full validation at
  the end
- include timestamp normalization at DuckDB write time if tests confirm
  timezone/local-time shifts affect persisted timelines
- treat both repeated failing command text and repeated stdout/stderr from
  commands as sufficient repeated-command-loop evidence for `agent_looping`
- do not treat short sessions with no final answer as unresolved solely because
  the final answer is missing; require additional late correction, failure, or
  warning evidence
- keep documentation updates limited to design/phase docs unless output changes
  materially

## Scope

In scope:

- Codex and Pi sessions already ingested into DuckDB
- deterministic analysis ordering fixes
- marker detection false-positive fixes
- repeated-request and marker evidence hardening
- failed-command, failed-tool-result, and repeated-failure evidence hardening
- unresolved-ending signal correctness
- same-file edit loop evidence improvements
- regression fixtures and tests for each fixed edge case
- analysis artifact updates when needed to expose richer evidence
- design-doc and phase-plan updates

Out of scope:

- Claude Code parsing
- new adapters
- aggregate summary commands
- project-level trends
- Markdown reports
- graph projection
- export commands
- privacy/redaction hardening beyond avoiding new raw-output storage
- semantic embeddings
- local ML models
- LLM/API calls
- broad schema redesign
- historical analysis-run comparison

## Known Issues To Address

The following issues are strong Phase 5 candidates because they affect current
analysis correctness or evidence quality:

- duplicate marker-family matches can generate duplicate `message_feature_id`
  values for one message
- bare rejection markers can misclassify scope-boundary requests as corrections
- loaded sessions can process messages in hash order when timestamps are
  missing, instead of raw-event order
- unresolved-ending evidence should not count failures or corrections that were
  followed by a later final answer
- the configured timestamp ending window should be unioned with the record-index
  ending window when timestamps are available
- repeated-failure evidence should carry source event IDs consistently
- `agent_looping` should distinguish repeated command loops from repeated
  non-command tool-output failures

- command text/path privacy improvements if a Phase 5 evidence change would
  otherwise expose new raw sensitive content

## Feature Hardening Details

### Timeline And Ordering

Analysis should operate in normalized timeline order. When records are reloaded
from DuckDB, messages, tool records, command runs, file activities, usage rows,
and warnings should preserve the source `raw_events.record_index` order whenever
that provenance is available.

Timestamp ordering can remain useful as metadata, but it should not reorder
events that came from a deterministic JSONL record index.

### Marker Features

Marker detection should avoid emitting duplicate primary keys when multiple
strings map to the same marker family in one message. Evidence should preserve
all matched strings while storing only one feature row per:

```text
message_id + feature_name + marker_family
```

Marker detection should also avoid treating ordinary scope-boundary phrases as
strong correction evidence. For example, a phrase like `no need to do code
changes yet` should primarily be a scope boundary, not a user-stuck correction.

### Repeated Requests

Repeated-request detection should remain deterministic and dependency-free. This
phase should improve tests and evidence more than it changes the scoring model.

Useful evidence should include:

- matched prior message ID
- matched prior source event ID
- similarity score
- threshold
- enough metadata to explain why the match was accepted

Threshold changes should be made only with explicit fixture calibration.

### Failed Commands And Tool Results

Failure features should preserve the distinction between:

- a failed command with an exit code
- a failed tool result with structured failure metadata
- repeated output hashes
- repeated failing command text

This distinction matters because `tooling_blocked` can come from repeated tool
failures, while `agent_looping` should require stronger evidence that the agent
is retrying the same command or approach.

Repeated-failure group evidence should include source event IDs in addition to
command/tool result IDs.

### Same-File Edit Loops

Same-file edit detection should remain path-based for now, but evidence should
make the repeated paths and source event IDs easy for future summary/report
commands to use.

This phase should not attempt semantic patch-region analysis unless it becomes a
small isolated helper with clear tests.

### Unresolved Ending

The unresolved-ending signal should be conservative. It should consider both:

- the final record-index window
- the final timestamp window

Late failures, corrections, or parse warnings should not count as unresolved if
a later assistant final answer exists after that evidence. A missing final
answer should not be enough evidence by itself, especially for short planning or
read-only sessions. Missing final answer can contribute only when combined with
additional late correction, failure, or warning evidence.

## Privacy And Storage Defaults

Phase 5 should not expand raw sensitive storage. It can add hashes, lengths,
counts, IDs, booleans, and metadata needed to explain deterministic features.

If a feature needs to expose command text or paths in evidence, prefer using
existing locally stored normalized fields. Do not add raw tool output, raw patch
content, raw write content, or full old/new edit strings to artifacts.

## Proposed CLI Shape

No new CLI commands are planned for Phase 5.

The existing command remains the user-facing validation path:

```bash
session-doctor analyze <session-id> --db <path>
session-doctor analyze <session-id> --db <path> --format json
```

JSON artifacts may gain richer evidence fields inside existing
`message_features`, `session_features`, and `classifications` arrays.

## Task Splits And Commit Points

### Commit 1: Phase 5 Plan And Roadmap Docs

Deliverables:

- add this `docs/phase-5-plan.md`
- update `docs/session-doctor-design.md` to link to the Phase 5 plan
- keep aggregate summary/trend/report/graph sequencing intact
- keep Phase 5 marked planned, not complete

Likely files:

- `docs/phase-5-plan.md`
- `docs/session-doctor-design.md`

Implementation sketch:

```text
1. Add this plan as the implementation contract for Phase 5.
2. Keep `docs/session-doctor-design.md` current-state and phase-order sections
   aligned with this plan.
3. Do not touch runtime code in this commit.
```

Expected tests:

- full existing suite remains green because this commit is docs-only

Validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check .
uv run pytest -q
```

Clean commit point:

```text
Phase 5 can be reviewed as a concrete analysis-hardening plan before code changes.
```

### Commit 2: Timeline Reload And Ending Window Correctness

Deliverables:

- make DuckDB bundle reload preserve raw-event order for analysis-relevant rows
- normalize offset-aware timestamps at DuckDB write time if regression tests
  confirm local-time shifts in persisted timelines
- union timestamp and record-index ending windows
- ensure unresolved-ending evidence compares each late signal against later final
  answers, not only the session's max final-answer index
- tests for sessions with missing timestamps, mixed timestamps, late failures,
  and later final answers

Likely files:

- `src/session_doctor/store/duckdb.py`
- `src/session_doctor/analysis/features.py`
- `tests/test_store.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
# src/session_doctor/store/duckdb.py
def utc_naive(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value

# Use utc_naive(...) when inserting timestamp-like fields.
```

```sql
-- src/session_doctor/store/duckdb.py
-- When loading child records, prefer source raw-event order over timestamps.
SELECT messages.*
FROM messages
LEFT JOIN raw_events ON raw_events.event_id = messages.source_event_id
WHERE messages.session_id = ?
ORDER BY raw_events.record_index NULLS LAST, messages.timestamp NULLS LAST, messages.message_id
```

```python
# src/session_doctor/analysis/features.py
def ending_source_event_ids(bundle):
    return record_index_window_event_ids(bundle) | timestamp_window_source_event_ids(bundle)

def has_later_final_answer(event_id):
    event_index = event_indexes.get(event_id)
    return any(index > event_index for index in final_answer_indexes)

late_failed_command_ids = [
    command.command_run_id
    for command in bundle.command_runs
    if command.source_event_id in late_event_ids
    and command.exit_code not in (None, 0)
    and not has_later_final_answer(command.source_event_id)
]
```

Expected tests:

- a store round-trip where two timestamp-less messages reload in raw record
  order, not `message_id` order
- an offset-aware timestamp inserted into DuckDB reloads as the same UTC instant
  without local-time shifting
- a failed command in the ending window followed by a final answer does not
  produce `unresolved_ending_signal`
- a short session with no final answer and no late failure/correction/warning
  does not produce `unresolved_ending_signal`

Validation:

```bash
uv run pytest tests/test_analysis.py tests/test_store.py -q
uv run ruff check src/session_doctor/analysis src/session_doctor/store tests
uv run ty check .
```

Clean commit point:

```text
Analysis uses stable source ordering and conservative ending-window evidence.
```

### Commit 3: Marker Feature Deduplication And False-Positive Reduction

Deliverables:

- dedupe marker features by marker family per message
- preserve all matched marker strings in evidence
- reduce or remove bare rejection markers that collide with scope-boundary
  language
- tests for duplicate marker families and scope-boundary phrases such as
  `no need to` or `not yet`

Likely files:

- `src/session_doctor/analysis/features.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
# src/session_doctor/analysis/features.py
def marker_features(messages, analysis_run_id):
    for message in user_messages:
        for feature_name, markers in marker_groups:
            matched_by_family: dict[str, list[str]] = defaultdict(list)
            for marker, family in markers.items():
                if marker_matches(text, marker):
                    matched_by_family[family].append(marker)

            for family, matched_markers in matched_by_family.items():
                yield message_feature(
                    feature_name=feature_name,
                    feature_value=family,
                    evidence={"matched_markers": sorted(matched_markers)},
                )
```

```python
# Prefer removing over special-casing if a marker is too broad.
CORRECTION_MARKERS = {
    # no bare "no"
    "not what i asked": "not_what_i_asked",
    "that is not what i meant": "not_what_i_meant",
    ...
}
```

Expected tests:

- one message containing `be thorough` and `very important` emits one
  `frustration_marker=high_stakes` feature with both matched strings in evidence
- `no need to change code yet` emits a scope-boundary marker but no correction
  marker
- duplicate marker-family matches can be persisted without duplicate primary
  key failures

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check .
```

Clean commit point:

```text
Marker features are unique, explainable, and less likely to over-classify planning requests.
```

### Commit 4: Failure Evidence And Loop Classification Narrowing

Deliverables:

- include source event IDs in repeated-failure evidence groups
- preserve whether each repeated-failure group came from command text,
  stdout/stderr hashes, or tool-output hashes
- narrow `agent_looping` repeated-failure classification to repeated command
  loop evidence, including repeated failing command text or repeated
  stdout/stderr from command runs, rather than any repeated non-command tool
  failure
- tests for repeated command failures, repeated tool-output failures, and mixed
  evidence sessions

Likely files:

- `src/session_doctor/analysis/features.py`
- `src/session_doctor/analysis/classification.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
# src/session_doctor/analysis/features.py
def repeated_failure_groups(bundle):
    add_group(
        key=f"failed_command:{command.command}",
        group_type="failed_command_text",
        record_id=command.command_run_id,
        source_event_id=command.source_event_id,
    )
    add_group(
        key=f"stderr_hash:{command.stderr_hash}",
        group_type="command_stderr_hash",
        record_id=command.command_run_id,
        source_event_id=command.source_event_id,
    )
    add_group(
        key=f"tool_output_hash:{result.output_hash}",
        group_type="tool_output_hash",
        record_id=result.tool_result_id,
        source_event_id=result.source_event_id,
    )

    return [{
        "key": key,
        "group_type": group_type,
        "record_ids": sorted(record_ids),
        "source_event_ids": sorted(source_event_ids),
        "repeat_count": len(record_ids) - 1,
    }]

def repeated_command_loop_groups(groups):
    return [
        group for group in groups
        if group["group_type"] in {
            "failed_command_text",
            "command_stdout_hash",
            "command_stderr_hash",
        }
    ]
```

```python
# src/session_doctor/analysis/classification.py
if repeated_command_loop_count >= 2 or (
    repeat_request_count >= 2 and same_file_repeated_count >= 1
):
    emit_agent_looping()
```

Expected tests:

- repeated failing command text emits `agent_looping`
- repeated command stdout/stderr failure hashes can emit `agent_looping`
- repeated non-command tool-output hashes can emit `tooling_blocked` but do not
  emit `agent_looping` by themselves
- `session_classifications.evidence_event_ids` is non-empty when evidence
  records have source events

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check .
```

Clean commit point:

```text
Failure classifications carry event evidence and separate tooling blockers from command loops.
```

### Commit 5: Edit-Loop And Repeated-Request Evidence Hardening

Deliverables:

- enrich same-file repeated edit evidence with source event IDs where available
- verify repeated-request evidence includes prior message/source event IDs and
  threshold metadata
- add fixture-calibrated positive, negative, and near-miss repeated-request
  cases if coverage is missing
- avoid threshold changes unless fixtures show an explicit need

Likely files:

- `src/session_doctor/analysis/features.py`
- `tests/test_analysis.py`
- possibly `tests/fixtures/codex/repeated-failure-session.jsonl`
- possibly `tests/fixtures/pi/repeated-failure-session.jsonl`

Implementation sketch:

```python
# src/session_doctor/analysis/features.py
def repeated_file_edit_evidence(bundle):
    events_by_path: dict[str, list[str]] = defaultdict(list)
    counts_by_path = Counter()
    for activity in mutating_file_activities(bundle):
        counts_by_path[activity.path] += 1
        if activity.source_event_id:
            events_by_path[activity.path].append(activity.source_event_id)

    return {
        "paths": {path: count for path, count in counts_by_path.items() if count > 1},
        "source_event_ids_by_path": {
            path: sorted(events_by_path[path])
            for path, count in counts_by_path.items()
            if count > 1
        },
        "source_event_ids": sorted(set(chain.from_iterable(events_by_path.values()))),
    }
```

```python
# Repeated-request evidence should already look roughly like this.
evidence={
    "matched_message_id": matched_message.message_id,
    "matched_source_event_id": matched_message.source_event_id,
    "threshold": REPEAT_REQUEST_SIMILARITY_THRESHOLD,
}
```

Expected tests:

- same-file repeated edit evidence includes repeated path, per-path source
  events, and flattened source event IDs
- repeated-request positive/negative/near-miss examples keep the threshold from
  drifting accidentally
- existing Codex and Pi repeated-failure fixtures still classify as expected

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check .
```

Clean commit point:

```text
Edit-loop and repeated-request features expose evidence usable by future summaries.
```

### Commit 6: Analyze Artifact And CLI Regression Coverage

Deliverables:

- ensure `analyze --format json` exposes the hardened evidence fields
- ensure default artifact writing still works
- CLI tests for Codex and Pi fixture sessions after the feature hardening
- no new command surface

Likely files:

- `src/session_doctor/cli.py`
- `tests/test_cli.py`
- `tests/test_analysis.py`
- `tests/test_store.py`

Implementation sketch:

```python
# src/session_doctor/cli.py
def analysis_payload(...):
    return {
        "session": session.model_dump(mode="json"),
        "analysis_run": analysis_run.model_dump(mode="json"),
        "summary_metrics": {...},
        "message_features": [feature.model_dump(mode="json") for feature in message_features],
        "session_features": [feature.model_dump(mode="json") for feature in session_features],
        "classifications": [classification.model_dump(mode="json") for classification in classifications],
    }
```

Most of this function should stay structurally unchanged. The goal is to verify
the richer `evidence` and `evidence_event_ids` produced by earlier commits flow
through unchanged to both `--format json` and the artifact file.

Expected tests:

- ingest and analyze Codex fixture; assert JSON output contains hardened
  evidence fields
- ingest and analyze Pi fixture; assert artifact file contains hardened evidence
  fields
- `report`, `graph`, `summary`, and `trends` are not introduced in Phase 5

Validation:

```bash
uv run pytest tests/test_cli.py tests/test_analysis.py tests/test_store.py -q
uv run ruff check src tests
uv run ty check .
```

Clean commit point:

```text
The existing analyze command exposes the hardened feature/classification evidence end to end.
```

### Commit 7: Docs And Manual Smoke Test

Deliverables:

- update design or phase docs only if output examples change materially
- add manual smoke-test notes for copied Codex and Pi sessions
- final full quality gate

Likely files:

- `docs/phase-5-plan.md`
- `docs/session-doctor-design.md`
- optionally `README.md` only if user-visible output changes materially

Implementation sketch:

```text
If code changes only enrich existing JSON evidence fields, prefer leaving README
unchanged. If terminal output changes or new artifact fields are important for
users, add a short docs note with one compact example.
```

Suggested manual smoke test:

```bash
rm -f /tmp/session-doctor-phase5.duckdb
uv run session-doctor ingest --agent pi \
  --source tests/fixtures/pi/repeated-failure-session.jsonl \
  --db /tmp/session-doctor-phase5.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase5.duckdb
uv run session-doctor analyze <session-id> \
  --db /tmp/session-doctor-phase5.duckdb \
  --format json
```

Final validation:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check .
uv run pytest -q
```

Clean commit point:

```text
Phase 5 analysis behavior is documented and validated end to end.
```

## Recommended Implementation Order

1. Lock the plan and roadmap docs.
2. Fix ordering and unresolved-ending correctness before adding or changing
   feature evidence.
3. Fix marker false positives before recalibrating classifications.
4. Harden repeated-failure evidence and narrow loop classification.
5. Enrich edit-loop and repeated-request evidence for future summaries.
6. Verify artifacts and CLI behavior remain stable.

## Open Questions Before Implementation

None. The implementation should still ask for steering if new trade-offs appear
while tests or real-session smoke checks reveal behavior not covered by this
plan.
