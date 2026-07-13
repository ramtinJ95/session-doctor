# Session Doctor Design

Status: deterministic analysis v2 architecture accepted for implementation.

The staged implementation and validation contract is
[`deterministic-analysis-v2-plan.md`](deterministic-analysis-v2-plan.md). The
phase documents record the historical v1 implementation; they are not the
current analysis contract.

## Product Intent

Session Doctor is a local-first CLI for inspecting coding-agent sessions from
Codex, Claude Code, and Pi. It preserves exact source evidence, normalizes
agent-specific formats, and derives deterministic observations that a person or
another tool can inspect.

Production analysis:

- runs locally;
- makes no LLM, model, provider, telemetry, or network call;
- treats task episodes as the primary analytical unit;
- treats native sessions as provenance containers and rollups;
- reports observable evidence without claiming true task success, user state,
  agent blame, or causality;
- preserves active, unknown, unavailable, ambiguous, and mixed states;
- ranks independent facets without an overall risk score.

External LLMs may help create evaluation annotations through blinded packet
export and validated result import. They are never runtime analysis
dependencies or gold evidence by themselves.

## Current Transition

The repository currently contains the deterministic v1 session analyzer,
classifications, scores, reports, graphs, summaries, and trends. They remain
operational only until PR 7 of the v2 roadmap replaces the analyzer.

At PR 7:

- v1 labels, scores, derived tables, payloads, and tests are deleted;
- `analyze` switches to the episode/lifecycle/observation contract;
- `summary`, `trends`, `report`, `graph`, and `projects list` remain registered
  but fail before opening the database with exit code 2 and this message shape:

  ```text
  <command> is unavailable during the deterministic analysis v2 rebuild; see docs/deterministic-analysis-v2-plan.md.
  ```

- no command falls back to v1 or serves a partial v2 projection;
- `version`, `doctor`, `adapters list`, `db init`, `db info`, `ingest`,
  `sessions list`, and `integrations path` remain available;
- restored v2 analysis surfaces are marked experimental until PR 23 passes the
  untouched final-test gate.

`projects list` is unavailable because its current payload includes v1 analysis
coverage/version fields. PR 21 restores it as a normalization-only project
identity view with no derived-analysis dependency.

Between PR 2 and PR 3, Claude parent topology and persisted tool-result
enrichment were explicitly unavailable rather than derived from uncaptured live
sidecars. Schema version 6 restores them from exact multi-file bundle members.

### Phase 11: Standalone Visual Reports And Trend Dashboards

Until PR 7 removes the dependent v1 projections, renderers consume typed projections only.
HTML writes one self-contained offline file and does not query DuckDB. Graph remains JSON-only.
These are current output-safety requirements, not references to the historical
phase document. The v2 report/graph/trend replacements inherit them when PRs 20-22 restore
those surfaces.

## Architecture

```text
mutable native files
  -> exact compressed source blobs and capture manifests
  -> versioned normalization over exact captured bytes
  -> lifecycle, capability, identity, ordering, and provenance evidence
  -> deterministic task episodes and delegation topology
  -> versioned evidence relations
  -> observed work mode and observed episode result
  -> observable findings
  -> independent facet tiers and efficiency measurements
  -> session, project, and controlled-cohort rollups
```

DuckDB is the authoritative local history store. Raw source snapshots are
durable. Normalized and derived rows are versioned and rebuildable.

## Component Boundaries

### Discovery

Discovery finds candidate native sources without interpreting their analytical
meaning. Built-in roots are:

```text
Codex       ~/.codex/sessions
Claude Code ~/.claude/projects
Pi          ~/.pi/agent/sessions
```

Discovery exposes source kinds and skipped/unsupported categories explicitly.
It does not silently turn metadata, memory, or unrelated sidecars into
sessions.

### Capture

Capture stores exact bytes before parsing them. Identical bytes share one
compressed content-addressed blob, while every ingest observation retains its
own immutable capture identity and time. Multi-file sessions use ordered bundle
manifests; they do not claim an atomic directory snapshot.
Claude manifests are limited to required transcripts, their metadata, and
referenced sidecars; bytes used to select evidence are verified during capture.

