# Phase 9 Plan: Reports And Graph Projection

Status: complete.

This document is the implementation contract for Phase 9. It must complete an
interactive grilling pass and a blocker-only adversarial review before it is
approved for implementation.

## Goal

Phase 9 should turn the normalized single-session timeline and latest persisted
analysis into two deterministic diagnostic surfaces:

```text
normalized Codex / Claude / Pi session
  + latest persisted analysis compatibility
  + exact historical project recurrence context
  -> privacy-safe report read model
  -> terminal / Markdown / stable JSON report
  -> complete supported JSON evidence graph
```

The phase should answer:

- What happened structurally in this session?
- Is its latest analysis current, stale, or missing?
- Which scores, classifications, and evidence support a guarded interpretation?
- Which repeated requests, corrections, failures, file loops, or ending signals
  were detected?
- Does the session itself contribute to a recurring project-level pattern in
  the trailing historical window?
- How are messages, tool calls/results, commands, files, analysis findings, and
  evidence provenance related?

Clean completion point:

```text
One session can be inspected as a useful human report or a complete typed
evidence graph without implicit analysis, hidden writes, content leakage, or
causal claims stronger than the persisted evidence.
```

## Starting Point

Phases 1 through 8 provide:

- normalized Codex, Claude Code, and Pi sessions in DuckDB
- exact source-event provenance and deterministic row ordering
- explicit root/sidechain session topology
- current analyzer-version compatibility and deliberate `analyze --all`
  recovery
- five explainable scores, message/session features, and classifications
- canonical command/file identities and privacy-safe recurring fingerprints
- aggregate summaries, aligned trends, and root-family recurrence readers
- stable terminal/JSON rendering patterns and copied-local validation
- reserved `report` and `graph` CLI commands
- placeholder graph schemas/tables but no graph producer, reader, or writer
- 235 passing tests plus Ruff and `ty` quality gates

The report and graph must consume persisted normalized/current-analysis rows.
They must not rerun feature extraction or classification in a read path.

## Resolved Planning Decisions

### Phase Scope

- Deliver both single-session reports and graph projection in Phase 9.
- Treat the report as the primary human-facing result and the graph as the
  machine-readable evidence projection over the same read model.
- Keep both commands local-only and deterministic.
- Keep exact-session boundaries: selecting a root does not merge child
  sidechains, and selecting a sidechain does not merge its parent or siblings.
- Expose parent/child session IDs as topology references when present.
- Do not add family-merging modes in Phase 9.

### Read-Only Lifecycle

- Generate reports and graphs on demand from DuckDB.
- Do not write report artifacts by default or through a Phase 9 option; all
  formats go to stdout and callers may redirect explicitly.
- Do not persist graph projections or cache them.
- Do not auto-analyze stale or missing sessions.
- Remove the unused `graph_nodes` and `graph_edges` DuckDB tables from the
  pre-1.0 schema and bump the schema version. Existing databases must be rebuilt.
- Keep graph schemas as derived in-memory payload models, not storage models.

### Analysis Compatibility

- Select only the latest persisted analysis run, using the same ordering and
  analyzer-version contract as Phase 8.
- Report compatibility as `current`, `stale`, or `missing`.
- Use score, feature, classification, interpretation, and analysis-derived
  graph rows only when compatibility is `current`.
- For stale or missing analysis, emit a successful partial report/graph with
  normalized structural evidence, unavailable analysis sections, the observed
  stale version when applicable, and an action to run `analyze SESSION_ID`.
- Never present stale scores or classifications with only a warning.

### Disclosure And Privacy

- Default terminal, Markdown, JSON, and graph output is message-text-free.
- Add report-only `--show-text`; it includes text only for messages selected as
  report evidence, never the full transcript.
- Text disclosure is authorized only by exact persisted message IDs: a
  `MessageFeature.message_id`, a repeat-match `matched_message_id`, or an
  explicit `message_ids` list in a displayed current-run session feature.
- A classification/source-event ID or mere source-event co-location never
  authorizes message-text disclosure.
- `--show-text` does not disclose tool arguments, tool output, command output,
  diffs, edit bodies, or unredacted commands.
- All report formats explicitly expose whether evidence text was included.
- Graph output never accepts `--show-text` and never includes message text.
- Display only allowlisted metadata. Never serialize arbitrary normalized,
  feature-evidence, classification, or native metadata dictionaries.
- Show command examples only through `command_display` plus
  `redact_command_for_display`.
- Derive tool-failure correlational IDs from native output hashes; never expose
  native hashes.
