# Phase 8 Plan: Project-Level Trends

Status: planned; grilling approved.

This document is the implementation contract for Phase 8. The plan completed an
interactive grilling pass and a final adversarial review before implementation.

## Goal

Phase 8 should extend the existing read-only aggregate summary into deterministic
time-series and recurring-pattern views over already ingested sessions and their
latest persisted analysis.

The target pipeline is:

```text
normalized Codex / Claude / Pi sessions
  + latest analysis rows per session
  -> explicit project, agent, cohort, and time filters
  -> aligned weekly or monthly buckets
  -> coverage, risk, score, classification, and recurrence metrics
  -> guarded trend judgments
  -> terminal and stable JSON output
```

The phase should answer:

- How many matching sessions occurred in each period?
- How much of each period has analysis coverage?
- Are friction, stuckness, prompt-clarity risk, agent-fit risk, project
  complexity, or risky-session rate changing materially?
- Do top-level and sidechain sessions show different patterns?
- What risk and coverage has each agent exhibited in the selected scope?
- Which commands, failed tool-result fingerprints, and problematic files recur
  across distinct sessions?
- Which observed project paths have enough data to inspect separately?

Clean completion point:

```text
Trends expose aligned, explainable cross-session evidence without inventing
project identity, causal agent comparisons, or confidence unsupported by the
sample size.
```

## Starting Point

Phases 1 through 7 and the pre-Phase-8 milestone provide:

- strict normalized Pydantic schemas and DuckDB schema version 3
- Codex, Claude Code, and Pi ingestion through one agent-neutral pipeline
- UTC-normalized DuckDB timestamps, with nullable timestamps preserved
- session `cwd`, `project_path`, `is_sidechain`, and parent-session fields
- canonical command identity and redacted command display fields
- canonical and project-relative file identity fields
- privacy-preserving tool-result output hashes and lengths
- deterministic Phase 6 classifications and five reusable score features
- source-scoped replacement and one current persisted analysis per session
- the read-only Phase 7 `summary` command, filters, aggregate queries, terminal
  rendering, stable JSON payload, and privacy tests
- copied-local validation for all three native adapters
- 187 passing tests plus Ruff and `ty` quality gates

The current aggregate implementation already contains useful SQL fragments for
filtered sessions, latest analysis selection, score extraction, risk labels,
failed-command groups, and problematic-file groups. Phase 8 should extract and
reuse those contracts rather than copying them into a parallel query stack.

## Resolved Planning Decisions

Phase 8 should use these decisions unless grilling exposes a contradiction.

### Query And Persistence

- Keep trends read-only and calculate them on demand.
- Do not persist trend rows or add a materialized trend cache.
- Do not add a schema migration unless implementation proves the existing
  normalized and analysis tables cannot express a required contract.
- Use only the latest analysis run for each session, and use its metrics only
  when its `analyzer_version` matches the current analyzer version.
- Add `src/session_doctor/analysis/version.py` as the single owner of
  `ANALYZER_VERSION`, initially preserving the existing `"phase6"` value.
  Formula or classification changes that affect comparability must change that
  version.
- Do not automatically analyze sessions from `trends` or `projects list`.
- Keep never-analyzed and stale-analysis sessions visible as distinct counts.
- Add an explicit `analyze --all` recovery path; do not weaken trend
  comparability or mutate data implicitly to hide stale analysis.
- Apply one filter/window contract consistently to every trend section.

### Project Identity

- Treat `COALESCE(NULLIF(project_path, ''), NULLIF(cwd, ''))` as an observed
  project-path hint, not a guaranteed repository root.
- `projects list` reports exact observed path hints. It does not infer common
  ancestors, inspect Git metadata, or merge nested paths.
- `trends --project <path>` deliberately defines a user-selected scope and
  includes sessions whose stored `project_path` or `cwd` equals that path or is
  beneath it.
- Keep nested observed paths distinct in global project rows so one session is
  not counted in multiple guessed project groups.
- Preserve existing home-path redaction in terminal and JSON output.
- Do not add a project registry, project table, aliases, or VCS-root detection
  in this phase.

### Session Cohorts

- Treat top-level sessions and sidechain sessions as separate cohorts.
- Never combine their score averages, risk rates, classification rates, or
  trend judgments.
- Use `sessions.is_sidechain = false` for the `top_level` cohort and `true` for
  the `sidechain` cohort.