Schema version 6 stores blobs with SHA-256 content identity and deterministic
zlib level-6 compression in `source_blobs`; the other durable capture tables are
`logical_sources`, `source_snapshots`, `snapshot_bundles`, and
`snapshot_bundle_members`, plus bundle/member capture metadata and immutable
lifecycle observations.

History is exposed through `session-doctor snapshots list`, `show`, `replay`,
and `prune`. Replay requires an explicit output path, refuses accidental
replacement, and can export a complete ordered multi-file bundle. Non-terminal
complete bundles settle only after a consecutive identical capture in the same
lineage at least 30 seconds later. Prune reports structured dependent loss,
including inbound topology references, clears references to deleted
provenance, and commits relational deletion atomically before checkpointing
DuckDB.

The parser consumes only stored bytes. A source mutation after capture cannot
change that normalization run.

Raw snapshot deletion is explicit. Referenced snapshots cannot be pruned
without a forced operation that lists dependent loss. DuckDB checkpointing
follows successful pruning where it can reclaim storage.

### Adapters And Normalization

Each adapter owns native discovery, parsing, and capability declarations. It
normalizes source evidence without producing product findings.

The common evidence model includes:

```text
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
```

Every normalized entity preserves source, record order, native identity,
adapter/parser version, and support. Missing adapter capability is unavailable,
not false or zero.

Normalization runs are immutable and versioned. Multiple parser versions may
coexist over the same exact bundle content. Historical reprocessing is explicit
and additive.

Schema version 7 stores immutable run metadata in `normalization_runs`, links
runs to capture observations through `normalization_run_bundles`, and keys each
canonical serialized normalized entity by run in `normalized_entities`.
Ordinary ingest records only the current parser run for the new capture.
`normalizations replay` is the only historical write path; status and selection
queries are read-only and report current, stale, or missing coverage. The v1
normalized tables remain a rebuildable current compatibility projection until
the v2 query cutover.

Bundle content identity includes every persisted source-descriptor field used
as parser input. Normalized entity payloads use recursively key-sorted canonical
JSON. A compatible fallback run must use the same adapter, normalization
version, configuration hash, and parser major version, with a parsed numeric
version no newer than the running parser; ties break by run identity. Adapter
capability declarations are canonical normalization-configuration inputs.

### Identity And Ordering

- Source record order is authoritative within one captured source.
- Native causal links are retained where available.
- Timestamps do not invent order across concurrent sources.
- Parent and delegated child timelines remain separate partial orders.
- Project identity uses trustworthy native metadata, an ingestion-time VCS
  root, stored CWD with provenance, or unknown—in that order.
- Model identity is one model, mixed models, or unknown.
- Usage semantics are cumulative, incremental, or unavailable.

Schema version 8 persists `normalization-v3` semantic foundations by
normalization run. Record index is authoritative inside each source; duplicate
indexes are invalid, and timestamps never reorder records or create
cross-source edges. Native parent links create causal edges only within their
source; duplicate native IDs and unresolved parents remain explicit instead of
guessing, and self/forward references cannot become causal edges. Adapter
declarations keep capability support separate from observed
instrumentation, so absent evidence is unavailable rather than zero. Terminal
instrumentation cites inspectable raw-event IDs.

Project identity records native repository metadata, a VCS root observed and
stored at ingestion, stored CWD, or unknown in that precedence order. Bundle
content identity includes the stored ingestion observation. Model identity is
one model, mixed models, or unknown from every structured provider/model pair
and adapter model transition, including unknown-provider evidence, not only the
final session model. Every
usage row declares cumulative, incremental, or aggregation-unavailable
semantics; mixed row semantics make aggregate usage unavailable.

`semantic_analysis_runs` retains additive v2 semantic histories keyed by the
declared analysis identity. Execution timestamps are metadata and do not alter
the identity. A run is accepted only when its normalization foundation,
lifecycle observation, and ordering version exist and match.

Every delegated evidence row has one source episode and one canonical rollup
owner. Parent reports may project delegated components, but additive aggregates
count each row once. Cross-model delegation makes the parent episode mixed and
ineligible for single-model outcome rates.