- Prefer project-relative file paths, then normalized relative paths, then
  home-redacted absolute paths. Never expose file content hashes.
- Marker names may be shown; stored matched phrases remain hidden unless their
  evidence message is disclosed by `--show-text`.
- Treat IDs and fingerprints as correlational identifiers, not secrets.

### Interpretation Language

- Emit guarded, evidence-backed observations rather than one root-cause verdict.
- Use language such as `consistent_with`, `observed`, and `may_warrant_review`.
- Never claim that a user, agent, tool, file, or project caused an outcome.
- Never rank agents, infer intent, diagnose sentiment, or claim statistical
  calibration.
- Keep observations separate when evidence supports different interpretations.
- Provide deterministic review actions, not claims that a specific fix will
  resolve the problem.

### Project Context

- Include project recurrence context by default only when the selected session
  has an exact stored `project_path` or `cwd` hint and a usable start time.
- Identify the scope source as `session_project_path` or `session_cwd`.
- Do not infer a VCS root, merge nested paths, or search parent directories.
- Derive an evidence cutoff from the latest timestamp belonging to the selected
  session's normalized rows, falling back to `ended_at`, then `started_at`.
- Use a fixed trailing 12-week window beginning on the Monday 11 weeks before
  the cutoff's week and ending inclusively at the evidence cutoff. Exclude
  sessions and pattern events after that instant.
- Candidate roots and contributing sessions require non-null `started_at`
  within `[window_start, evidence_cutoff]`. Pattern events require their own
  non-null timestamp in that range; never inherit session time.
- A pre-cutoff session contributes only events at or before the cutoff. Count
  untimed and after-cutoff candidate sessions/events as temporal exclusions.
- Resolve each candidate's preferred observed hint as non-empty `project_path`,
  otherwise non-empty `cwd`; match that one value to the selected preferred
  hint or descendants. Do not use the broader Phase 8 filter OR across both
  candidate columns.
- Reuse Phase 8 command, tool-result, file identity, root-family, malformed
  topology, and two-distinct-root-family recurrence contracts.
- For problematic-file recurrence, only sessions whose latest analysis is
  current may establish problematic/risky eligibility. Count stale and missing
  candidate analyses separately; never reuse their classifications or scores.
- If the selected session's analysis is not current, the problematic-files
  subsection is unavailable with `selected_analysis_not_current`; command and
  tool-result recurrence may still be available from normalized rows.
- Include only recurring groups to which the selected session contributes.
- For sidechains, contribution is attributed to the valid top-level root family,
  while evidence counts remain split by top-level and sidechain sessions.
- If the session is untimed, has no project hint, or belongs to malformed
  topology, return an explicit unavailable reason rather than broadening scope.
- A selected sidechain whose root's preferred hint is outside the selected
  scope returns `root_outside_project_scope`; do not include or broaden to the
  root silently.

### Report Size

- Add positive `--limit`, default 10.
- Apply the limit independently to ranked/detail report sections.
- Every bounded section reports `total`, `displayed`, and `omitted` counts.
- Sorting occurs before limiting and is deterministic under ties.
- Summary metrics and classifications are not silently truncated.
- Graph projection is complete for supported rows and never uses the report
  limit or silently truncates nodes/edges.

### Graph Implementation

- Build a direct typed projection with Pydantic models; do not use NetworkX.
- Remove the currently unused NetworkX dependency.
- Use a directed multigraph payload because multiple evidence relations may
  connect the same nodes.
- Construct sorted nodes/edges directly rather than adopting a library-specific
  node-link JSON shape.
- Include every supported normalized row in the exact session, plus current
  analysis nodes when available.
- Report counts for excluded unsupported row kinds and unresolved references.
- Add no new NLP, similarity, causality, or temporal inference rules in Phase 9.

## CLI Contract

```bash
session-doctor report SESSION_ID \
  [--db PATH] \
  [--format terminal|markdown|json] \
  [--limit N] \
  [--show-text]

session-doctor graph SESSION_ID \
  [--db PATH] \
  [--format json]
```

Rules:

- `report` defaults to terminal format, limit 10, and no message text.
- `graph` defaults to JSON and rejects every other format.
- Both require an existing current-schema database and exact session ID.
- A missing session returns exit 1 with the stable safe message
  `Session not found: SESSION_ID`.
- Invalid format/limit combinations return exit 2.
- JSON stdout contains only JSON. Markdown stdout contains only Markdown.
- Neither command creates a database, analysis run, artifact, report file, or
  graph row.

Examples:

```bash
session-doctor report SESSION_ID
session-doctor report SESSION_ID --format markdown > report.md
session-doctor report SESSION_ID --format json
session-doctor report SESSION_ID --show-text
session-doctor graph SESSION_ID
```