- Search linked root/sidechain families for recurring command, tool-result, and
  file patterns, but report distinct top-level and sidechain session counts for
  every pattern.
- Do not recursively roll child activity or analysis into a parent session.
- Do not treat a sidechain as equivalent to a top-level session in agent
  comparisons.

### Time Semantics

- Bucket sessions by `sessions.started_at`.
- Treat persisted timestamps as UTC-naive values representing normalized UTC.
- Use Monday-starting UTC calendar weeks and UTC calendar months.
- Use half-open bucket intervals: `[bucket_start, bucket_end)`.
- Default to `--bucket week --periods 12`.
- Anchor the default window to the bucket containing the latest timed session
  after project and agent filters, before splitting cohorts.
- Expose the deterministic `latest_session_at` timestamp. Do not calculate a
  wall-clock data age in terminal or JSON output.
- Emit every bucket in the selected range, including empty buckets.
- Report matching sessions with no `started_at` as `untimed_sessions`; do not
  guess a bucket from `ended_at`, analysis time, file time, or command time.
- Report matching timed sessions outside the selected window separately from
  windowed sessions.
- Use the same aligned window for both metric cohorts. Recurrence selects root
  session families by the root session's bucket in that window.
- Reject unsupported bucket values and non-positive period counts explicitly.
- Cap periods at a documented upper bound of 120 to avoid accidental unbounded
  terminal and JSON output.

For a latest matching session in the week beginning 2026-07-06 and 12 weekly
periods, the range is:

```text
[2026-04-20T00:00:00Z, 2026-07-13T00:00:00Z)
```

### Analysis Coverage And Denominators

- Count all windowed sessions in `sessions`.
- A session has current analysis only when its selected latest `analysis_run`
  uses the current analyzer version.
- Report `current_analyzed_sessions`, `stale_analysis_sessions`, and
  `never_analyzed_sessions` separately for both all matching sessions and the
  timed windowed subset.
- Report analyzer-version counts for matching and windowed stale/current
  analyses.
- Current-analysis coverage is `current_analyzed_sessions / sessions` for the
  bucket or cohort.
- Score averages use only sessions whose latest analysis contains that score.
- Every score average includes its own sample count.
- Classification rate is distinct sessions with the label divided by analyzed
  sessions, not all ingested sessions.
- Risky-session rate is distinct risky sessions divided by analyzed sessions.
- Return `null`, not zero, when a rate or average has no valid denominator.
- Do not let unanalyzed sessions silently lower score or classification rates.

### Risk Semantics

Reuse the Phase 7 risk contract:

- negative/risk labels are the existing deterministic negative labels
- a session is risky when it has a risk label or its maximum available Phase 6
  risk score is at least `0.55`
- risk score names are:
  - `friction_score`
  - `stuckness_score`
  - `prompt_clarity_risk`
  - `agent_fit_risk`
  - `project_complexity_signal`

The shared risk-label set and threshold should have one code owner used by
summary and trends.

### Guarded Trend Judgments

Directional judgments are available only when `--project` defines an explicit
project scope. Global trends still expose raw series, project/agent observations,
and recurring patterns without cross-project direction claims.

Produce one direction result per metric and for risky-session rate in each
cohort.

Outcome metric statuses for `friction_score`, `stuckness_score`,
`prompt_clarity_risk`, and risky-session rate are:

```text
improving
worsening
no_material_change
insufficient_data
```

Neutral signal statuses for `agent_fit_risk` and
`project_complexity_signal` are:

```text
decreasing
increasing
no_material_change
insufficient_data
```

Do not produce one combined project-health judgment. Direction describes a
window comparison, not a causal explanation or monotonic slope.

Judgment algorithm:

1. Split the selected buckets into two equal most-recent windows.
2. If the number of buckets is odd, leave the earliest extra bucket visible in
   the time series but exclude it from judgment comparison.
3. In each comparison window, require at least half its buckets to be non-empty,
   rounded up, with a minimum of three non-empty buckets.
4. For a score metric, require at least six sessions with that score in each
   comparison window.
5. For risky-session rate, require at least six analyzed sessions in each
   comparison window.
6. Require current-analysis coverage of at least `0.80` in both windows and an
   absolute coverage difference no greater than `0.15`. Score-specific sample
   coverage is sessions containing that score divided by all cohort sessions in
   the comparison window; it must satisfy the same `0.80` minimum and `0.15`
   maximum difference for each score judgment.