### Episodes

Task episodes never span native session files. Cross-session work can have a
versioned continuation/family relation but is never auto-merged.

Boundary precedence is:

1. narrow explicit new-task marker -> split;
2. correction, clarification, or broad repeat -> no split;
3. observed interaction closure plus strong topic shift -> split;
4. weak/conflicting evidence -> no split plus ambiguity;
5. elapsed time alone -> no split.

Boundary identity uses immutable event anchors rather than segmenter-generated
ranges. Silence does not imply abandonment. Explicit replacement without
closure produces interrupted-unknown evidence for the earlier episode.

### Relations, Results, And Findings

Relations connect named evidence under a versioned rule. Successful activity
only resolves earlier failure when target identity and ordering establish the
relationship. Passing validators with unknown changed-scope linkage remain
evidence but cannot promote work state.

The result model is `ObservedEpisodeResult`:

```text
response_state
work_evidence_state
observed_work_mode
response_support
work_evidence_support
provisional
evidence_relation_ids
```

Response delivery and work evidence remain separate. User acceptance may
increase response support but cannot substitute for validation.

Findings describe patterns such as explicit correction, repeated target
failure, recovery, unresolved repetition, or policy gates. Burden, recovery,
and unresolved attention are separate. Broad change surface is a facet, not an
attention finding.

### Facets And Efficiency

Facets expose raw components and one fixed ordinal tier:

```text
none
low
medium
high
```

Initial independent facets are:

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

Tokens, native cost, active execution time, and elapsed time are separate
efficiency measurements. They are not facets and are never combined.

### Rollups And Model Cohorts

Every aggregate shows total and eligible denominators. Active, unknown,
unavailable, ambiguous, delegated, and mixed-model counts remain visible.

Ordinary model-grouped history is descriptive. Direct model comparison requires
an explicit benchmark/case identity, repository/state fingerprint,
harness/configuration identity, compatible instrumentation, and a single-model
episode. Goal similarity alone never establishes a controlled cohort.

## Accepted Enum Contract

The following semantic values are fixed for the initial v2 implementation.
Schema versions may add values but cannot silently reinterpret them.

```text
AgentName
  codex | claude | pi

SupportStatus
  observed | inferred | ambiguous | unavailable

LifecycleState
  terminal_observed | settled_unknown | possibly_active | snapshot_incomplete

BoundaryDecision
  split | no_split | ambiguous

ObservedWorkMode
  response_only | inspection | mutating | operational | mixed | unknown

ResponseState
  delivered | missing | unknown | in_progress

WorkEvidenceState
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

ModelIdentityKind
  one_model | mixed_models | unknown

UsageSemantics
  incremental | cumulative | unavailable

ActionOutcome
  success | ordinary_failure | policy_gate | timeout_or_cancelled | unknown

OrdinaryFailureReason
  command_not_found | permission | network | test_assertion
  tool_reported_failure | unknown

FacetTier
  none | low | medium | high

FamilyIdentityStatus
  established | ambiguous | unknown

AnnotationPacketKind
  boundary | episode

IdentityExposureStatus
  blind_eligible | identity_exposed | target_identity_unverifiable

JudgeConsensusStatus
  unanimous | disputed | insufficient

AuditSelectionStatus
  not_selected | selected

AuditEligibilityStatus
  eligible | ineligible

HumanReviewKind
  panel_dispute | panel_insufficient | consensus_audit

ReferenceResolutionStatus
  judge_consensus | human_resolved | ambiguous

ReleaseState
  experimental | validated
```

Finding and relation names are versioned rule-set members rather than global
enums so unsupported families can remain absent without pretending exhaustive
coverage.

## Evaluation Packet Contract

Schema version 9 stores packet registrations, judge annotations, panel
resolutions, audit selections, human adjudications, and final references as
separate immutable durable records. Boundary export is deterministic and local;
`evaluation import-judge` only reads an externally produced JSON record. No
evaluation command invokes a provider. Episode export remains explicitly
unavailable until frozen adjudicated boundaries exist after PR 8.