## Shared Diagnostic Read Model

Add one store read path that loads, in a consistent read-only snapshot:

- the selected normalized session bundle
- parent/child session references without loading their content
- latest analysis run and compatibility
- current-run message features, session features, and classifications
- exact-session project recurrence matches and context availability

The read model must keep normalized records and analysis rows distinct. It
should expose typed indexes by record ID/source-event ID for payload builders,
but should not leak DuckDB rows or generic dictionaries into output code.

Load the diagnostic model and recurrence context through one DuckDB read-only
connection inside one explicit transaction. Projection and rendering occur
after that snapshot is loaded. Do not issue independent store reads that could
observe different concurrent database states.

Required compatibility fields:

```text
status: current | stale | missing
current_analyzer_version
observed_analyzer_version: string | null
analysis_run_id: string | null
action: string | null
```

The selected latest analysis run is either consumed as a whole or excluded as a
whole. Never join features/classifications from different runs.

## Report Contract

### Stable Top-Level Shape

```json
{
  "schema_version": 1,
  "session": {},
  "privacy": {},
  "analysis": {},
  "summary": {},
  "scores": [],
  "classifications": [],
  "evidence": {},
  "ending": {},
  "project_context": {},
  "observations": [],
  "review_actions": [],
  "limitations": []
}
```

Keys remain present for partial reports. Unavailable scalar values are `null`;
unavailable collections are empty arrays plus an explicit reason. Do not omit a
section merely because evidence is absent.

Nested objects use these stable shapes:

```text
session:
  session_id, agent, is_sidechain, parent_session_id, child_session_ids,
  started_at, ended_at, project_hint, project_hint_source, model_provider,
  model, agent_version
privacy:
  message_text_included, disclosure_scope
analysis:
  status, current_analyzer_version, observed_analyzer_version,
  analysis_run_id, action
summary:
  raw_events, messages, tool_calls, tool_results, command_runs,
  file_activities, parse_warnings
score row:
  name, value, component_values, component_weights, contributions,
  source_event_ids, unresolved_source_event_ids
classification row:
  classification_id, label, score, confidence, evidence_summary,
  source_event_ids, unresolved_source_event_ids
bounded evidence section:
  status, reason, total, displayed, omitted, items
observation/review-action/limitation row:
  code, summary, evidence_ids
```

All arrays have a documented deterministic order. `status` is
`available|unavailable`; unavailable sections require a machine-readable
`reason`. Numeric values remain JSON numbers and unavailable numeric values are
`null`, never string sentinels.

Evidence items are strict discriminated unions:

```text
message_signal:
  evidence_id, feature_id, feature_name, message_id, source_event_id, role,
  timestamp, score, matched_message_id, matched_source_event_id,
  similarity_score, text
command_failure:
  evidence_id, command_run_id, source_event_id, command_display, exit_code,
  fingerprint
tool_failure:
  evidence_id, tool_result_id, tool_call_id, source_event_id, tool_name,
  output_length, fingerprint
failure_group:
  evidence_id, group_type, fingerprint, occurrence_count, record_ids,
  source_event_ids
file_loop:
  evidence_id, display_path, path_resolution, edit_count,
  file_activity_ids, source_event_ids
classification_reference:
  evidence_id, classification_id, source_event_id, resolved_node_type,
  resolved_node_id
```

`text` is always `null` without `--show-text`. Fields unsupported by a specific
persisted row remain `null`; they are not omitted or filled from arbitrary
metadata. Native repeated-failure keys/hashes never populate `fingerprint`.

Emit one `classification_reference` item per
`(classification_id, evidence_event_id)` occurrence. Derive its ID from that
pair. Resolve only to the exact raw-event node; an absent event leaves both
resolved fields null and increments the unresolved-reference count by one.
Classification metadata feature names do not authorize report items or graph
edges. Graph `supports_classification` edges therefore run only from resolved
raw-event nodes to the classification node.

Evidence arrays use these fixed orders:

- message signals: source record index nulls last, message timestamp nulls last,
  message ID, feature name
- command/tool/file rows: source record index nulls last, event timestamp nulls
  last, record ID
- failure groups: occurrence count descending, first source record index,
  group type, public fingerprint
- classification references: classification display order, source record index
  nulls last, source-event ID
- observations and review actions: fixed template priority, then code
- limitations: fixed severity priority, then code

Unresolved counts measure reference occurrences, not unique missing IDs. The
same missing event referenced by two classifications counts twice because it
would have produced two distinct relations.