7. Calculate each comparison value directly over its eligible sessions, not as
   an unweighted mean of bucket means.
8. Calculate `delta = recent_value - earlier_value`.
9. Use one absolute materiality threshold of `0.10`:
   - outcome metrics: negative means `improving`, positive means `worsening`
   - neutral signals: negative means `decreasing`, positive means `increasing`
   - absolute deltas below `0.10` mean `no_material_change`
10. If any gate fails, return `insufficient_data` with machine-readable reasons.

Each judgment must expose:

- metric name
- status
- earlier value and recent value
- signed delta
- earlier and recent sample counts
- earlier and recent non-empty bucket counts
- earlier and recent current-analysis/sample coverage
- threshold
- comparison method (`two_window_session_weighted_mean`)
- reason codes when insufficient

Expected insufficient-data reasons include:

```text
too_few_comparison_buckets
too_few_nonempty_earlier_buckets
too_few_nonempty_recent_buckets
too_few_earlier_samples
too_few_recent_samples
insufficient_earlier_coverage
insufficient_recent_coverage
coverage_difference_too_large
project_scope_required
```

Do not add configurable gates in Phase 8. Stable fixed gates keep terminal,
JSON, tests, and interpretation aligned.

### Agent Observations

- Report agent observations separately inside each cohort.
- Include session count, analyzed count, coverage, risky-session rate, score
  averages, score sample counts, and classification counts/rates.
- Use the same selected window as the bucket series.
- Do not rank agents as best or worst.
- Do not emit per-agent improving/worsening judgments in this phase.
- Describe `agent_fit_risk` as observed agent-fit risk, not proof that the agent
  caused the outcome or that another agent would perform better.
- Keep task-type comparisons deferred because there is no normalized task-type
  identity.

### Recurring Patterns

A pattern is recurring only when it appears in at least two distinct top-level
session families whose root sessions are in the selected window. Multiple
events or sidechains in one root family remain one workflow, not a project-level
recurrence.

Resolve linked sidechains recursively to a same-agent session with
`is_sidechain = false` for recurrence only. Include linked sidechain activity in
that family's pattern evidence. Exclude and count malformed chains by reason:

```text
orphan_parent
cycle
cross_agent_parent
```

A top-level session is its own root. A sidechain with a null/missing parent is
orphaned; a repeated session ID in its ancestor walk is cyclic; and any
parent-child edge crossing agent names is invalid. These states do not fail the
command and are never guessed into independent families. This does not roll
sidechain scores, labels, or counts into top-level metric cohorts.

Project and agent filters apply to both levels: a root must match the active
filters for its family to enter recurrence, and only linked member sessions that
independently match those filters may contribute activity. A matching root does
not pull outside-project descendant activity into the selected scope, and a
matching descendant cannot pull a non-matching root family into scope.

Every recurring-pattern row should include:

- event/activity count
- distinct session count
- distinct root-family count
- distinct top-level session count
- distinct sidechain session count
- sorted agent names
- active bucket count
- first and most recent timestamps when available
- one deterministic example session ID

Rows should be ranked by distinct root-family count, then distinct session
count, then event count, then recency, then a stable display/fingerprint key.
Apply `--limit` independently to each pattern section.

#### Failed Commands

- Before exposing recurring command examples, harden the shared command-display
  redactor used by summary and trends. It must cover sensitive option names in
  both `--key=value` and `--key value` forms, credential-bearing environment
  assignments and URLs, and authorization/header token forms such as Bearer
  credentials. Add privacy sentinels for each supported shape. If a value cannot
  be separated safely, omit the display example rather than exposing the native
  command.
- Reuse `command_identity_hash` as identity and `command_display` as the
  redacted example.
- Count non-zero exits and deliberately normalized cancelled/interrupted command
  outcomes using the same failure contract as summary. Extract metadata flags
  through structured DuckDB JSON functions in one shared tested predicate; do
  not preserve brittle raw JSON substring matching as the long-term contract.
- Attribute recurrence to the root session's start bucket. Use command event
  timestamps only for first/most-recent evidence metadata.
- Never expose native unredacted command text or the identity hash.

#### Failed Tool-Result Fingerprints