Every packet has an `evaluation_corpus_id`. Audit freezing requires an explicit
corpus, a durably preregistered cardinality, and complete atomic packet registration
before any panel resolution. The immutable audit record stores the complete
cohort, eligible subset, ranked nearest-20% selection, and one seed. Panel
resolution requires that frozen corpus record. No packet may be added afterward.

The checked-in `evaluation/boundary-pilot-v1.json` preregisters 24 stratified
development regions across adapters, lengths, successes, blockers,
active/incomplete cases, and prior ambiguity. Family identity remains unknown
or ambiguous before PR 12, so this pilot makes no checkpoint or final-test
claim. `evaluation/boundary-pilot-sources-v1.json` supplies the ordered source
turns and intervening structures; the loader rejects missing, duplicate, or
non-adjacent regions and derives 24 unique packet identities. Judge packets
contain only those normalized events; selection strata stay in the private
manifest. `evaluation export-pilot` durably captures the exact combined corpus,
registers its 24 packets with direct snapshot-bundle provenance, and exports
judge-only files. The command accepts no corpus override: registration verifies
the captured bytes against the pinned checked manifest and source document.
Normal production packets retain normalization-run plus
snapshot-bundle provenance and reconstruct private target identities from the
stored semantic foundation before regenerating and atomically registering the
complete expected boundary corpus. Episode packets cannot use this path.

A frozen cohort spanning multiple snapshot bundles makes partial snapshot
pruning unavailable, even with force. The cohort may be removed only when the
authorized prune covers its complete packet set, preventing dangling audit and
reference provenance.

Evaluation uses two documents: a private routing envelope retained in DuckDB
and a judge-visible packet written to the export directory. Routing envelopes
must never be written beside judge packets. Both carry independent schema and
annotation-protocol versions.

### Routing Envelope

```text
schema_version
annotation_protocol_version
packet_id
packet_kind
normalization_run_id
snapshot_bundle_id
source_family_id
source_family_status
family_policy_version
target_model_identities
excluded_judge_identities
identity_exposure_status
judge_packet_hash
```

`source_family_id` and `family_policy_version` are nullable while
`source_family_status` is `unknown` or `ambiguous`; they become required only
for an `established` family after PR 12. Routing identity is never included in
a blind-eligible judge packet. `target_identity_unverifiable` is ineligible for
any claim requiring exclusion of the target model from its own judge panel.

### Boundary Packet

```text
schema_version
annotation_protocol_version
packet_id
packet_kind = boundary
left_user_event_id
right_user_event_id
adjacent_user_turns
intervening_normalized_events
bounded_context_events
anonymized_capability_support
```

Allowed answers are `split`, `no_split`, and `ambiguous`. Evidence IDs must
belong to the packet.

### Episode Packet

Episode packets are generated only from frozen adjudicated boundaries.

```text
schema_version
annotation_protocol_version
packet_id
packet_kind = episode
episode_anchor_ids
annotation_task
normalized_episode_events
bounded_context_events
raw_capture_observations
anonymized_model_roles
anonymized_capability_support
allowed_answers
```

`annotation_task` selects one result, relation, finding, or facet rubric. The
packet never includes Session Doctor predictions or derived lifecycle/work-mode
interpretation. Known model/provider/adapter strings are redacted. Packets that
cannot be reasonably blinded are marked identity-exposed in the routing
envelope and excluded from blinded quality claims.

### Judge Annotation

```text
judge_annotation_id
schema_version
annotation_protocol_version
packet_id
judge_model
judge_provider
judge_prompt_version
answer
evidence_ids
rationale
created_at
```

Imports reject unknown packets, answers outside the packet rubric, evidence IDs
outside the packet, excluded target judges, and protocol/schema mismatches.

Three distinct non-target judges form the default panel. Only unanimity becomes
automatic judge consensus. Disagreement requires human review. A frozen random
20% of unanimous pilot cases receives human audit. Human-unresolved cases may
remain ambiguous.

### Panel, Audit, And Human Adjudication

Judge answers are immutable inputs. Consensus, audit selection, and final
reference resolution are separate versioned records:

```text
JudgePanelResolution
  judge_panel_resolution_id
  schema_version
  annotation_protocol_version
  packet_id
  judge_annotation_ids
  consensus_status
  unanimous_answer
  resolved_at

AuditSelection
  audit_selection_id
  schema_version
  annotation_protocol_version
  packet_id
  judge_panel_resolution_id
  eligibility_status
  selection_status
  selection_seed_id
  selection_reason
  selected_at

HumanAdjudication
  human_adjudication_id
  schema_version
  annotation_protocol_version
  packet_id
  judge_panel_resolution_id
  audit_selection_id
  review_kind
  reviewer_identity
  answer
  evidence_ids
  rationale
  reviewed_at

ReferenceResolution
  reference_resolution_id
  schema_version
  annotation_protocol_version
  packet_id
  resolution_status
  answer
  source_judge_panel_resolution_id
  source_audit_selection_id
  source_human_adjudication_id
  resolved_at
```

Every unanimous panel receives one audit-selection record. Ineligible panels
must be `not_selected`; eligible pilot panels are deterministically `selected`
or `not_selected` by the frozen sample. `audit_selection_id` is required on a
consensus-audit adjudication and null for panel-dispute/panel-insufficient
review. `source_audit_selection_id` is required on every resolution from a
unanimous panel. An audit never overwrites the panel record. Its human
adjudication and final resolution preserve whether consensus was confirmed,
reversed, or left ambiguous.
Ineligible panels cannot become final references; they remain available only
as diagnostic annotations outside blinded quality claims.

All referenced records must share `schema_version`, `packet_id`, and
`annotation_protocol_version` with their referenced panel. An audit selection
may reference only a unanimous panel. A consensus-audit human adjudication must
reference a `selected` audit record for that same panel; dispute and insufficient
adjudications must reference the corresponding disputed or insufficient panel
and no audit record. A reference resolution may cite only panel, audit, and
human records satisfying these co-reference rules. Imports reject cross-packet,
cross-protocol, cross-panel, or status-incompatible links.

Final provenance matrix:

- `judge_consensus` requires one eligible unanimous panel, no human
  adjudication, and an explicit not-selected audit record; its answer equals
  the unanimous panel answer;
- `human_resolved` requires exactly one cited human adjudication for a disputed
  or insufficient panel, or one consensus-audit adjudication tied to a selected
  audit; its answer equals that cited adjudication's answer;
- `ambiguous` requires human adjudication for the disputed, insufficient, or
  selected-audit path, cites exactly that adjudication, and records its allowed
  ambiguous answer;
- a selected consensus audit cannot produce final `judge_consensus` without a
  completed human adjudication; confirmed consensus resolves as
  `human_resolved` while preserving the original unanimous panel answer.

## Versioning And Determinism

Every schema change increments the relevant schema version. Raw capture history
is durable across schema changes; derived v1 history may be deleted at the PR 7
replacement.

Analysis identity includes exact normalized content, lifecycle observation,
ordering, segmentation, relation, result, finding, facet, and configuration
versions. Execution timestamps are metadata, not semantic identity.

Determinism is semantic within a declared environment: identical exact input,
configuration, and component versions produce the same ordered analytical
values. Byte-identical serialization across runtimes is not required.

## Privacy And Storage

The tool stores local coding transcripts that may contain sensitive data.
Ordinary ingestion retains exact compressed snapshots because parser time
travel is a product requirement. DuckDB files and exported evaluation packets
must therefore be treated as sensitive application data.

Reports remain bounded and disclose raw text only under explicit options.
Evaluation export is explicit and is the only workflow intended to move source
evidence to external judges. Session Doctor itself never sends it.

## Deferred Implementation Choices

The roadmap assigns these decisions to their owning PRs:

- remaining derived SQL table names, constraints, and indexes;
- settling interval;
- built-in validator registry;
- active-time derivation by adapter;
- snapshot and prune CLI option names;
- initial tier and support thresholds after pilot evidence;
- concrete analysis/report schema version numbers.

These choices may change implementation mechanics but cannot weaken the
accepted product contracts above.
