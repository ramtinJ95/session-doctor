# Deterministic Analysis v2 Plan

Status: accepted architecture contract. Implementation authorized 2026-07-13.

## Goal

Redesign Session Doctor so it can classify coding-agent work and surface
improvement opportunities deterministically, locally, and without LLM or
network calls in the production analysis path.

The same rules should support future model evaluation without turning
uncontrolled historical sessions into causal model rankings.

The current pipeline is strong at normalization and structural measurement but
conflates several different questions:

- how much work occurred;
- how much friction was observed;
- whether a failure was later recovered;
- whether a response was delivered;
- whether validation or an operation was observed;
- whether evidence is final or comes from a still-growing transcript;
- whether an episode should be reviewed for a specific reason.

V2 separates those questions and makes task episodes, rather than whole native
transcripts, the primary analysis unit.

## Grilled Product Decisions

### Analysis And Classification

- Task episodes are primary; native sessions are rollups.
- Ambiguous boundaries merge rather than inventing a new task.
- A narrow explicit `new task` or `separate question` marker may start a new
  episode without prior interaction closure.
- Episodes never span native session files. Cross-session continuation is a
  candidate relation, not an automatic merge.
- Subagent episodes contribute to their parent episode's delegated metrics, but
  child timelines remain separately ordered and are never timestamp-interleaved
  into the parent timeline.
- Judgment-heavy labels such as `user_stuck`, `agent_fit_risk`,
  `task_too_large`, and `healthy` are removed.
- Findings describe observable patterns and distinguish burden, recovery, and
  unresolved evidence.
- Broad goal similarity remains a high-recall relation, but cannot trigger an
  attention finding without unresolved evidence.
- Initial request constraints do not count as instruction-change friction;
  only later revisions and corrections do.
- `broad_change_surface` is a measurement facet, not a binary finding.
- Corrections are recorded as burden; only unresolved correction cycles become
  attention findings.
- A later success recovers a failure only when it shares the same normalized
  target identity or another explicit deterministic relation.
- A fail-edit-pass validator sequence is a resolved validation cycle, not an
  unresolved tooling blocker.
- Approval and safety denials are separate policy-gate evidence, not ordinary
  tool failures.
- Action outcomes first separate `success`, `ordinary_failure`, `policy_gate`,
  `timeout_or_cancelled`, and `unknown`. Only ordinary failures receive the
  narrow reasons command-not-found, permission, network, test/assertion,
  tool-reported failure, or unknown. Policy gates and cancellations never enter
  ordinary-failure counts.

### Results And Validation

- The combined response/work record is named `ObservedEpisodeResult`, not task
  success or completion status.
- Response delivery and work evidence are separate.
- Missing final output never implies abandonment.
- Explicit replacement without closure produces `interrupted_unknown`, not
  stopped or failed.
- Explicit user acceptance increases delivery certainty but does not replace
  objective validation.
- A passing validator with unknown changed-scope linkage is evidence only; it
  does not improve work state.
- Built-in validators come first. Project-specific validator configuration is
  deferred.
- A recognized target-linked deployment, publish, or configuration command may
  produce `operation_success_observed`, but not full task success.
- Inspection/research episodes may produce `evidence_delivery_observed`, which
  does not claim factual correctness.
- If an adapter lacks an explicit final-response phase, response delivery may
  be inferred only from a settled snapshot whose last substantive assistant
  message has no pending tool work. Support is marked inferred.
- Still-growing snapshots receive complete analysis of captured evidence but
  provisional result state and are excluded from finalized outcome rates.

### Facets And Rankings

- There is no composite risk score or overall review queue.
- Facets emit raw components plus fixed ordinal tiers, not continuous 0-1
  pseudo-precision.
- Optional cohort percentiles are descriptive and never replace fixed tiers.
- Total friction burden and friction density are separate rankings.
- Correction/repetition densities use user-turn denominators; failure/retry
  densities use eligible action denominators. Metadata events and elapsed time
  are not universal denominators.
- Change surface remains independent from unresolved progress.
- Token use, cost, active execution time, and elapsed time are separate
  efficiency measurements. They are never combined into one score or folded
  into friction.
- Active and elapsed time remain separate because elapsed time includes user
  idle time and resumed sessions.
- Unknown cumulative-versus-incremental usage semantics make aggregation
  unavailable rather than guessed.

### Snapshots, History, And Determinism

- Ordinary ingestion retains exact raw source snapshots for parser time travel.
- Raw snapshots are compressed BLOBs in DuckDB, deduplicated by content hash.
- Multi-file native sessions use a manifest of exact per-file snapshots with
  capture order/times; no atomic directory-snapshot claim is made.
- Unchanged recaptures reuse one content-addressed blob.
- Growing files initially create new full blobs. Chunk-level prefix
  deduplication is deferred until measured growth justifies it.
- Snapshot pruning is explicit only. It blocks referenced history unless a
  force operation lists and confirms dependent loss, then runs `CHECKPOINT` to
  reclaim space where DuckDB can do so.
- Raw snapshot tables are durable across future pre-1.0 schema changes.
  Normalized and derived tables may be rebuilt from retained bytes.
- Normalized outputs are retained by parser version.
- V2 analysis outputs are retained by rule/version identity.
- Existing v1 analysis results may be deleted when v1 is replaced; historical
  analysis retention begins with v2.
- New snapshots use current parser/rules by default. Historical snapshots are
  reprocessed only by an explicit additive command.
