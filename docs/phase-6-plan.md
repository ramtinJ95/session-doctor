# Phase 6 Plan: Classification Scoring

Status: complete.

Implemented scope:

- Added reusable `SessionFeature` score rows for `friction_score`,
  `stuckness_score`, `prompt_clarity_risk`, `agent_fit_risk`, and
  `project_complexity_signal`.
- Added score formula metadata with component values, weights, contributions,
  and contributing feature/source-event evidence.
- Added narrow ambiguity and stop/pause marker features plus session counts used
  by Phase 6 labels.
- Preserved existing labels while adding metadata-rich v2 rules and more
  specific evidence summaries.
- Added conservative labels for `healthy`, `agent_misunderstood`,
  `prompt_ambiguous`, `task_too_large`, `repo_complexity_high`, and
  `abandoned_or_stopped` with regression tests for important boundaries.
- Kept the existing `analyze` command and JSON artifact top-level shape while
  exposing the new score features through `summary_metrics` and
  `session_features`.
- Kept Phase 6 local-only and deterministic, without schema migrations, LLM
  calls, embeddings, ML dependencies, aggregate commands, reports, or graph
  projection.

## Goal

Phase 6 should turn the current small deterministic classification layer into a
more useful, calibrated scoring layer for single-session diagnosis before the
tool adds aggregate summaries, project trends, reports, or graph projection.

The target vertical slice is:

```text
ingested Codex/Pi session -> existing deterministic features -> risk score features -> richer classifications -> analyze output/artifact
```

By the end of Phase 6, `session-doctor analyze <session-id>` should expose
clearer scores for friction, stuckness, prompt clarity risk, agent fit risk, and
project complexity signal. Session classifications should remain deterministic
and explainable, but should be easier for the future `summary` and `trends`
commands to rank and aggregate.

## Starting Point Before Implementation

Phases 1 through 5 provide:

- Typer CLI and app entry point
- adapter discovery for Codex, Claude Code, and Pi
- Codex and Pi JSONL parsing into normalized records
- DuckDB persistence for parsed bundles
- delete-and-replace ingestion by `source_id`
- `session-doctor ingest --agent codex`
- `session-doctor ingest --agent pi`
- `session-doctor sessions list`
- `session-doctor analyze <session-id>`
- deterministic message and session features over normalized Codex and Pi
  records
- persisted `analysis_runs`, `message_features`, `session_features`, and
  `session_classifications`
- default JSON analysis artifacts and `analyze --format json`
- hardened timeline ordering, marker evidence, repeated-failure evidence, edit
  evidence, and unresolved-ending evidence
- current classification labels:
  - `user_stuck`
  - `tooling_blocked`
  - `agent_looping`
  - `resolved_after_corrections`
- graph placeholder schemas/tables without graph projection
- tests for Codex/Pi adapters, store behavior, CLI ingest/list/analyze, and
  deterministic analysis

The important missing pieces at the start of Phase 6 were:

- reusable score features such as `friction_score`, `stuckness_score`,
  `prompt_clarity_risk`, `agent_fit_risk`, and `project_complexity_signal`
- calibrated score thresholds with explicit fixtures for low, medium, and high
  risk sessions
- richer session labels from the design doc, emitted conservatively
- less generic evidence summaries for existing labels
- label metadata that explains which score/threshold triggered a label
- a clean contract for future aggregate summary queries to rank sessions by
  risk without reimplementing classification logic

## Resolved Implementation Decisions

Phase 6 should use these decisions:

- keep Phase 6 single-session analysis only
- keep Phase 6 analysis over already ingested Codex and Pi sessions
- do not add Claude Code parsing in this phase
- do not add aggregate summary, trend, report, graph, export, or explain
  commands in this phase
- keep analysis local-only, deterministic, and explainable
- avoid LLM calls, embeddings, ML dependencies, network calls, and optional
  model-assisted classification
- do not add a DuckDB schema migration unless implementation proves an existing
  table cannot express the required derived data