- Include only tool results with `is_error = true` and a non-null `output_hash`.
- Join the related tool call name when available.
- Group by tool name plus output hash so identical generic output from unrelated
  tools is not merged.
- Expose a stable opaque fingerprint ID derived from the tool name and output
  hash; do not expose the native output hash. Document that this is a
  correlational identifier, not protection against guessing low-entropy output.
- Do not expose tool output, arguments, command output, diffs, or sidecar
  content.
- Treat missing tool-call names explicitly as unknown rather than guessing.

#### Problematic Files

- Reuse the Phase 7 problematic-session risk contract and mutating file
  operations.
- Group by canonical path first, then reuse Phase 7's project-path plus
  project-relative-path reconstruction when both values are trustworthy.
- Do not correlate unresolved relative paths across sessions.
- Require activity in at least two distinct root families.
- Redact home paths before terminal or JSON output.
- Do not expose write/edit/patch content or content hashes.

Classification bucket counts/rates plus the three recurring-pattern sections
are the Phase 8 representation of repeated stuck patterns. Do not invent a new
cross-session classifier in this phase.

## Command Contract

### Batch Analysis Recovery

Make the existing positional `session_id` optional at the Typer boundary while
requiring exactly one of `SESSION_ID` or `--all`. Preserve the existing
single-session invocation and defaults:

```bash
session-doctor analyze --all [OPTIONS]
```

Batch options:

```text
--project PATH        Analyze matching exact/descendant project scopes
--agent NAME          Analyze only one native agent
--force               Reanalyze already-current matching sessions too
--write-artifacts     Opt into normal per-session JSON artifacts
--format VALUE        terminal or json
```

Batch behavior:

- without `--force`, select stale-analysis and never-analyzed matching sessions
- skip and count already-current sessions
- with `--force`, select every matching session
- process sessions in deterministic `started_at NULLS LAST, session_id` order
- keep each session's existing transactional analysis replacement boundary
- write no artifacts by default; `--write-artifacts` opts into normal per-session
  artifact paths
- continue after a session failure, report succeeded/skipped/failed IDs and
  counts, and exit 1 after processing when any session failed
- emit one batch summary payload in JSON mode, with no progress text mixed into
  stdout
- refactor single-session analysis internals to raise typed workflow failures
  that the single and batch CLI surfaces can render deliberately; do not drive
  batch control flow by catching nested `typer.Exit`
- reject both a positional session ID together with `--all` and neither mode
- reject batch-only options unless `--all` is present
- reject single-session `--artifact` and `--no-artifact` in batch mode
- reject `--write-artifacts`, `--force`, `--project`, and `--agent` in
  single-session mode
- keep positional single-session `analyze` behavior and artifact defaults
  unchanged

Batch counts obey these invariants:

```text
matching = selected + skipped
selected = succeeded + failed
```

`selected` means sessions actually attempted. `skipped` means matching sessions
already current when `--force` is absent.

The batch JSON payload should contain:

```json
{
  "filters": {"project": null, "agent": null},
  "analyzer_version": "phase6",
  "force": false,
  "write_artifacts": false,
  "counts": {
    "matching": 4,
    "selected": 3,
    "succeeded": 2,
    "skipped": 1,
    "failed": 1
  },
  "succeeded_session_ids": ["session-a", "session-b"],
  "skipped_session_ids": ["session-current"],
  "failures": [
    {
      "session_id": "session-c",
      "code": "analysis_failed",
      "message": "Session analysis failed"
    }
  ]
}
```

Stable failure codes should distinguish at least:

```text
session_not_loadable
analysis_failed
artifact_write_failed
persistence_failed
```

Failure messages must be generic and must not contain raw exceptions, source
paths, messages, commands, tool output, or artifact paths. Terminal mode may
render these same safe fields. JSON mode writes no progress or raw diagnostics
to stdout; all expected per-session failures belong in the payload. Unexpected
internal exceptions should map to `analysis_failed` with their original cause
preserved in-process for debugging, not serialized.

### Trends

```bash
session-doctor trends [OPTIONS]
```

Options:

```text
--db PATH             DuckDB path; same default and validation as summary
--project PATH        Exact-or-descendant project_path/cwd scope
--agent NAME          codex, claude, or pi
--bucket VALUE        week or month; default week
--periods INTEGER     aligned bucket count; default 12; range 1..120
--limit INTEGER       maximum rows per ranked detail section; default 10
--format VALUE        terminal or json; default terminal
```