- Determinism means semantic determinism in a declared environment: identical
  snapshot, config, and component versions produce the same ordered analytical
  values. Cross-runtime byte-identical serialization is not required.
- Default commands use the latest snapshot and expose its lifecycle. Finalized
  aggregates include only eligible settled snapshots.

### Evaluation And Model Comparison

- Production analysis never calls an LLM or external API.
- Evaluation annotation may use external LLM judges through an export/import
  protocol; Session Doctor itself does not call provider APIs.
- The default panel is three distinct judge models.
- The model being evaluated cannot judge its own sessions.
- Judges are always blind to Session Doctor predictions.
- Automatic consensus requires unanimity. Any 2-1 split gets human review.
- Twenty percent of unanimous pilot cases receive random human audit; the rate
  may change only after measured judge reliability.
- Human-unresolved cases may have an ambiguous allowed answer rather than a
  forced label or removal.
- The first pilot is a stratified challenge set of 20-30 episodes. It makes no
  statistical quality claim.
- Before public v2 completion, the corpus expands only as needed to support the
  result states, findings, and facets that the tool intends to claim.
- Ordinary unmatched sessions may be summarized descriptively by model, but
  direct model comparisons require an explicit benchmark/case ID, matching
  repository/state fingerprint, harness/config, and instrumentation coverage.
- Goal fingerprints help discover candidate cohorts but never establish
  comparability.
- Multi-model episodes belong to a mixed cohort and are not credited to the
  final or dominant model.
- Every aggregate shows total and eligible denominators. Missing capability is
  visible and is not silently treated as unknown behavior or excluded.
- Recurring improvement opportunities require evidence across independent
  episode families, not retries or revisions of one task.
- Recommendations are evidence-backed review opportunities, not prescriptions
  or causal claims.

### Development And Cutover

- `main` uses one active analysis implementation; no parallel v1/v2 semantic
  pipeline is maintained.
- The rewrite may break public analysis contracts incrementally.
- When an underlying contract becomes invalid, affected commands are removed
  or fail explicitly until rebuilt. They never serve stale v1 or partial v2
  semantics.
- Analysis JSON schemas may change multiple times; every change bumps its schema
  version.
- V1 labels/scores are deleted in the replacement PR rather than translated or
  retained for old consumers.
- Infrastructure and calibration changes remain separate PRs.
- Pi may lead implementation, but public v2 completion requires equivalent
  capability handling for Pi, Codex, and Claude Code.
- A failed untouched final-test gate blocks declaring v2 complete. The failure
  cannot be tuned away while retaining the same final-test group.

## Target Pipeline

```text
mutable native files
  -> exact compressed raw blobs + snapshot bundle manifest
  -> versioned normalization over one exact snapshot
  -> lifecycle, ordering, capability, project, model evidence
  -> deterministic task episodes + delegated child links
  -> versioned evidence relations
  -> observed work mode + observed episode result
  -> observable findings
  -> independent facet tiers and efficiency measures
  -> session/project/model-cohort rollups
```

## Core Contracts

### Raw Blob And Snapshot Store

Proposed durable tables:

```text
source_blobs
  blob_id
  content_hash
  codec
  compressed_bytes
  original_byte_length

logical_sources
  logical_source_id
  agent_name
  source_kind
  source_path
  first_seen_at

source_snapshots
  snapshot_id
  logical_source_id
  blob_id
  snapshot_content_id
  captured_at
  native_modified_at
  capture_status
  previous_snapshot_id

snapshot_bundles
  snapshot_bundle_id
  bundle_content_id
  agent_name
  native_session_identity
  captured_at

snapshot_bundle_members
  snapshot_bundle_id
  snapshot_id
  capture_order
  member_role
  member_capture_status

lifecycle_observations
  lifecycle_observation_id
  snapshot_bundle_id
  lifecycle_policy_version
  state
  evidence_json
  observed_at
```

`blob_id` is content-addressed and deduplicates identical bytes.
`snapshot_content_id` identifies one logical source plus one blob for reusable
normalization. `snapshot_id` identifies one immutable capture observation and
includes capture sequence/time, so two identical recaptures remain distinct and
can establish settling. `bundle_content_id` hashes the agent/native bundle
identity plus ordered member role, logical-source identity, snapshot-content
identity, and capture completeness. Identical bytes from unrelated native
sessions therefore cannot collide. `snapshot_bundle_id` identifies one bundle
capture observation. A bundle member may have no snapshot only when its capture
status explicitly records a missing/unreadable required source.

Capture parses only bytes already stored in the blob row. A live append after
capture cannot change those bytes. Invalid trailing data is recorded against
that snapshot and can be re-evaluated by a future parser.

Pruning defaults to unreferenced snapshots. Forced pruning lists affected
normalization runs, analysis runs, annotations, and bundle manifests before
deletion.

### Versioned Normalization And Analysis

Every normalized row belongs to a deterministic normalization run:

```text
normalization_run_id = hash(
  bundle_content_id,
  adapter_name,
  adapter_version,
  normalization_version,
  configuration_hash
)
```

Every analysis result belongs to a deterministic semantic identity:

```text
analysis_identity = hash(
  normalization_run_id,
  lifecycle_observation_id,
  lifecycle_policy_version,
  ordering_version,
  segmentation_version,
  relation_rule_set_version,
  result_rule_set_version,
  finding_rule_set_version,
  facet_policy_version,
  configuration_hash
)
```

Execution timestamps remain separate metadata. Collections and ties use stable
ordering. Numeric tier boundaries are fixed in versioned policy.