- store reusable scores as `SessionFeature` rows, not as new columns or a new
  wide score table
- keep `SessionClassification` as the only session-label storage model
- keep the existing artifact top-level shape backward-compatible
- keep existing labels and refine their scoring/evidence rather than removing
  or renaming them
- add new labels only when the current deterministic feature set can support
  them with clear evidence
- do not add message-level classification tables in Phase 6; message-level
  markers remain `MessageFeature` evidence
- use bounded deterministic score formulas that produce values in `[0.0, 1.0]`
- put formula version, threshold, and contributing feature names in metadata
- keep confidence separate from score:
  - score represents severity/risk
  - confidence represents how directly the evidence supports the label
- make `healthy` conservative and mutually exclusive with negative labels
- let positive labels such as `resolved_after_corrections` coexist with earlier
  negative evidence when the session genuinely recovered
- keep terminal output compact, but show the new risk scores because they are
  user-facing analysis results
- split implementation into small reviewable commits with full validation at
  the end

## Scope

In scope:

- deterministic risk score features
- classification formula refactor for readability and reuse
- calibrated threshold constants with tests
- richer evidence summaries and metadata for existing labels
- conservative new labels supported by current evidence
- JSON artifact and terminal output updates for score features
- regression tests for false positives and known label boundaries
- copied-real-session smoke tests for Codex and Pi
- design-doc, phase-plan, and README reference updates

Out of scope:

- Claude Code parsing
- new adapters
- aggregate summary commands
- project-level trend commands
- Markdown reports
- graph projection
- export commands
- MCP or skill wrappers
- privacy/redaction hardening beyond avoiding new raw sensitive content
- semantic embeddings
- local ML models
- LLM/API calls
- historical analysis-run comparison
- broad schema redesign

## Known Issues To Address

The following issues are strong Phase 6 candidates because they affect
interpretability, future aggregation, or scoring correctness:

- current labels contain useful evidence, but there are no reusable risk scores
  for future summary/trend ranking
- current score formulas are embedded directly in label branches, making it hard
  to compare label severity consistently
- confidence values are currently fixed per rule and are not tied to evidence
  directness
- `user_stuck` can mix repeated requests, corrections, frustration, and
  unresolved endings without explaining which factor dominated
- `tooling_blocked` uses failed command ratio and repeated failures, but does
  not distinguish severity from confidence in metadata
- `agent_looping` has stronger Phase 5 evidence, but its score should make the
  repeated-command versus repeated-request/edit path visible
- there is no `healthy` label for clean analyzed sessions, making future
  aggregate summaries harder to interpret
- design-doc labels such as `agent_misunderstood`, `prompt_ambiguous`,
  `task_too_large`, `repo_complexity_high`, and `abandoned_or_stopped` are not
  implemented yet
- current terminal output does not display cross-label risk scores because they
  do not exist yet

## Scoring Model

Phase 6 should introduce deterministic score features as regular
`SessionFeature` rows:

```text
friction_score
stuckness_score
prompt_clarity_risk
agent_fit_risk
project_complexity_signal
```

Each score should store:

- `feature_value`: score formatted as a stable decimal string
- `score`: same numeric score as a float
- `evidence`: contributing feature names and source event IDs when available
- `metadata`: formula version, thresholds, and normalized component values

### Friction Score

`friction_score` should represent how rough the session felt operationally.

Primary inputs:

- `frustration_count`
- `correction_count`
- `failed_command_ratio`
- `failed_tool_result_ratio`
- `repeated_failure_count`
- `unresolved_ending_signal`

Implementation sketch:

```python
friction_score = clamp01(
    0.18 * capped_count(frustration_count, cap=3)
    + 0.14 * capped_count(correction_count, cap=3)
    + 0.22 * failed_command_ratio
    + 0.18 * failed_tool_result_ratio
    + 0.14 * capped_count(repeated_failure_count, cap=3)
    + (0.18 if unresolved_ending_signal else 0.0)
)
```

### Stuckness Score