The command should:

- require an existing current-schema database
- use the existing agent-name and path normalization rules
- reject invalid options with stable Typer errors
- remain read-only
- not write an artifact
- not trigger ingestion or analysis

### Projects List

```bash
session-doctor projects list [OPTIONS]
```

Options:

```text
--db PATH
--agent NAME
--limit INTEGER        default 10; must be positive
--format terminal|json
```

Each observed project row should include:

- redacted observed path
- total sessions
- top-level sessions
- sidechain sessions
- current-analyzed, stale-analysis, and never-analyzed sessions
- analyzer-version counts
- first and latest timed session
- agent names

Unknown project paths should be reported as an aggregate count outside the
observed-path rows, not represented as a fake project named `(unknown)`.
Order observed project rows by total session count descending, latest timed
session descending with nulls last, then raw observed path ascending before
applying the limit; redact only during payload/render conversion.

## Query Architecture

Fight append-bias by extracting shared summary/trend contracts before adding
new queries.

Suggested modules:

```text
src/session_doctor/store/aggregate_queries.py
src/session_doctor/store/trend_models.py
src/session_doctor/store/trend_readers.py
src/session_doctor/trend_payload.py
```

Likely existing files to update:

```text
src/session_doctor/store/summary_readers.py
src/session_doctor/store/duckdb.py
src/session_doctor/store/__init__.py
src/session_doctor/cli_options.py
src/session_doctor/cli_renderers.py
src/session_doctor/cli.py
```

`aggregate_queries.py` should own only genuinely shared contracts, likely:

- access to `analysis.version.ANALYZER_VERSION`
- risk-label and score-name constants
- filtered base-session SQL helpers
- latest-analysis selection
- current/stale/never analysis compatibility classification
- latest score extraction
- latest label grouping
- risky-session predicate construction
- command-failure predicate construction

Do not create a generic SQL-builder framework. Keep fixed query shapes explicit,
parameterized, and reviewable.

`trend_readers.py` should orchestrate one read-only connection and return strict
frozen models. It may use focused query functions for:

- window bounds and scope totals
- bucket/cohort metrics
- classification bucket metrics
- cohort judgments
- agent observations
- project observations
- recurring failed commands
- recurring tool-result fingerprints
- recurring problematic files

Dynamic bucket SQL must select from a validated internal enum or fixed mapping;
never interpolate unvalidated CLI text.

## Output Model

The stable JSON shape should follow this structure:

```json
{
  "filters": {
    "project": "~/project",
    "agent": null,
    "bucket": "week",
    "periods": 12,
    "limit": 10
  },
  "window": {
    "start": "2026-04-20T00:00:00",
    "end": "2026-07-13T00:00:00",
    "anchor": "latest_matching_session",
    "latest_session_at": "2026-07-10T10:00:00"
  },
  "scope": {
    "matching_sessions": 20,
    "windowed_sessions": 16,
    "outside_window_sessions": 3,
    "untimed_sessions": 1,
    "analysis_compatibility": {
      "current_analyzer_version": "phase6",
      "matching": {
        "current": 16,
        "stale": 2,
        "never": 2,
        "version_counts": {"phase5": 2, "phase6": 16}
      },
      "windowed": {
        "current": 13,
        "stale": 2,
        "never": 1,
        "version_counts": {"phase5": 2, "phase6": 13}
      }
    }
  },
  "cohorts": {
    "top_level": {
      "totals": {},
      "buckets": [],
      "judgments": [],
      "agents": []
    },
    "sidechain": {
      "totals": {},
      "buckets": [],
      "judgments": [],
      "agents": []
    }
  },
  "projects": [],
  "recurring_patterns": {
    "failed_commands": [],
    "failed_tool_results": [],
    "problematic_files": []
  }
}
```

For no matching sessions or matching sessions with no `started_at`, preserve the
same window object with:

```json
{
  "start": null,
  "end": null,
  "anchor": "none",
  "latest_session_at": null
}
```

All matching analysis-compatibility counts remain available, while every
windowed count is zero.

Exact nested metric fields should be fixed in tests before the CLI is treated as
stable. Use `null` for unavailable values and round displayed/JSON scores and
rates consistently without changing the underlying query values.