Queries default to the latest compatible normalization and analysis for the
latest snapshot. Time-travel options select snapshot and component versions
explicitly.

Lifecycle observations are immutable, versioned rows derived from one or more
capture observations. A later identical capture creates a new lifecycle
observation and therefore a new analysis identity; it never mutates the result
under an existing identity.

### Lifecycle

Lifecycle states:

```text
terminal_observed
settled_unknown
possibly_active
snapshot_incomplete
```

Lifecycle precedence is:

1. any required member missing/unreadable, changing during capture, or carrying
   adapter/parser evidence of a truncated native record/structure ->
   `snapshot_incomplete`;
2. complete bundle with trustworthy native terminal evidence ->
   `terminal_observed`;
3. later complete capture with the same `bundle_content_id` after the settling
   interval -> `settled_unknown`;
4. otherwise -> `possibly_active`.

For sources without terminal markers, one capture produces a
`possibly_active` lifecycle observation. A later ingestion observing the same
content hash after the explicit settling interval creates a new
`settled_unknown` lifecycle observation for the later bundle. Prior snapshots,
lifecycle observations, and analyses remain unchanged. Ingestion never waits
and re-reads implicitly.

The latest active snapshot is still analyzed descriptively. Result state,
efficiency totals, and unresolved-ending conclusions remain provisional and do
not enter finalized rates.

`snapshot_incomplete` is always provisional and ineligible for finalized
aggregates. Terminal evidence inside an incomplete bundle cannot override
incompleteness. Settling compares complete bundle identities, never one member's
content hash.

### Ordering And Delegation

- Record index is authoritative inside one source snapshot.
- Native causal links are preserved when available.
- Timestamps support display and bounded correlation but do not invent order.
- Multi-file and sidechain sources remain a partial order.
- Explicit spawn/tool links attach child episodes to parent episodes.
- Parent rollups include direct and delegated components separately.
- Child evidence IDs and source order remain inspectable.
- No combined parent/child visual timeline is synthesized from timestamps.
- Every evidence row remains owned by its source episode and has one canonical
  `rollup_owner_episode_id`. For delegated task aggregates, that owner is the
  top-level parent episode.
- Child episodes remain independently inspectable but are excluded from
  additive session/project outcome, facet, and efficiency denominators when
  their evidence is projected into a parent.
- Parent delegated projections never create a second evidence contribution.
- If delegated work uses a different model, the parent task episode is mixed
  model and is ineligible for single-model outcome/efficiency rates. Child-only
  execution summaries may be shown separately as descriptive subagent cohorts.

### Project And Model Identity

Project identity uses, in order:

1. trustworthy native repository metadata;
2. a local VCS root observed at ingestion;
3. stored CWD with provenance;
4. unknown.

Touched-path common prefixes never establish project identity. Controlled
model cohorts additionally require repository/state fingerprint and matching
harness/instrumentation configuration.

Episode model identity is:

```text
one model
mixed models
unknown
```

Mixed episodes retain per-event/per-usage model evidence but never contribute
to a single-model outcome rate.

### Episode Boundaries

Primitive interaction-closure evidence is not task outcome. It includes native
final-response phase, a closed assistant turn with no pending tool work, or an
explicitly closed native turn.

Initial segmentation precedence:

1. narrow explicit new-task marker -> split, even without prior closure;
2. explicit correction, clarification answer, or broad repeated-goal relation
   -> no split;
3. observed closure plus strong topic shift -> split;
4. conflicting or weak evidence -> no split plus ambiguous boundary;
5. elapsed time alone -> no split.

Boundary annotations use immutable event anchors and may be `split`,
`no_split`, or `ambiguous`. They are not keyed by ranges generated by the
segmenter itself.

Cross-session high-confidence similarity creates a continuation candidate or
task-family link, never one merged episode.

### Observed Work Mode

Work mode describes logged activity, not user intent:

```text
response_only
inspection
mutating
operational
mixed
unknown
```

- normalized file mutations and recognized mutating commands produce
  `mutating`;
- recognized deploy/publish/service/configuration or external-state-changing
  actions produce `operational`;
- normalized reads/searches, recognized read-only shell commands, browser
  navigation/inspection, and web retrieval produce `inspection`;
- unrecognized shell/browser actions provide action evidence but do not select
  a work mode by tool name alone;
- multiple modes produce `mixed`;
- no observed action tools produce `response_only` only when the adapter declares
  sufficient action instrumentation and the snapshot is complete; otherwise
  the mode is `unknown`.

`response_only` does not assert that the user requested only advice. It may
also represent a requested action that the agent never performed.

### Evidence Relations

Relations are deterministic and versioned:

```text
goal_repeats_prior_goal
explicit_correction_of_prior_response
failure_repeats_target_failure
failure_recovered_on_same_target
validation_cycle_resolved
validation_observed_scope_unknown
post_change_validation_observed
operation_success_observed
evidence_gathered
evidence_delivered
policy_gate_encountered
explicit_user_stop
explicit_task_replacement
continuation_candidate
```

Target-linked progress between broad repeated requests includes:

- a target-linked state mutation;
- a newly successful target;
- new diagnostic evidence with a distinct evidence identity;
- target-linked delivery evidence.

Any successful tool call is insufficient. Repeated reads/searches with no new
evidence identity do not suppress unresolved repetition.

Validation requires a recognized built-in validator, ordering after the last
relevant mutation, deterministic scope linkage, and no later contradictory
failure. Unknown scope remains evidence only.