### Session Metadata

Expose:

- session ID and agent
- top-level/sidechain status
- parent session ID and sorted direct child session IDs
- start/end timestamps without wall-clock age calculations
- redacted project/CWD display hint and its source
- model/provider/agent version when present
- structural counts for messages, tool calls/results, commands, file
  activities, parse warnings, and raw events
- parse-warning count and allowlisted warning codes, not raw native payloads

Do not expose source paths, native session IDs, arbitrary session metadata, or
analysis artifact paths.

### Analysis Sections

For current analysis:

- expose all five Phase 6 scores in fixed order with values and allowlisted
  component values/weights/contributions
- expose classifications in deterministic risk/score/label order
- preserve classification confidence and evidence summaries
- resolve every exposed evidence event ID to normalized provenance when
  possible
- count and surface unresolved evidence references

For stale/missing analysis, scores, classifications, analysis evidence,
observations, and derived review actions remain unavailable rather than empty
healthy results.

### Evidence Sections

Use persisted current-run feature evidence; do not recompute detectors.

Sections:

```text
repeated_requests
corrections
frustration_markers
scope_boundaries
ambiguity_markers
stop_or_pause_markers
command_failures
tool_failures
repeated_failures
repeated_file_edits
classification_evidence
```

Each row includes stable internal IDs, source-event IDs when available,
detector/feature name, score/count, and an allowlisted structural summary.
Message rows include role, timestamp, and text only under `--show-text`.

Important evidence rules:

- `repeat_request_similarity` may link the detected message to its persisted
  best prior match and similarity score. It does not establish intent or cause.
- marker rows show normalized detector family; matched phrase fragments are not
  output independently.
- repeated-failure groups expose record counts/types and opaque fingerprints,
  not native hash keys.
- one record may belong to multiple failure groups; totals do not imply unique
  failed records unless explicitly named as distinct counts.
- file loops resolve to persisted file activities by canonical/project-relative
  identity where possible; path-only feature evidence is not treated as a
  foreign key.
- unresolved evidence IDs remain visible as counts and limitations.

### Ending State

Report:

- stored final-answer/resolution features when current
- unresolved-ending signal and its stored evidence categories
- late failed-command and parse-warning references when resolvable
- whether ending interpretation is unavailable due to stale/missing analysis

Do not independently recreate temporal ending predicates in the report reader.

### Observations And Review Actions

Generate observations from fixed templates over persisted current findings.
Examples of allowed meaning:

```text
Repeated request evidence is present across N messages.
The same failure fingerprint occurred N times.
The session ended with unresolved-ending evidence after correction markers.
This session contributes to a project recurrence spanning N root families.
```

Review actions are deterministic invitations to inspect evidence, such as
checking the first/last repeated failure or clarifying a repeated scope
boundary. They must not prescribe agent choice, assert a root cause, or promise
an outcome.

## Historical Project Recurrence Contract

For a timed session with a project hint:

1. Resolve the exact session's valid top-level root family.
2. Calculate the selected-session evidence cutoff from its latest normalized
   timestamp, then `ended_at`, then `started_at`.
3. Begin the window on Monday 00:00:00 of the cutoff's week minus 11 weeks.
   Require candidate roots and contributing sessions to start in the range and
   each contributing event to have its own timestamp in the range. Exclude
   untimed or later candidates/events with explicit counts.
4. Resolve each session's preferred observed hint (`project_path`, else `cwd`)
   and scope it to the selected preferred hint or descendants.
5. Apply Phase 8 recurrence identity and malformed-topology rules.
6. Require the selected sidechain's root preferred hint to remain in scope.
7. Retain groups with at least two distinct top-level root families.
8. Return only groups containing the selected session as an evidence member.

The project-context payload includes:

```text
status: available | unavailable
reason: null | no_project_hint | untimed_session | orphan_parent | cycle |
        cross_agent_parent | root_outside_project_scope
scope_path
scope_source
window_start
evidence_cutoff
family_exclusions
temporal_exclusions
problematic_file_analysis_exclusions
failed_commands
failed_tool_results
problematic_files
```

`failed_commands`, `failed_tool_results`, and `problematic_files` are bounded
sections with the normal `status/reason/total/displayed/omitted/items` shape and
the report `--limit`. Their strict item shapes are:

```text
failed command:
  pattern_id, command_display, event_count, selected_session_event_count,
  session_count, root_family_count, top_level_session_count,
  sidechain_session_count, agents, first_at, most_recent_at
failed tool result:
  pattern_id, tool_name, fingerprint, event_count,
  selected_session_event_count, session_count, root_family_count,
  top_level_session_count, sidechain_session_count, agents, first_at,
  most_recent_at
problematic file:
  pattern_id, display_path, event_count, selected_session_event_count,
  session_count, root_family_count, top_level_session_count,
  sidechain_session_count, agents, first_at, most_recent_at
```