PR 2 exposes only implemented scope, window, cohort, and judgment keys. PR 3
adds final agent, project, and recurring-pattern keys. Do not represent
not-yet-implemented sections as successful empty results; the Phase 8 payload is
declared stable only after PR 3.

Terminal output should remain compact:

- one scope/window table
- one bucket table per non-empty cohort, including explicit empty periods
- one judgment table per cohort
- one agent observation table per cohort when rows exist
- one project table
- one table per non-empty recurring-pattern section
- concise insufficient-data reasons rather than hiding missing judgments

Do not add trend recommendations in Phase 8. The measured series and guarded
judgments are the output; recommendations would introduce a second policy layer
before trend behavior has real-world calibration.

## Privacy Contract

Trend output must not expose:

- source log paths
- message text or message hashes
- native unredacted commands or command identity hashes
- command stdout/stderr hashes
- tool arguments or argument hashes
- raw tool output or native output hashes
- file contents, patches, diffs, sidecar contents, or content hashes
- raw metadata JSON

Trend output may expose:

- session IDs as evidence anchors
- agent names
- redacted observed project/file paths
- redacted canonical command examples
- opaque derived failed-tool fingerprint IDs
- aggregate counts, rates, score values, labels, and timestamps

Use explicit payload conversion and privacy regression tests. Do not serialize
store dataclasses generically.

## Error And Empty-State Contract

- Missing database: use the existing stable missing-database error.
- Invalid or incompatible database: use existing stable database errors.
- No matching sessions: return successful empty terminal/JSON output that names
  the active filters.
- Matching untimed sessions but no timed sessions: report scope counts, no
  fabricated window (`start`, `end`, and `latest_session_at` are null and
  `anchor` is `none`), empty bucket lists, and insufficient judgments.
- Timed sessions but no current analysis: emit session buckets and zero current
  coverage; distinguish stale from never-analyzed sessions; score,
  classification, risk-rate, and judgments remain unavailable.
- Mixed analyzer versions: exclude stale metrics, report version counts, and
  direct users to `analyze --all` without mutating from `trends`.
- One cohort absent: preserve its stable JSON object with zero totals and empty
  rows; omit noisy terminal detail tables for it.
- No recurring patterns: return empty arrays and a concise terminal statement.

## Test Strategy

Use synthetic fixtures and direct store-model builders. Do not make automated
tests depend on real local session stores or the current date.

### Shared Aggregate Refactor

- Phase 7 summary metrics and payload structure remain unchanged after helper
  extraction. Shared command examples may become more aggressively redacted as
  the deliberate privacy fix required before recurring-command output.
- Latest-analysis selection remains deterministic under timestamp ties.
- current, stale, and never-analyzed states are mutually exclusive
- mixed analyzer versions never enter one Phase 8 metric
- changing the analyzer-version constant makes prior analysis stale
- Risk labels, thresholds, and command-failure semantics have one tested owner.
- Project and agent filters behave identically in summary and trends.

### Window And Bucket Tests

- Monday UTC weekly boundaries
- month and year boundaries
- offset-aware source timestamps after DuckDB UTC normalization
- latest-filtered-session anchoring
- deterministic latest-session timestamp without wall-clock age fields
- empty periods between non-empty periods
- exact start inclusion and end exclusion
- odd period counts and judgment-window exclusion of the earliest extra period
- missing timestamps and all-untimed scopes
- sessions before the selected window
- invalid bucket, period, and limit values
- null-valued stable window object for no-match and all-untimed scopes

### Coverage And Metric Tests

- analyzed and unanalyzed sessions in one bucket
- stale and current analysis in one bucket
- score averages exclude missing scores and expose score-specific samples
- classifications use analyzed-session denominators
- risk-rate threshold and risk-label paths
- null values for unavailable denominators
- latest analysis rows only
- current analyzer-version rows only
- no cross-cohort mixing

### Judgment Tests

- improving at exactly `-0.10`
- worsening at exactly `0.10`
- decreasing/increasing statuses for neutral signal metrics
- `no_material_change` inside the threshold
- direct session-weighted values rather than mean-of-bucket-means
- each insufficient-data reason
- six-sample, 80% coverage, 15-point coverage-difference, and proportional
  non-empty-bucket boundaries
- separate top-level and sidechain judgments
- no overall or per-agent judgment
- no directional judgment without an explicit project scope

### Project And Agent Tests