Operational success requires a recognized state-changing command and target
identity. It does not prove service health or full task success.

### Observed Episode Result

Response state:

```text
delivered
missing
unknown
in_progress
```

Work-evidence state:

```text
post_change_validation_observed
change_observed_unvalidated
operation_success_observed
evidence_delivery_observed
partial_progress
blocked_unresolved
stopped
interrupted_unknown
unknown
in_progress
```

Response-state precedence:

1. a substantive assistant response in an explicit native final-response phase,
   or the settled inferred-delivery rule defined above -> `delivered`;
2. an explicit native completed response phase that records no assistant
   response, or explicit task stop/replacement before any response to the
   current request -> `missing`;
3. a possibly-active latest episode with pending tool work or an unclosed
   assistant turn and no delivery evidence -> `in_progress`;
4. all other cases, including settled silence, adapter-unavailable final-phase
   evidence, and incomplete trailing source -> `unknown`.

Work-evidence precedence over captured evidence:

1. explicit replacement without stop -> `interrupted_unknown`;
2. explicit stop without later restart -> `stopped`;
3. unresolved target blocker -> `blocked_unresolved`;
4. scope-linked post-change validator ->
   `post_change_validation_observed`;
5. recognized target operation success -> `operation_success_observed`;
6. inspection evidence plus delivered response ->
   `evidence_delivery_observed`;
7. mutation without linked validation -> `change_observed_unvalidated`;
8. other target-linked progress -> `partial_progress`;
9. active latest episode with no captured work evidence -> `in_progress`;
10. insufficient evidence, including response-only observed mode -> `unknown`.

Lifecycle provisionality is orthogonal to both states. An active snapshot does
not erase a captured blocker, mutation, validator, operation, or partial
progress; it sets `provisional = true` and excludes the result from finalized
rates. Finalized and provisional values are separate rows/identities, never two
fields that silently overwrite one another.

User acceptance is a separate delivery-certainty relation. It cannot promote
work evidence to post-change validation.

### Findings

Candidate descriptive findings:

```text
explicit_correction
repeated_goal_request
repeated_command_failure
repeated_tool_failure
failure_recovered
resolved_validation_cycle
policy_gate_encountered
explicit_user_stop
ambiguous_episode_boundary
active_snapshot
```

Candidate attention findings:

```text
unresolved_correction_cycle
unresolved_goal_repetition
unresolved_failure_loop
unresolved_tooling_blocker
late_unresolved_failure
```

Attention findings require unresolved evidence. Repetition, corrections,
repeated edits, broad activity, or policy gates cannot trigger attention alone.

Each finding exposes raw components, rule version, support, and evidence IDs.
Names describe measured patterns rather than user or agent state.

### Facets And Efficiency Measures

Initial facets:

```text
friction_burden
friction_turn_density
friction_action_density
unresolved_progress
tooling_blockage
instruction_revision
change_surface
response_support
work_evidence_support
```

Separate efficiency measures:

```text
input_tokens
output_tokens
cache_read_tokens
cache_write_tokens
native_reported_cost
active_execution_time
elapsed_episode_time
```

Each facet has a reviewed rubric with:

- admissible raw components;
- fixed `none | low | medium | high` tier boundaries;
- eligible denominator;
- missing-capability behavior;
- direct versus delegated components;
- prohibited inputs from other facets;
- optional descriptive cohort percentile.

Judges annotate rubric tiers with cited evidence. Pairwise checks validate tier
ordering. There is no shared overall-priority annotation.

### Review Opportunities And Recurrence

Review opportunities state what repeated and where to inspect. They do not
claim a root cause or prescribe a fix.

A pattern is recurring only after appearing in at least two independent task
or source families. Revisions, continuations, child sessions, and repeated
attempts in one family count once.

Family identity is versioned and evidence-backed:

- explicit benchmark/case identity is strongest;
- native root/child topology and explicit continuation links keep related
  episodes in one family;
- high-similarity fingerprints may create an ambiguous family candidate but do
  not prove independence;
- unknown family identity is ineligible for independent-family recurrence;
- family policy is calibrated with the relation rules before recurrence ships.

### Rollups

Episode rollups show:

- total and eligible episodes;
- direct and delegated evidence;
- lifecycle/result counts and rates;
- finding counts and recurrence-family counts;
- facet tier distributions;
- unknown, unavailable, active, and mixed-model counts;
- efficiency coverage and separate measures.

`response_support` and `work_evidence_support` remain separate axes and cannot
be recombined into a completion-confidence score.

Session and project rollups never use a maximum across unrelated dimensions.
Model summaries over unmatched sessions are descriptive only.

## Evaluation And Annotation Contract

### Two-Stage Packets

Boundary packet:

- adjacent user turns;
- intervening assistant/tool structure;
- bounded neighboring context;
- event IDs;
- no Session Doctor boundary prediction.

Episode packet, built only from frozen adjudicated boundaries:

- complete episode user/assistant text;
- bounded adjacent context;
- tool, command, file, status, anonymized model-role, and usage structure;
- hashes and lengths instead of huge raw outputs by default;
- event/entity IDs;
- no Session Doctor relation, lifecycle interpretation, work mode, result,
  finding, facet, recurrence, recommendation, or other derived prediction.