`stuckness_score` should represent whether the session appears unable to make
progress toward the user's goal.

Primary inputs:

- `repeat_request_count`
- `correction_count`
- `frustration_count`
- `repeated_command_failure_count`
- `same_file_edited_repeatedly_count`
- `unresolved_ending_signal`

Implementation sketch:

```python
stuckness_score = clamp01(
    0.22 * capped_count(repeat_request_count, cap=3)
    + 0.20 * capped_count(correction_count, cap=3)
    + 0.12 * capped_count(frustration_count, cap=3)
    + 0.20 * capped_count(repeated_command_failure_count, cap=3)
    + 0.10 * capped_count(same_file_edited_repeatedly_count, cap=3)
    + (0.20 if unresolved_ending_signal else 0.0)
)
```

### Prompt Clarity Risk

`prompt_clarity_risk` should represent ambiguity, scope churn, or instructions
that required the user to restate boundaries.

Primary inputs:

- `scope_boundary_count`
- `correction_count`
- `repeat_request_count`
- optional Phase 6 `ambiguity_marker` feature, if added

Implementation sketch:

```python
prompt_clarity_risk = clamp01(
    0.22 * capped_count(scope_boundary_count, cap=4)
    + 0.24 * capped_count(correction_count, cap=3)
    + 0.20 * capped_count(repeat_request_count, cap=3)
    + 0.18 * capped_count(ambiguity_count, cap=3)
)
```

Phase 6 may add a small deterministic `ambiguity_marker` message feature if
fixture calibration shows it is needed for `prompt_ambiguous`. Keep the marker
set narrow, for example:

```text
not sure
unclear
ambiguous
which one
what do you mean
can you clarify
```

### Agent Fit Risk

`agent_fit_risk` should represent whether the current agent/tooling combination
appears poorly matched to the task.

Primary inputs:

- `failed_command_ratio`
- `failed_tool_result_ratio`
- `repeated_command_failure_count`
- `agent_looping` evidence path
- `task_too_large` evidence path
- `unresolved_ending_signal`

Implementation sketch:

```python
agent_fit_risk = clamp01(
    0.20 * failed_command_ratio
    + 0.18 * failed_tool_result_ratio
    + 0.24 * capped_count(repeated_command_failure_count, cap=3)
    + 0.12 * capped_count(same_file_edited_repeatedly_count, cap=3)
    + 0.10 * capped_count(edited_file_count, cap=8)
    + (0.18 if unresolved_ending_signal else 0.0)
)
```

### Project Complexity Signal

`project_complexity_signal` should be a weak signal that the session involved a
large or complex local task. It is not a judgment about the whole repository.

Primary inputs:

- `edited_file_count`
- `same_file_edited_repeatedly_count`
- `max_edits_to_single_file`
- `command_count`
- `tool_result_count`
- optional model usage totals, if easy to aggregate without changing storage

Implementation sketch:

```python
project_complexity_signal = clamp01(
    0.22 * capped_count(edited_file_count, cap=8)
    + 0.18 * capped_count(same_file_edited_repeatedly_count, cap=4)
    + 0.16 * capped_count(max_edits_to_single_file, cap=6)
    + 0.16 * capped_count(command_count, cap=12)
    + 0.12 * capped_count(tool_result_count, cap=20)
)
```

Use conservative wording in summaries. A high value should mean "this session
looks complex", not "the project is objectively complex."

## Classification Labels

Phase 6 should preserve existing labels and add new labels only where the
current deterministic evidence is strong enough.

### Existing Labels To Keep

Keep these labels:

```text
user_stuck
tooling_blocked
agent_looping
resolved_after_corrections
```

Refine them so each classification stores metadata like:

```json
{
  "rule": "user_stuck_v2",
  "score_feature": "stuckness_score",
  "threshold": 0.55,
  "contributing_features": ["repeat_request_count", "correction_count"]
}
```

### New Labels For Phase 6

Implement these labels if tests confirm the thresholds are conservative:

```text
healthy
agent_misunderstood
prompt_ambiguous
task_too_large
repo_complexity_high
abandoned_or_stopped
```

Do not implement these labels if their evidence cannot be made specific with
the existing feature set and small Phase 6 feature additions.

#### healthy

Emit `healthy` only when:

- no negative label is emitted
- `friction_score < 0.25`
- `stuckness_score < 0.25`
- `agent_fit_risk < 0.25`
- `unresolved_ending_signal` is false
- there is at least one user or assistant message

`healthy` should be mutually exclusive with negative labels. It can coexist
with no other labels.

#### agent_misunderstood

Emit `agent_misunderstood` when explicit correction evidence indicates the
assistant misunderstood the request or scope.

Candidate triggers:

- `correction_count >= 1` and `prompt_clarity_risk >= 0.35`
- correction marker families such as `not_what_i_asked`, `not_what_i_meant`,
  `misunderstood`, or `unexpected_action`

This label may coexist with `resolved_after_corrections` if the session
recovered.

#### prompt_ambiguous

Emit `prompt_ambiguous` when the evidence points to ambiguity or scope
boundaries rather than tooling failure.

Candidate triggers:

- `prompt_clarity_risk >= 0.55`
- `scope_boundary_count >= 2`
- `ambiguity_marker` evidence, if implemented

Avoid emitting this label solely because a user gave careful instructions. A
single scope-boundary marker such as `only` or `just` should not be enough.

#### task_too_large

Emit `task_too_large` when the session shows broad task surface area combined
with friction or unresolved evidence.

Candidate triggers:

- `project_complexity_signal >= 0.65` and `friction_score >= 0.35`
- `edited_file_count >= 6` and `command_count >= 8`
- high tool/result volume combined with `unresolved_ending_signal`

Do not emit this label for a large but successful session with low friction.

#### repo_complexity_high

Emit `repo_complexity_high` only as a weak, evidence-backed session label.

Candidate triggers:

- `project_complexity_signal >= 0.75`
- multiple files edited and repeated edits to at least one file
- enough command/tool activity to show the session touched a meaningful task
  surface

The evidence summary should say "session touched a complex-looking area" rather
than claiming the repository itself is globally complex.

#### abandoned_or_stopped

Emit `abandoned_or_stopped` when the user explicitly stops or defers and there
is no later final answer that resolves the session.

Phase 6 may add a small `stop_or_pause_marker` feature if needed. Candidate
markers:

```text
stop
stop doing
pause
leave it
never mind
not now
we can stop
```

Do not emit this label for ordinary scope control such as "do not edit that
file" unless there is explicit stop/defer language near the end.

### Labels To Keep Deferred

Defer these unless new evidence makes them precise:

```text
neutral
clarification
blocked
positive_resolution
```

They are useful concepts, but Phase 6 can cover the immediate needs with
session labels and score features.

## Evidence And Metadata

Every classification should include:

- label
- score
- confidence
- evidence event IDs
- evidence summary
- metadata with rule version, thresholds, score feature, and contributing
  features

Evidence summaries should be short but specific:

```text
Session shows repeated user requests and late correction evidence.
Session has repeated failing command output from the same command loop.
Session appears clean: no failure, repeat, correction, or unresolved-ending evidence.
```

Avoid summaries that merely restate the label:

```text
Session is classified as user_stuck.
```

## CLI And Artifact Behavior

No new CLI commands are planned for Phase 6.

The existing command remains the user-facing validation path:

```bash
session-doctor analyze <session-id> --db <path>
session-doctor analyze <session-id> --db <path> --format json
```

Terminal output should add a compact score section or include these score rows
in the existing metrics table:

```text
friction_score
stuckness_score
prompt_clarity_risk
agent_fit_risk
project_complexity_signal
```

JSON artifacts should expose the new score features through the existing
`session_features` and `summary_metrics` fields. The top-level artifact shape
should not change.

## Privacy And Storage Defaults