- exact observed project rows remain distinct for nested paths
- `--project` includes exact and descendant stored paths
- unknown project paths are counted separately
- home redaction in terminal and JSON
- per-agent observations are cohort-specific
- agent filter behavior for Codex, Claude, and Pi
- no best/worst agent claim appears in payload or terminal output
- deterministic project ordering before the default limit of 10

### Recurring Pattern Tests

- two events in one root family do not qualify
- matching activity in two sidechains of one root family does not qualify
- linked sidechain activity under two distinct root families qualifies
- orphan sidechains cannot establish recurrence
- recursive family resolution assigns one root and detects invalid topology
- root-session start time owns recurrence window/bucket attribution
- matching roots include only linked member activity that independently matches
  project and agent filters
- command wrapper normalization groups cross-adapter failures
- command secret redaction survives trend payload conversion
- identical tool output from different tool names stays separate
- missing tool names remain explicit
- native output hashes are absent from output
- canonical/project-relative files group across adapters
- unresolved paths never group across sessions
- non-problematic-session files are excluded
- pattern rows split top-level and sidechain session counts
- deterministic ordering under ties

### CLI And Payload Tests

- terminal and JSON output for both bucket sizes
- stable top-level and nested JSON keys
- explicit empty arrays and null values
- output does not write analysis, artifacts, or trend rows
- existing `summary`, single-session `analyze`, ingest, and database commands
  remain unchanged
- privacy sentinels do not occur in terminal or JSON output

### Batch Analysis Tests

- default selection includes stale and never-analyzed sessions only
- batch count invariants distinguish matching, selected, and skipped sessions
- `--force` includes already-current sessions
- project and agent filters match trend scope semantics
- deterministic processing order
- artifacts are absent by default and written only with `--write-artifacts`
- one failed session does not stop later sessions
- any failure produces exit code 1 after the batch summary
- JSON stdout contains only the batch payload
- typed workflow failure categories map to stable safe failure entries
- raw exceptions and sensitive paths/content do not enter batch terminal or JSON
- positional session ID and `--all` are mutually exclusive
- omitting both positional session ID and `--all` fails clearly
- `--artifact`/`--no-artifact` fail in batch mode and `--write-artifacts` fails
  in single-session mode
- batch-only options without `--all` fail clearly
- rerunning a successful default batch selects no sessions

## Manual Copied-Local Validation

After fixture behavior is stable:

1. Copy recent completed Codex, Claude, and Pi sources into an isolated temporary
   directory.
2. Ingest and analyze enough copied sessions to exercise multiple buckets when
   available.
3. Run weekly and monthly trends globally and for one copied project scope.
4. Run `projects list` in terminal and JSON formats.
5. Record only parser/native versions, structural counts, bucket counts,
   coverage, warning codes, judgment statuses/reasons, recurring-pattern counts,
   and observed false-positive/false-negative notes.
6. Do not record source paths, messages, commands, tool output, diffs, or file
   content.
7. Remove copied sources, temporary databases, artifacts, and validation scripts.

If local history cannot satisfy the sample, density, version, and coverage
gates, record
`insufficient_data` as the expected honest result rather than weakening the
gate or fabricating additional evidence.

## Delivery Plan

The phase-plan document should land before implementation. Implementation should
then use four reviewable pull requests.

### PR 1: Analysis Version Contract And Batch Recovery

Deliverables:

- define one current analyzer-version constant and document its bump contract
- classify current, stale, and missing persisted analyses
- extend `analyze` with mutually exclusive `--all` mode
- add project/agent filters and `--force`
- keep batch artifacts opt-in through `--write-artifacts`
- continue across per-session failures and return a stable batch summary
- preserve all single-session analysis behavior
- focused workflow, CLI, store, artifact, and failure tests

Clean commit points:

```text
Analysis comparability has one explicit version contract.
```

```text
Users can deliberately restore current analysis coverage in bulk.
```

### PR 2: Trend Foundation And Time-Series Vertical Slice

Deliverables:

- extract shared aggregate query contracts without changing summary behavior
- add trend filters and strict result models
- add aligned weekly/monthly window calculation
- add scope totals and explicit empty/untimed handling
- add top-level and sidechain bucket metrics
- add score, coverage, classification, and risk-rate metrics
- add guarded per-cohort judgments
- add `trends` terminal and JSON output
- focused store, payload, CLI, regression, and privacy tests