Packets contain normalized source evidence and anonymized capability
declarations only. Model/provider/adapter identity lives in a non-judge routing
envelope. Judge-visible models use stable aliases such as `primary_model` and
`delegated_model_1`. Exact known identity strings are removed from structured
fields and deterministically redacted from source text. A packet that cannot be
reasonably blinded is marked `identity_exposed` and is ineligible for a blinded
judge-quality claim. Lifecycle facts required for a judging task are represented
as raw capture observations, not Session Doctor's derived lifecycle state.

### Judge Export/Import

Session Doctor provides deterministic packet export and schema-validated judge
output import. External agents or provider tooling make LLM calls.

Every imported judgment records:

```text
packet_id
annotation_protocol_version
judge_model
judge_provider
judge_prompt_version
answer
evidence_ids
rationale
created_at
```

The default panel is three distinct non-target models. Unanimous answers become
`judge_consensus`; disagreements become `human_review_required`. Twenty percent
of unanimous pilot answers are randomly selected from a frozen seed for human
audit. Human-unresolved cases retain ambiguous allowed answers.

### Corpus Stages

1. **Pilot development:** 20-30 stratified challenge regions across adapters,
   lengths, prior disagreements, successes, blockers, and active/incomplete
   cases. No checkpoint/final split is claimed before continuation/family rules
   exist. Purpose: stabilize boundaries, definitions, and annotation workflow.
2. **Family seal:** after PR 12 calibrates continuation and family identity,
   assign whole versioned families to development, subsystem checkpoints, or
   final test. Related attempts can no longer cross partitions.
3. **Calibration expansion:** collect additional development families only for
   result states/findings/facets the tool intends to claim.
4. **Checkpoint:** use distinct unopened family groups for the required
   subsystems. Failed checkpoint cases become development evidence; a new
   candidate requires a new independently grouped checkpoint.
5. **Final test:** one untouched family group opened after rule and surface
   freeze.

Synthetic fixtures remain checked in and exact. LLM-assisted reference
annotations are versioned and frozen; production inference remains fully
deterministic.

### Metrics

The pilot reports examples and disagreement, not quality claims. Later gates
are support-driven and finalized after pilot distributions are known.

Required metric families:

- semantic rerun equivalence in the declared environment;
- boundary precision, recall, and ambiguity coverage;
- relation precision and unavailable coverage;
- result-state precision, coverage, and abstention;
- finding precision/recall;
- facet-tier agreement and pairwise ordering;
- active-snapshot false-finalization rate;
- cross-adapter support parity;
- judge unanimity, disagreement, audited consensus error, and human ambiguity;
- total versus eligible denominators.

No result state, finding, facet, adapter, or cohort gets a quality claim below
its approved minimum support. A failed final gate blocks declaring v2 complete.

## Controlled Model Cohorts

Direct comparison requires:

```text
benchmark_case_id
repository_or_state_fingerprint
harness_and_configuration_identity
adapter_and_instrumentation_compatibility
single_model_episode_identity
```

Controlled reports show result/finding/facet and efficiency distributions with
total and eligible denominators. They do not combine efficiency measures or
claim causal model superiority.

Goal fingerprints, projects, and ordinary historical sessions can suggest
candidate cohorts and support descriptive summaries only.

## Incremental Replacement Policy

There is one active analyzer on `main`.

When episode analysis replaces v1 session classification:

- remove v1 scores, labels, active derived rows, and tests;
- bump the analysis schema;
- keep ingestion, snapshot inspection, normalization replay, evaluation, and
  episode analysis available;
- make summary, report, graph, and trends explicitly unavailable until rebuilt;
- restore each command only when it can expose one coherent current contract;
- mark restored v2 surfaces experimental until final-test completion;
- do not provide v1 fallback or v2-to-v1 translation.

## PR Roadmap

Each PR must remain green, make unavailable states explicit, and avoid mixing
infrastructure with calibration.

Dependency policy:

- each numbered PR depends on all prior merged PRs unless its section says
  otherwise;
- PR 8 stabilizes the provisional segmentation version persisted by PR 9;
- PR 12 seals family partitions and jointly gates segmentation/relations before
  result rules consume them;
- PR 12 gates relation families consumed by PR 13;
- PR 14 gates result states consumed by PR 15;
- PR 16 gates findings used by facet rubrics in PR 17;
- PR 19 gates every non-experimental analytical family restored in PRs 20-22;
- a failed or low-support calibration does not force a guessed implementation:
  dependent PRs omit that family and expose it as unavailable/experimental;
- PR 23 can declare v2 complete only for families preregistered in PR 19 and
  passed by the untouched final test.

### PR 1: Accept The Grilled V2 Contract

Deliverables:

- finalize this plan and update the main design document;
- finalize enums, naming, packet schemas, and unavailable-command policy;
- define which current commands become unavailable at the episode cutover;
- record deferred choices: compression codec, exact table names, and initial
  tier thresholds.

Tests:

- documentation-only; existing suite remains green.

Gate:

- every remaining open issue is implementation-level or explicitly deferred.

### PR 2: Durable Raw Blob And Snapshot Schema

Deliverables:

- add compressed content-addressed BLOB storage;
- add logical source, snapshot, bundle, and member manifests;
- capture single-file sources into DuckDB before parsing;
- deduplicate unchanged bytes;
- preserve blob/snapshot tables as durable migration inputs.

Likely areas:

- store migrations, schemas, IDs, ingest workflow, adapter base contracts.

Tests:

- exact byte round-trip;
- unchanged content deduplication;
- growing file creates a new blob/snapshot;
- parser sees only stored bytes;
- durable-table migration behavior.

Gate:

- exact parser replay is possible from DuckDB with the original source removed.