Derive `pattern_id` from the private canonical identity but expose only the
stable public ID. Sort recurrence rows by root-family count, session count, and
event count descending; then most-recent timestamp descending nulls last; then
safe display key and pattern ID ascending. Sort `agents` and all exclusion-count
keys lexicographically. Do not expose Phase 8's `example_session_id`.

The selected session may contribute through a sidechain, but its root must be
valid and within the historical window. No directional trend judgment is added
to a single-session report.

## Graph Contract

### Stable Top-Level Shape

```json
{
  "schema_version": 1,
  "session_id": "...",
  "analysis": {},
  "privacy": {"message_text_included": false},
  "directed": true,
  "multigraph": true,
  "counts": {},
  "excluded": {},
  "nodes": [],
  "edges": []
}
```

Nested graph objects use these stable shapes:

```text
analysis: same compatibility fields as report analysis
counts: nodes, edges, nodes_by_type, edges_by_type
excluded: rows_by_type, unresolved_references
node common fields: node_id, node_type, label, source_event_id, timestamp
edge fields: edge_id, edge_type, source_node_id, target_node_id, confidence,
             source_event_id
```

Nodes are strict discriminated Pydantic unions. Type-specific allowlisted fields
are defined by the node model rather than a generic metadata/data dictionary.
Edges have no arbitrary metadata dictionary.

### Node Types

Phase 9 supports:

```text
session
session_reference
raw_event
message
tool_call
tool_result
command_run
file_activity
file
failure_group
message_feature
session_feature
classification
parse_warning
```

Rules:

- exactly one session node anchors the projection
- metadata-only raw-event nodes expose event ID, record index, native event type,
  and timestamp, but never payload hash or arbitrary metadata
- parent/direct-child references use `session_reference` nodes containing only
  session ID, relationship, agent, sidechain flag, and existence status
- each normalized message, tool call/result, and command run has an instance
  node
- each file activity has an instance node that targets a deduplicated file node
- file nodes deduplicate only through canonical/project-relative identity;
  unresolved file activities get session-local nodes
- parse-warning nodes expose code/severity only
- failure-group nodes avoid quadratic all-pairs `same_failure_as` edges
- feature/classification nodes exist only for current analysis
- model-usage rows are counted as excluded in Phase 9; they add no diagnostic
  relation yet

### Edge Types

Phase 9 supports conservative relations:

```text
contains
parent_message
derived_from
has_tool_result
runs_command
targets_file
member_of_failure_group
repeats_request_of
detected_in
contributes_to_score
supports_classification
has_warning
parent_session_reference
child_session_reference
```

Relation rules:

- edge direction is fixed:
  - `contains`: selected session -> exact-session node
  - `parent_message`: child message -> parent message
  - `derived_from`: normalized/analysis node -> raw event
  - `has_tool_result`: tool call -> tool result
  - `runs_command`: tool call -> command run
  - `targets_file`: file activity -> file
  - `member_of_failure_group`: failure instance -> failure group
  - `repeats_request_of`: repeated message -> matched prior message
  - `detected_in`: message feature -> exact message
  - `contributes_to_score`: component session feature -> score feature
  - `supports_classification`: resolved raw event -> classification
  - `has_warning`: matched raw event -> parse warning, otherwise session ->
    warning
  - `parent_session_reference`: selected session -> parent reference
  - `child_session_reference`: selected session -> child reference
- explicit normalized foreign keys use confidence `1.0`
- normalized and analysis nodes connect to raw-event provenance through
  `derived_from`; source-event co-location never becomes an all-pairs relation
- repeat-request edges come only from persisted best-match message-feature
  evidence and retain its similarity score
- analysis provenance edges resolve only within the selected current run
- classification metadata does not create feature-to-classification edges
- missing targets do not produce dangling edges; they increment unresolved
  reference counts
- parent/child session references terminate at lightweight `session_reference`
  nodes; no related-session content is loaded
- repeated failure instances connect to a group node rather than every other
  instance
- join parse warnings to raw events only on exact `(source_id, record_index)`.
  A non-null warning index must resolve to at most one event; null, unmatched,
  or ambiguous indices use session -> warning and increment unresolved
  provenance.

Explicitly defer these unsupported/causal edges:

```text
asks_for
responds_to (unless an explicit normalized parent ID exists)
edits (when only source-event co-occurrence exists)
fails_with raw error text
corrects a specific assistant response
causes_retry
references_prior_attempt
caused_classification
caused_outcome
```

### IDs, Ordering, And Metadata

- Derive graph node/edge IDs with `stable_id` from semantic identity, never list
  position alone.
- Keep IDs stable across repeated projection of unchanged normalized/current
  analysis rows.
- Sort nodes by fixed node-type order, source record order/timestamp, then ID.
- Sort edges by fixed edge-type order, source node ID, target node ID, then ID.
- Use explicit metadata fields per node/edge type; do not expose generic
  `metadata` maps in JSON.
- Validate that every edge endpoint exists, every ID is unique, and all rows
  belong to the selected exact session or an explicit topology-reference node.

Strict node models expose only these type-specific fields in addition to common
node fields:

```text
session: agent, is_sidechain, started_at, ended_at
session_reference: referenced_session_id, relationship, agent, is_sidechain,
                   exists
raw_event: record_index, native_event_type
message: message_id, role, text_length, content_block_types
tool_call: tool_call_id, tool_name
tool_result: tool_result_id, tool_call_id, is_error, output_length, fingerprint
command_run: command_run_id, command_display, exit_code, started_at, ended_at
file_activity: file_activity_id, operation, path_resolution
file: display_path, path_resolution
failure_group: group_type, fingerprint, occurrence_count
message_feature: feature_id, feature_name, feature_value, score
session_feature: feature_id, feature_name, feature_value, score
classification: classification_id, classification_label, score, confidence,
                evidence_summary
parse_warning: warning_id, code, severity, record_index
```

Message nodes never contain a `text` field. Tool-result/command/file nodes never
contain native hashes, raw command text, raw paths, output, arguments, or
content. Fingerprints use Phase 8's public correlational-ID derivation.

The fixed node-type order is:

```text
session, session_reference, raw_event, message, tool_call, tool_result,
command_run, file_activity, file, failure_group, message_feature,
session_feature, classification, parse_warning
```

The fixed edge-type order is:

```text
contains, parent_message, derived_from, has_tool_result, runs_command,
targets_file, member_of_failure_group, repeats_request_of, detected_in,
contributes_to_score, supports_classification, has_warning,
parent_session_reference, child_session_reference
```

## Terminal And Markdown Rendering

All report renderers consume the stable report payload/read model; they do not
query DuckDB or reinterpret evidence.

Terminal sections use Rich tables in this order:

1. report summary and privacy mode
2. session metadata and analysis compatibility
3. scores and classifications
4. bounded evidence sections
5. ending state
6. historical project recurrence
7. guarded observations and review actions
8. limitations

Markdown uses stable headings, tables, and bullets in the same semantic order.
It contains no ANSI escapes and ends with one newline. Empty/unavailable
sections remain explicit. Markdown and JSON must not contain terminal-only
database paths or styling labels.

## Architecture

Expected responsibility split:

```text
src/session_doctor/
  diagnostic_models.py       shared typed report/graph read contracts
  report_payload.py          privacy-safe stable report conversion
  report_renderers.py        terminal and Markdown rendering
  graph_projection.py        direct typed node/edge construction
  graph_payload.py           stable graph JSON conversion
  cli.py                     thin command boundaries
  cli_options.py             Phase 9 option validation
  privacy.py                 shared path/fingerprint display helpers
  store/
    diagnostic_readers.py    exact-session/latest-analysis snapshot
    recurrence_context.py    selected-session historical recurrence
```

Exact filenames may be simplified during implementation, but load, projection,
payload, and rendering responsibilities must not collapse into one CLI module.
Reuse existing aggregate SQL, analysis compatibility, topology resolution,
command redaction, and canonical file identity helpers rather than copying them.

## Schema And Dependency Changes

- Bump `SCHEMA_VERSION` from 3 to 4.
- Remove `graph_nodes` and `graph_edges` from `TABLE_NAMES`, create statements,
  source-replacement deletes, database-info expectations, and design docs.
- Do not add report/graph persistence tables.
- Keep/revise `GraphNode` and `GraphEdge` as strict projection schemas and add a
  typed graph report model.
- Remove NetworkX from runtime dependencies and the lockfile.
- Document that pre-1.0 databases must be rebuilt after the schema change.

## Error And Empty-State Contract

- Missing/invalid/incompatible databases reuse stable existing CLI errors.
- Missing session is a safe exit-1 error.
- Stale/missing analysis is successful partial output, not an exception.
- Empty normalized collections produce explicit zeros/arrays.
- Unresolved evidence IDs and graph references are counted and described, not
  guessed or silently dropped.