Clean commit points:

```text
Summary and trends share one latest-analysis and risk contract.
```

```text
Trends exposes a useful read-only weekly/monthly vertical slice.
```

### PR 3: Projects, Agent Observations, And Recurring Patterns

Deliverables:

- add the `projects` Typer group and `projects list`
- add observed project rows and unknown-project counts
- add cohort-specific agent observations
- add recurring failed-command groups
- harden shared command-display redaction before exposing recurring examples
- add recurring failed-tool-result fingerprints
- add recurring problematic-file groups
- add recursive root-family attribution for recurrence only
- add deterministic ranking and per-section limits
- complete terminal and JSON sections
- cross-adapter identity and privacy regression tests

Clean commit points:

```text
Observed project and agent cohorts are explicit without guessed identity.
```

```text
Recurring patterns correlate cross-session evidence without exposing content.
```

### PR 4: Cross-Adapter Validation And Completion

Deliverables:

- three-adapter end-to-end fixture coverage
- privacy-safe copied-local smoke validation
- update README command examples
- update `docs/session-doctor-design.md` current state and Phase 8 status
- record validation evidence and observed limitations
- final full quality gate

Clean commit point:

```text
Phase 8 trend behavior is documented and validated across all native adapters.
```

## Acceptance Criteria

Phase 8 is complete when:

- `trends` supports weekly and monthly aligned buckets
- the default window is 12 weeks anchored to latest matching timed data
- `latest_session_at` is exposed without wall-clock-dependent age output
- project and agent filters apply consistently across every trend section
- empty periods, outside-window sessions, and untimed sessions are visible
- top-level and sidechain outcomes are never mixed
- current, stale, and never-analyzed counts are explicit
- only current analyzer-version rows enter metrics and judgments
- `analyze --all` can restore current coverage deliberately with filters,
  force, opt-in artifacts, and explicit partial-failure behavior
- analysis coverage and every metric denominator are explicit
- all five Phase 6 score trends are exposed
- classification and risky-session rates use analyzed-session denominators
- outcome and neutral-signal directions use honest distinct status vocabulary
- directional judgments require explicit project scope and obey fixed sample,
  proportional bucket-density, coverage, and delta gates
- insufficient judgments include machine-readable reasons
- agent observations remain non-causal and cohort-specific
- `projects list` reports exact observed project hints without guessed roots
- recurring commands, failed tool fingerprints, and problematic files require at
  least two distinct top-level root families
- linked sidechain activity can contribute to its root family while orphan
  sidechains cannot establish recurrence
- recurring-pattern rows report top-level and sidechain counts separately
- terminal and JSON outputs preserve the privacy contract
- trend/project commands are read-only and write no artifacts or derived trend
  rows; batch analysis is an explicit separate mutation
- report, graph, project registry, task taxonomy, and model-assisted analysis
  remain deferred
- copied-local validation is recorded without retaining private content
- Ruff formatting and lint pass
- `ty check` passes
- all tests pass
- README, design documentation, and this plan match the implemented state

## Explicitly Deferred

- project registry, aliases, and VCS-root detection
- inferred project hierarchy or nested-path merging
- arbitrary `--since`/`--until` date ranges
- configurable trend gates or recurrence thresholds
- per-agent trend judgments or best-agent recommendations
- deterministic task taxonomy and agent fit by task type
- recursive parent/sidechain metric rollups beyond recurrence attribution
- token, cost, duration, model, provider, and version trends
- adapter parse-warning trends
- stored/materialized trend tables and cache invalidation
- Markdown reports and graph projection
- export commands
- LLM calls, embeddings, local ML, and network services

## Grilling Status

The interactive grilling pass resolved:

- strict current-analyzer-version comparability and explicit stale coverage
- an explicit filtered `analyze --all` recovery workflow
- latest-data anchoring with deterministic recency timestamps only
- a shared `0.10` materiality threshold without claiming statistical stability
- outcome-specific versus neutral signal direction vocabulary
- project-scoped judgments only
- 80% coverage and 15-point coverage-difference gates
- proportional non-empty-bucket density gates
- root-family recurrence with linked sidechain evidence
- stable but non-secret failed-output correlation IDs
- a four-PR implementation sequence

The post-grilling adversarial review returned no findings. This plan is approved
for implementation; Phase 8 code has not started.