### PR 3: Snapshot Bundles, Lifecycle, Time Travel, And Pruning

Deliverables:

- support multi-file bundle manifests and capture skew evidence;
- derive lifecycle using terminal evidence or later identical capture;
- add latest/status-aware and explicit snapshot selection;
- add snapshot listing and raw replay inspection;
- add reference-aware explicit prune and post-prune `CHECKPOINT`;
- retain latest plus historical snapshots without duplicate aggregate sessions.

Tests:

- Claude-style multi-file bundle replay;
- active first capture and settled later identical capture;
- no implicit ingest waiting;
- historical snapshot selection;
- prune block, force dependency report, and checkpoint path.

Gate:

- ordinary ingestion supports exact raw time travel and status-aware latest
  selection.

### PR 4: Versioned Normalization Replay

Deliverables:

- key normalized rows by deterministic normalization run;
- retain multiple parser versions over one raw snapshot;
- add explicit historical normalization command;
- mark current/stale/missing parser coverage;
- keep new snapshots on current parser by default without automatic historical
  reprocessing.

Tests:

- two parser versions coexist over identical bytes;
- explicit additive replay never replaces old rows;
- latest compatible selection is deterministic;
- no query triggers implicit normalization writes.

Gate:

- parser drift can be measured independently from analysis drift.

### PR 5: Ordering, Capabilities, Identity, Project, Model, And Usage Semantics

Deliverables:

- define source order and partial-order relationships;
- add adapter capability and instrumentation evidence;
- derive deterministic semantic analysis identity;
- improve project identity using native metadata or observed VCS root;
- preserve one/mixed/unknown model identity;
- declare usage rows cumulative, incremental, or aggregation unavailable;
- retain versioned analysis histories beginning with v2.

Tests:

- timestamp regressions do not reorder source records;
- concurrent sidechains remain a partial order;
- missing capability is unavailable, not zero;
- home-CWD session can use trustworthy VCS/native identity;
- mixed-model and unavailable-usage cases;
- semantically identical runs match in the declared environment.

Gate:

- downstream rules never need to guess ordering, capability, project, model,
  or usage semantics.

### PR 6: Evaluation Packets, Judge Import, And Corpus Workflow

Deliverables:

- add boundary and episode packet schemas/export;
- add blinded judge-output import and evidence-ID validation;
- implement three-judge consensus, disagreement queue, frozen audit sampling,
  ambiguous reference answers, and protocol versions;
- create a 20-30-case stratified boundary pilot from candidate session regions;
- define episode-packet schemas now, but do not generate episode packets until
  boundary references are frozen after PR 8;
- keep all provider calls outside Session Doctor.

Tests:

- packet determinism and blinding;
- invalid or hallucinated evidence IDs rejected;
- unanimous versus 2-1 behavior;
- audit sample stable for one seed;
- target model excluded from its judge panel;
- routing identity never appears in a blind-eligible judge packet;
- identity-exposed and target-identity-unverifiable packets remain explicitly
  ineligible for the affected blinded claims;
- pre-PR-12 packets represent family identity as unknown/ambiguous rather than
  inventing a leakage family;
- boundary and episode capabilities are task-minimized and anonymized;
- panel consensus, audit selection, human adjudication, and final reference
  resolution remain separate immutable records.

Gate:

- boundary pilot annotations can be produced efficiently without production
  LLM dependencies or circular exposure to generated episodes or predictions.

### PR 7: Episode Segmenter And V1 Analysis Removal

Deliverables:

- add event-anchored episode and boundary models;
- implement Unicode-aware broad goal similarity and conservative precedence;
- implement explicit new-task override and ambiguous merge behavior;
- remove v1 score/classification producer, rows, payloads, and tests;
- change `analyze` to episode/lifecycle/observation output;
- make summary, report, graph, trends, and `projects list` explicitly
  unavailable;
- update README, changelog, and the bundled integration skill to describe the
  temporary command availability and remove all v1 labels/scores guidance.

Tests:

- explicit new task splits without closure;
- correction/review/repeat remains one episode;
- weak topic shift merges with ambiguity;
- elapsed time does not split;
- cross-session content remains separate; continuation relations are deferred
  to the relation framework;
- unavailable commands fail with one deliberate message and no fallback.
- public docs and integration guidance cannot invoke unavailable/v1 semantics.

Gate:

- one active episode analyzer remains; no v1 semantic path survives.

### PR 8: Segmentation Calibration

Deliverables:

- run the pilot boundary packets through three judges and human audit;
- tune only segmentation rules against development references;
- add accepted cases as synthetic regressions;
- freeze a provisional segmentation version for relation development;
- freeze adjudicated boundary IDs and deterministic episode-evidence packet
  inputs; do not assign task-specific packet IDs until each owning relation,
  result, finding, or facet rubric and allowed-answer set is versioned.

Gate:

- definitions are stable enough for persisted episode identity;
- no checkpoint/final quality claim is made before family partitions exist.

### PR 9: Episode Persistence, Delegation, And Continuations

Deliverables:

- persist episodes and boundaries by segmentation version;
- attach normalized entities or explicit ambiguity/unassigned status;
- attach child/subagent episodes to parent episodes by native spawn evidence;
- persist delegation topology and provenance without yet aggregating relation,
  finding, facet, or efficiency components;
- keep cross-session episodes separate until the relation framework derives
  continuation or family candidates.

Tests:

- deterministic IDs and round-trip;
- explicit unassigned/ambiguous events;
- delegation topology and provenance;
- no child double counting or synthetic ordering;
- no unversioned continuation or family identity.
- one canonical rollup owner per evidence row and explicit child aggregate
  ineligibility.

Gate:

- every analyzable entity has explicit episode membership or explicit reason it
  does not.

### PR 10: Relation Framework And Observable Failure Taxonomy

Deliverables:

- add versioned relations with evidence/support;
- implement broad goal repetition, explicit correction, repeated target
  failure, explicit stop/replacement, continuation, and progress relations;
- implement narrow failure categories and separate policy gates;
- model fail-edit-pass as a validation-cycle candidate.
- derive versioned continuation and family candidates, including explicit
  unknown/ambiguous family state.
- implement direct/delegated relation components over the persisted topology
  without interleaving child events.

Tests:

- same versus unrelated target identity;
- broad repeated goal without unresolved attention;
- new diagnostic evidence versus repeated read/search;
- policy denial versus ordinary tool failure;
- explicit replacement and stop distinctions.
- explicit continuation versus similarity-only ambiguity;
- family independence eligibility.

Gate:

- each relation is reconstructible from named evidence and a versioned rule.

### PR 11: Validation, Recovery, Operation, And Evidence Delivery Relations

Deliverables:

- add built-in validator registry;
- require scope and ordering for post-change validation;
- retain unknown-scope validator evidence without state promotion;
- implement same-target recovery and resolved validation cycles;
- implement target-linked operation success;
- implement inspection evidence gathering and delivery relations.

Tests:

- validator before versus after final mutation;
- unrelated passing command;
- unknown validation scope;
- later contradictory failure;
- corrected equivalent command with same target identity;
- operation success without service-success claim;
- evidence-backed versus unsupported response.

Gate:

- no relation overclaims task completion or unrelated recovery.

### PR 12: Relation Calibration

Deliverables:

- annotate and tune relation families separately;
- generate relation-task packets from frozen episode evidence and the versioned
  relation answer rubric;
- audit broad repetition false positives and recovery/validation precision;
- calibrate continuation/family identity before recurrence consumers exist;
- add synthetic regressions;
- seal versioned leakage families and assign whole families to development,
  subsystem checkpoints, or final test;
- freeze segmentation and relation candidates together;
- run fresh segmentation and relation checkpoints over independent family
  groups.

Gate:

- only supported relation families advance to observed-result rules;
- low-support relations remain unavailable or experimental;
- related attempts, continuations, revisions, and child sessions cannot cross
  checkpoint/final partitions.

### PR 13: Observed Work Mode And Episode Result

Deliverables:

- derive response-only, inspection, mutating, operational, mixed, and unknown
  work modes from logged activity;
- implement response and work-evidence state machines;
- add interrupted unknown, user acceptance certainty, active provisional state,
  and contradiction handling;
- expose direct and delegated work evidence separately in parent result support;
- bump analysis JSON schema.

Tests:

- every work mode and result state;
- action request with no tool use remains response-only observed mode;
- missing action instrumentation or incomplete capture prevents response-only;
- read-only shell/browser inspection, shell/file mutation, external operation,
  and unrecognized shell/browser cases;
- active snapshot never finalizes;
- silence never means abandonment;
- acceptance does not replace validation;
- inspection, operation, mutation, and response-only cases.

Gate:

- result names describe observed evidence, not ground-truth success.

### PR 14: Result Calibration

Deliverables:

- annotate/tune result precedence and support using frozen episode boundaries;
- generate result-task packets from frozen episode evidence and the versioned
  result answer rubric;
- measure coverage, abstention, active false-finalization, and judge agreement;
- add synthetic regressions;
- run a fresh result checkpoint.

Gate:

- unsupported states remain unknown/experimental;
- checkpoint failure blocks findings that depend on those states.

### PR 15: Observable Findings

Deliverables:

- add descriptive and attention findings;
- separate correction/repetition/failure burden, recovery, and unresolved state;
- prohibit broad change surface and policy gates from becoming attention alone;
- add finding evidence and rule versions;
- expose direct and delegated finding support separately;
- bump analysis JSON schema.

Tests:

- successful long review cycle;
- unresolved correction/repetition/failure loops;
- recovered failure remains visible without attention finding;
- broad edits/tools do not imply a problem;
- no user-state, blame, or causal wording.

Gate:

- findings are observable and resolution-aware.

### PR 16: Finding Calibration

Deliverables:

- annotate/tune one finding family at a time;
- generate finding-task packets only after that finding rubric and allowed
  answers are versioned;
- audit attention precision and broad-repetition behavior;
- add synthetic regressions;
- run fresh finding checkpoints only for supported public findings.

Gate:

- unsupported findings remain experimental or absent.

### PR 17: Facet Rubrics And Tier Contracts

Deliverables:

- finalize admissible components and fixed tier boundaries per facet;
- define separate burden/density denominators;
- define direct/delegated and missing-capability behavior;
- define facet-specific judge rubrics and pairwise checks;
- define efficiency coverage without a combined score.

Tests:

- rubric schema rejects cross-facet/overall relevance inputs;
- tier anchors cover ties, missing support, work modes, and long episodes.

Gate:

- every implemented facet has an independent measurable target.

### PR 18: Facets And Efficiency Measures

Deliverables:

- implement fixed tiers with raw components;
- implement separate per-turn/per-action density tiers;
- implement direct/delegated change surface;
- implement independent response-support and work-evidence-support tiers;
- expose separate token, cost, active-time, and elapsed-time measures;
- expose optional descriptive percentiles without changing fixed tiers;
- compute direct and delegated facet/efficiency components separately before
  parent rollup;