Phase 6 should not expand raw sensitive storage. It can add hashes, counts,
IDs, booleans, score values, threshold metadata, and feature names.

If a classification needs to reference command text, file paths, or message
text, prefer event IDs and existing feature evidence. Do not add raw tool
output, command output, patch content, write content, or full edit bodies to
artifacts.

## Task Splits And Commit Points

### Commit 1: Phase 6 Plan And Roadmap Docs

Deliverables:

- add this `docs/phase-6-plan.md`
- update `docs/session-doctor-design.md` to link to the Phase 6 plan
- update README design references if needed
- keep Phase 6 marked planned, not complete
- do not touch runtime code in this commit

Likely files:

- `docs/phase-6-plan.md`
- `docs/session-doctor-design.md`
- `README.md`

Implementation sketch:

```text
1. Add this plan as the implementation contract for Phase 6.
2. Keep Phase 7 Aggregate Summary MVP sequencing intact.
3. Do not implement classification changes in the planning commit.
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
Phase 6 can be reviewed as a concrete classification-scoring plan before code changes.
```

### Commit 2: Score Feature Helpers

Deliverables:

- add reusable score helper functions
- emit `friction_score`, `stuckness_score`, `prompt_clarity_risk`,
  `agent_fit_risk`, and `project_complexity_signal`
- include formula metadata and contributing feature evidence
- tests for score bounds and representative low/medium/high sessions

Likely files:

- `src/session_doctor/analysis/features.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def capped_count(value: int, cap: int) -> float:
    if cap <= 0:
        return 0.0
    return min(value, cap) / cap


def score_feature(..., formula_version: str, components: dict[str, float]) -> SessionFeature:
    score = clamp01(sum(components.values()))
    return session_feature(
        analysis_run_id,
        session_id,
        feature_name,
        f"{score:.3f}",
        score=score,
        evidence={"source_event_ids": ...},
        metadata={"formula": formula_version, "components": components},
    )
```

Expected tests:

- clean short session has low scores
- repeated correction/failure session has high friction and stuckness
- broad multi-file session has elevated project complexity signal
- scores never exceed `1.0` or fall below `0.0`

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check .
```

Clean commit point:

```text
Reusable risk score features are persisted through the existing session feature model.
```

### Commit 3: Classification Refactor

Deliverables:

- refactor `classify_session()` into small rule helpers
- keep existing labels behavior-compatible where practical
- make rule metadata consistent
- make evidence summaries more specific
- add tests for metadata and evidence summary content

Likely files:

- `src/session_doctor/analysis/classification.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
@dataclass(frozen=True)
class ClassificationContext:
    bundle: ParsedSessionBundle
    analysis_run_id: str
    message_features: list[MessageFeature]
    session_features: dict[str, SessionFeature]


def classify_user_stuck(context: ClassificationContext) -> SessionClassification | None:
    stuckness_score = float_feature(context.session_features, "stuckness_score")
    if stuckness_score < USER_STUCK_THRESHOLD:
        return None
    return classification(
        ...,
        metadata={
            "rule": "user_stuck_v2",
            "score_feature": "stuckness_score",
            "threshold": USER_STUCK_THRESHOLD,
            "contributing_features": [...],
        },
    )
```

Expected tests:

- existing user-stuck fixture still emits `user_stuck`
- existing tooling fixture still emits `tooling_blocked`
- repeated command loop still emits `agent_looping`
- resolved correction fixture still emits `resolved_after_corrections`
- classification metadata contains score feature and threshold

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check .
```

Clean commit point:

```text
Classification rules are small, metadata-rich, and ready for conservative label expansion.
```

### Commit 4: Conservative New Labels

Deliverables:

- add `healthy`
- add `agent_misunderstood`
- add `prompt_ambiguous` if fixture calibration supports it
- add `task_too_large` and `repo_complexity_high` if thresholds are precise
  enough
- add `abandoned_or_stopped` if stop/defer marker evidence is implemented
- add or defer small marker features required by those labels
- tests for each emitted label and each important false-positive boundary

Likely files:

- `src/session_doctor/analysis/features.py`
- `src/session_doctor/analysis/classification.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
if no_negative_labels and healthy_score_conditions(context):
    classifications.append(healthy_classification(context))

if correction_count >= 1 and prompt_clarity_risk >= AGENT_MISUNDERSTOOD_THRESHOLD:
    classifications.append(agent_misunderstood_classification(context))

if project_complexity_signal >= TASK_TOO_LARGE_COMPLEXITY_THRESHOLD and friction_score >= 0.35:
    classifications.append(task_too_large_classification(context))
```

Expected tests:

- clean finished session emits `healthy`
- correction plus final answer can emit both `agent_misunderstood` and
  `resolved_after_corrections`
- single scope-boundary marker does not emit `prompt_ambiguous`
- broad low-friction session does not emit `task_too_large`
- explicit stop near the end can emit `abandoned_or_stopped`
- non-command repeated tool failure still does not emit `agent_looping`

Validation:

```bash
uv run pytest tests/test_analysis.py -q
uv run ruff check src/session_doctor/analysis tests/test_analysis.py
uv run ty check .
```

Clean commit point:

```text
Phase 6 expands labels conservatively with calibrated false-positive tests.
```

### Commit 5: CLI And Artifact Exposure

Deliverables:

- expose new score features in terminal output
- ensure `analyze --format json` includes new scores in `session_features` and
  `summary_metrics`
- ensure default artifact writing still works
- CLI tests for Codex and Pi fixture sessions
- no new command surface

Likely files:

- `src/session_doctor/cli.py`
- `tests/test_cli.py`
- `tests/test_analysis.py`

Implementation sketch:

```python
ANALYSIS_SUMMARY_FEATURES = [
    "friction_score",
    "stuckness_score",
    "prompt_clarity_risk",
    "agent_fit_risk",
    "project_complexity_signal",
    ...
]
```

Expected tests:

- terminal analyze output includes the new score names
- JSON output includes the new score features
- artifact output includes classification metadata for Phase 6 labels
- `summary`, `trends`, `report`, and `graph` are not introduced in Phase 6

Validation:

```bash
uv run pytest tests/test_cli.py tests/test_analysis.py -q
uv run ruff check src tests
uv run ty check .
```

Clean commit point:

```text
Analyze exposes Phase 6 scores and classifications through existing output paths.
```

### Commit 6: Docs And Manual Smoke Test

Deliverables:

- update design docs to mark Phase 6 complete only after code is implemented
- update README only if user-facing output examples changed
- copied-real-session smoke tests for Codex and Pi
- final full quality gate

Likely files:

- `docs/phase-6-plan.md`
- `docs/session-doctor-design.md`
- optionally `README.md`

Suggested manual smoke test:

```bash
rm -f /tmp/session-doctor-phase6.duckdb
uv run session-doctor ingest --agent codex \
  --source /tmp/copied-codex-session.jsonl \
  --db /tmp/session-doctor-phase6.duckdb
uv run session-doctor ingest --agent pi \
  --source /tmp/copied-pi-session.jsonl \
  --db /tmp/session-doctor-phase6.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-phase6.duckdb
uv run session-doctor analyze <codex-session-id> \
  --db /tmp/session-doctor-phase6.duckdb \
  --format json
uv run session-doctor analyze <pi-session-id> \
  --db /tmp/session-doctor-phase6.duckdb
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
Phase 6 scoring behavior is documented and validated end to end.
```

## Recommended Implementation Order

1. Lock the plan and roadmap docs.
2. Add score features before changing classification rules.
3. Refactor classification helpers without changing labels.
4. Add conservative new labels only after score fixtures are calibrated.
5. Update CLI/artifact exposure after score and label behavior is stable.
6. Run copied-real-session smoke tests before marking Phase 6 complete.

## Open Questions Before Implementation

None block implementation. The implementation should still ask for steering if
fixture calibration shows that a proposed new label cannot be made precise with
the existing deterministic evidence.