- Untimed/no-project sessions produce explicit unavailable project context.
- Malformed parent topology uses the Phase 8 exclusion vocabulary.
- `--show-text` with no evidence-linked messages succeeds with
  `message_text_included=true` and an empty disclosed-message set.
- Rendering failure must not mutate any state.

## Test Plan

### Store And Compatibility

- exact session bundle and topology references load in deterministic order
- latest current/stale/missing analysis selection
- no cross-run feature/classification joins
- partial read models for stale/missing analysis
- historical recurrence uses exact project hint, trailing session-time window,
  selected-session membership, and Phase 8 family thresholds
- untimed, unknown-project, orphan, cycle, and cross-agent cases are explicit

### Report Payload

- stable keys/types for current, stale, and missing analysis
- all five scores and every classification remain explainable
- evidence IDs resolve deterministically and unresolved counts remain visible
- repeated requests, markers, failures, file loops, endings, and recurrence
  preserve their actual persisted semantics
- per-section totals/displayed/omitted obey `--limit`
- guarded wording contains no causal or agent-ranking claims
- generic metadata/evidence serialization is impossible by construction
- text disclosure uses exact persisted message IDs and never source-event
  co-location alone

### Privacy

- message sentinels absent by default in terminal/Markdown/JSON/graph
- only evidence-linked messages appear under report `--show-text`
- non-evidence transcript text remains absent with `--show-text`
- native command text/secrets, native output hashes, tool arguments/output,
  diffs, content hashes, source paths, and arbitrary metadata remain absent
- command/path display helpers preserve useful safe context
- JSON and Markdown privacy modes are machine-visible

### Graph Projection

- complete supported normalized row coverage, including raw-event anchors, with
  no silent limit
- current analysis nodes absent for stale/missing compatibility
- exact-session boundary under root/nested-sidechain fixtures
- stable IDs and ordering across repeated projection
- unique nodes/edges and resolvable endpoints
- direct link, raw-event provenance, tool-result, command, file, repeat-request,
  failure-group, feature, classification, warning, and topology-reference edges
- no quadratic repeated-failure edge explosion
- no unsupported causal edge types
- explicit excluded/unresolved counts

### CLI And Rendering

- report terminal, Markdown, and JSON formats
- graph JSON and invalid-format rejection
- positive report limit validation and section truncation disclosure
- stable missing-database/schema/session errors
- JSON stdout contains JSON only; Markdown stdout contains Markdown only
- report/graph commands create no artifacts, analysis runs, or database rows
- existing analyze/summary/trends/projects behavior remains unchanged

### Cross-Adapter End To End

- ingest native Codex, Claude, and Pi fixtures
- ingest Claude root/nested-sidechain topology fixtures
- analyze selected sessions
- report each adapter in all required formats
- graph each adapter and a linked sidechain
- verify normalized entity coverage and adapter-neutral node/edge vocabulary
- verify project recurrence only when two root families satisfy the threshold

## Copied-Local Validation

After fixture behavior is stable:

1. Copy isolated recent completed Codex, Claude, and Pi sessions into temporary
   roots; include a linked sidechain when available.
2. Ingest and analyze only the copies in a temporary database.
3. Run reports in default, Markdown, JSON, and evidence-only `--show-text`
   modes without retaining output.
4. Run graph JSON and validate endpoint/ID/type/count invariants.
5. Confirm report/graph reads create no database or artifact mutations.
6. Manually adjudicate whether each exposed observation is supported by its
   referenced evidence and whether wording avoids causal overreach.
7. Record only adapter/parser versions, structural counts, compatibility,
   report section counts, graph node/edge types/counts, unresolved/excluded
   counts, privacy sentinel results, supported/unsupported observation counts,
   and observed limitations.
8. Do not record source paths, project names, messages, commands, tool output,
   arguments, diffs, native hashes, file paths/content, or report prose.
9. Remove copied sources, databases, redirected outputs, and scripts.

Completion requires zero retained privacy sentinels, zero dangling graph edges,
zero unresolved implementation invariants, and correction of unsupported report
wording found in the small adjudication. The sample does not justify broad
false-positive/false-negative or calibration claims.

## Delivery Plan

Land this approved plan before implementation, then use four reviewable PRs.
Every PR must pass repeated review/fix cycles until the reviewer returns
`NO FINDINGS`.

### PR 1: Diagnostic Read Contract And Schema Cleanup

Deliverables:

- shared exact-session diagnostic read model
- latest-analysis compatibility and current-run loading
- topology references and unresolved-evidence indexes
- schema version 4 without persistent graph tables
- remove NetworkX
- shared privacy/path/fingerprint helpers
- focused store, schema, dependency, and compatibility tests

Clean completion point:

```text
Reports and graphs consume one typed read-only source without stale joins or
unused graph persistence.
```

### PR 2: Single-Session Reports

Deliverables:

- report models and stable JSON payload
- evidence sections and ending state
- exact historical project recurrence context
- guarded observations and review actions
- `--show-text` evidence-only disclosure
- terminal and Markdown renderers
- `report` CLI implementation and `--limit`
- focused behavior, rendering, compatibility, and privacy tests

Clean completion point:

```text
A session has a useful privacy-safe report even when analysis availability is
partial and no read command mutates state.
```

### PR 3: Complete Conservative Graph Projection

Deliverables:

- strict graph models and schema version
- complete supported normalized node projection
- current-analysis evidence nodes
- conservative direct/provenance/failure-group relations
- deterministic IDs, ordering, excluded/unresolved counts, and validation
- `graph` JSON CLI implementation
- cross-adapter, sidechain, scale, and privacy tests

Clean completion point:

```text
The exact session has a complete deterministic evidence graph without causal
invention, dangling references, hidden truncation, or persistence.
```

### PR 4: Native Validation And Completion

Deliverables:

- three-adapter report/graph end-to-end fixture flow
- Claude linked-sidechain validation
- privacy-safe copied-local structural smoke and small semantic adjudication
- README command examples
- design/Phase 9 status and current-package updates
- Phase 9 validation evidence and observed limitations
- final full quality gate

Clean completion point:

```text
Phase 9 report and graph behavior is documented and validated across all native
adapters without retaining private content.
```

## Acceptance Criteria

Phase 9 is complete when:

- `report` supports terminal, Markdown, and stable JSON
- `graph` emits stable JSON nodes/edges
- both commands are exact-session, local-only, deterministic, and read-only
- stale/missing analysis produces explicit partial output without auto-analysis
- only current-version analysis contributes derived findings
- report evidence references normalized event IDs and exposes unresolved counts
- default output contains no message text
- `--show-text` exposes only evidence-linked message text
- commands, paths, hashes, arguments, output, diffs, content, and metadata obey
  the privacy allowlist
- report sections expose totals/displayed/omitted under a positive limit
- project recurrence uses exact observed identity, a trailing historical window,
  selected-session membership, and Phase 8 root-family thresholds
- interpretations are guarded, deterministic, and non-causal
- graph projection includes every supported exact-session row without truncation
- graph edges have valid endpoints, stable IDs/order, and conservative semantics
- unsupported rows/references are explicit
- root and sidechain content never mix implicitly
- graph tables and NetworkX are removed cleanly under pre-1.0 schema rules
- copied-local validation retains no private content
- native semantic review finds no unsupported retained wording
- report/graph reads write no rows or artifacts
- existing CLI behavior remains green
- Ruff formatting/lint, `ty check`, all tests, and `git diff --check` pass
- README, design documentation, this plan, and validation evidence match the
  implemented state

## Explicitly Deferred

- report or graph persistence/caching
- automatic analysis from report/graph commands
- full transcript replay or raw-content ingestion/output
- Graphviz, DOT, HTML, graphical UI, and interactive graph exploration
- family-merged reports/graphs
- causal graph edges and new semantic inference rules
- goal/action/error ontologies not already represented deterministically
- graph algorithms, centrality, community detection, or learned embeddings
- arbitrary project-context windows and configurable recurrence thresholds
- report files/artifacts and broad export commands
- project registry, VCS-root inference, task taxonomy, and agent ranking
- LLM calls, local ML, cloud services, MCP, and agent wrappers

## Grilling Status

Resolved interactively:

- both reports and graph projection remain in Phase 9
- graph projection is on-demand and read-only
- graph semantics are conservative and use only persisted deterministic evidence
- stale/missing analysis produces an explicit partial result
- `--show-text` is evidence-only, not transcript replay
- every report format goes to stdout
- exact historical project recurrence is included by default when available
- interpretations remain guarded rather than causal
- graphs are complete for supported rows and untruncated
- root and sidechain session scopes stay exact
- direct typed projection replaces NetworkX
- unused graph persistence tables are removed under a schema bump
- report evidence sections use configurable positive limits
- completion includes copied-local structural validation and small private
  semantic adjudication without retaining content

The final planning review and every implementation PR review returned
`NO FINDINGS`. Phase 9 is implemented and validated; see
`docs/phase-9-validation.md`.