- aggregate each source evidence row exactly once through its canonical rollup
  owner;
- bump analysis JSON schema.

Tests:

- monotonic tier boundaries and stable ties;
- long resolved work versus short unresolved work;
- response support cannot improve work-evidence support or vice versa;
- unavailable usage aggregation;
- active versus elapsed time;
- no composite or hidden overall ranking.
- child-owned evidence cannot appear twice in parent/session/project totals;
- cross-model delegation makes the parent mixed rather than crediting the
  parent's direct model.

Gate:

- every output is one facet or one efficiency measure with explicit support.

### PR 19: Facet Calibration And Rule Freeze

Deliverables:

- annotate facet tiers and pairwise orderings;
- generate facet-task packets only after PR 17 freezes that facet rubric and
  allowed answers;
- tune one facet at a time;
- audit length bias and missing-capability effects;
- add synthetic regressions;
- run fresh facet checkpoints;
- freeze segmentation, relation, result, finding, and facet versions for final
  surface work.
- preregister exact final metric formulas, minimum support, pass/fail thresholds,
  final-group size/allocation, judge/audit protocol, and access record before
  any final-test case is opened;
- seal the final-test manifest and reference annotations from classifier output.

Gate:

- only facets with sufficient support proceed as non-experimental.

### PR 20: Restore Exact Reports And Graphs

Deliverables:

- restore exact-session terminal, Markdown, JSON, HTML, and graph commands;
- make episodes primary sections;
- expose raw snapshot, normalization, analysis, lifecycle, result, relation,
  finding, facet, efficiency, direct/delegated, and support provenance;
- add snapshot/version selection;
- keep bounded output and deterministic projection behavior;
- mark v2 surfaces experimental until final gate.

Tests:

- one typed result across all formats;
- historical snapshot and analysis selection;
- active provisional display;
- no stale v1 fields or cross-links.

Gate:

- exact-session surfaces contain one coherent v2 contract for all adapters.

### PR 21: Restore Summary And Review Opportunities

Deliverables:

- restore summary with independent facet rank groups;
- restore `projects list` as a normalization-only project identity view without
  analysis coverage/version fields;
- add total/eligible/active/unknown/mixed-model denominators;
- add result/finding/facet distributions and recurring independent-family
  evidence;
- add evidence-backed review opportunities without prescriptions;
- preserve explicit project/model/capability support;
- mark v2 surface experimental until final gate.

Tests:

- no max/composite risk path;
- complexity cannot enter unresolved rankings;
- independent-family recurrence;
- latest status-aware snapshot selection without duplicate historical sessions;
- all three adapters represented with missing capability explicit.
- delegated child episodes excluded from additive denominators while parent
  rollup includes their evidence once.
- project listing works with no analysis rows and exposes identity provenance.

Gate:

- summary cannot imply causal model, user, prompt, or agent judgment.

### PR 22: Restore Trends And Controlled Model Cohorts

Deliverables:

- restore project trends over episode results/findings/facet tiers;
- add descriptive unmatched model summaries;
- add controlled cohorts requiring case, repository/state, harness/config, and
  instrumentation identity;
- exclude mixed-model episodes from single-model rates;
- expose separate efficiency measures and total/eligible denominators;
- mark v2 surfaces experimental until final gate.

Tests:

- aligned periods, support gates, active/unknown coverage;
- controlled versus candidate/unmatched cohorts;
- mixed-model exclusion;
- cross-model delegation cannot credit child work to the parent model;
- no best/worst or causal language;
- Pi, Codex, and Claude equivalent-capability fixtures.

Gate:

- all supported adapters and public analytical commands have coherent v2
  behavior.

### PR 23: Corpus Expansion, Final Test, And V2 Completion

Deliverables:

- expand references only enough to support intended claims;
- freeze all code, packet, judge, rule, tier, and surface versions;
- open final test once;
- report synthetic, judge, human-audit, checkpoint, and final metrics
  separately;
- run copied-local Pi, Codex, and Claude validation;
- remove experimental markers only if all approved gates pass;
- define experimental-to-validated as a metadata-only release-state change;
- rerun the full deterministic synthetic and end-to-end suite against the exact
  post-marker artifact before declaring completion;
- update README, design, changelog, integration skill, and validation docs from
  their transition state to the validated complete v2 surface;
- confirm v1 code/results are gone while durable raw snapshots remain.

Gate:

- passing final gates declare v2 complete;
- failure leaves v2 experimental and requires a new protocol/version plus a
  new final-test group before a future completion claim.

## Global PR Requirements

Every implementation PR must:

- identify its dependency and merge gate;
- keep one active semantic implementation;
- make invalid downstream commands explicitly unavailable;
- preserve durable raw snapshots;
- keep active, unknown, ambiguous, mixed, and unavailable states visible;
- add false-positive/false-negative synthetic cases when changing a detector;
- version schema and semantic policy changes;
- avoid implicit query-time mutation;
- pass:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

No implementation PR is committed or opened without explicit user direction.

## Remaining Implementation-Level Questions

These are deliberately deferred until their owning PR:

- compressed BLOB codec and level;
- exact durable/derived table names and constraints;
- exact settling interval;
- built-in validator command registry;
- initial tier thresholds after pilot annotation;
- active-time derivation by adapter;
- snapshot/prune CLI names;
- report/trend schema version numbers;
- exact support thresholds after pilot distributions.
