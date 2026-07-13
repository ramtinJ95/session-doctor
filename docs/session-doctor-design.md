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

### Identity And Ordering

- Source record order is authoritative within one captured source.
- Native causal links are retained where available.
- Timestamps do not invent order across concurrent sources.
- Parent and delegated child timelines remain separate partial orders.
- Project identity uses trustworthy native metadata, an ingestion-time VCS
  root, stored CWD with provenance, or unknown—in that order.
- Model identity is one model, mixed models, or unknown.
- Usage semantics are cumulative, incremental, or unavailable.

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

HumanReviewKind
  judge_disagreement | consensus_audit

ReferenceResolutionStatus
  judge_consensus | human_resolved | ambiguous

ReleaseState
  experimental | validated
```

Finding and relation names are versioned rule-set members rather than global
enums so unsupported families can remain absent without pretending exhaustive
coverage.

## Evaluation Packet Contract

Evaluation uses two documents: a private routing envelope and a judge-visible
packet. Both carry independent schema and annotation-protocol versions.

### Routing Envelope

```text
schema_version
annotation_protocol_version
packet_id
packet_kind
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
  selection_status
  selection_seed_id
  selection_reason
  selected_at

HumanAdjudication
  human_adjudication_id
  schema_version
  annotation_protocol_version
  packet_id
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
  source_human_adjudication_ids
  resolved_at
```

An audit never overwrites the unanimous panel record. Its human adjudication
and the final reference resolution preserve whether consensus was confirmed,
reversed, or left ambiguous.

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

- compressed BLOB codec and level;
- exact SQL table names, constraints, and indexes;
- settling interval;
- built-in validator registry;
- active-time derivation by adapter;
- snapshot and prune CLI option names;
- initial tier and support thresholds after pilot evidence;
- concrete analysis/report schema version numbers.

These choices may change implementation mechanics but cannot weaken the
accepted product contracts above.
