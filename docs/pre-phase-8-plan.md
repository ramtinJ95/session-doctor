# Pre-Phase-8 Plan: Aggregate Hardening And Claude Code Adapter

Status: in progress.

This document is the implementation contract and progress checklist for the
work that must land before Phase 8 project-level trends begin.

The milestone is split into three sequential pull requests:

1. harden ingestion and cross-adapter aggregate identities
2. add the Claude Code root-session vertical slice
3. complete Claude subagent/sidecar support and copied-local validation

Phase 8 remains postponed until all three pull requests satisfy their acceptance
criteria. The existing phase numbers should not be changed; this is a named
pre-Phase-8 milestone.

## Progress Ledger

Update this table as work starts and lands. A pull request should be marked
complete only after its acceptance criteria and full quality gate pass.

| Workstream | Status | Pull request | Evidence |
| --- | --- | --- | --- |
| PR 1: ingestion and aggregate hardening | complete | #20 | schema v3; 145 tests; full gate; cross-adapter fixture smoke |
| PR 2: Claude root-session vertical slice | in progress | #21 | 159 tests; full gate; root fixture CLI/store smoke |
| PR 3: Claude completion and validation | planned | | |
| Pre-Phase-8 milestone | planned | | |

Allowed status values:

- `planned`
- `in progress`
- `complete`
- `blocked`

## Goal

Before adding project-level trends, make the normalized data trustworthy across
agents and complete Claude Code as the third native adapter.

The target pipeline is:

```text
Codex / Pi / Claude Code sources
  -> adapter-specific tolerant parsing
  -> strict normalized records with canonical command/file identities
  -> DuckDB persistence
  -> deterministic single-session analysis
  -> explainable cross-adapter aggregate summaries
  -> copied-local validation evidence
```

By the end of this milestone:

- equivalent commands from different adapters group together conservatively
- equivalent file paths from different adapters group together
- ingestion failures are explicit and return meaningful exit status
- aggregate risk output exposes every score that can affect ranking
- aggregate evidence does not contain duplicate event/message IDs
- Claude root and subagent sessions can be ingested, listed, analyzed, and
  summarized through the existing agent-neutral pipeline
- Claude auxiliary files are either correlated deliberately or ignored
  deliberately
- copied-local validation results are recorded without retaining private
  content
- Phase 8 can consume stable cross-agent identities instead of inventing its
  own normalization rules

## Why This Must Precede Phase 8

The Phase 7 smoke test exposed two cross-adapter identity gaps:

- Codex may preserve a shell wrapper such as `/bin/zsh -lc '<command>'` while Pi
  stores the inner command, causing one logical failure to form two groups.
- one adapter may store an absolute file path while another stores the same path
  relative to the project, causing one logical file to form two groups.

Trend queries built on those values would fragment repeated patterns while
appearing precise. Canonical identities therefore belong in normalized data,
not as Phase 8 report-specific cleanup.

Claude parsing is also part of the design's first complete product iteration and
third in the adapter implementation order, but the existing roadmap does not
assign it to a phase. This milestone gives that work an explicit owner without
renumbering Phase 8 through Phase 10.

## Starting Point

Phases 1 through 7 currently provide:

- strict normalized Pydantic schemas
- DuckDB persistence and source-scoped replacement
- Codex and Pi discovery, parsing, ingestion, and analysis
- deterministic feature extraction and Phase 6 scoring/classification
- aggregate summary queries and terminal/JSON output
- Claude discovery and source-kind classification
- reserved Claude parsing that currently raises `NotImplementedError`
- 128 passing tests and a green formatting, lint, and type-check gate at the
  start of planning

Known improvement work entering this milestone:

- canonical command identity across shell wrappers
- canonical file identity across relative and absolute paths
- overly broad ingestion exception handling
- incomplete risk-score exposure and inconsistent JSON score precision
- duplicate aggregate marker evidence IDs
- stale package-shape documentation
- obsolete machine-specific paths in the design document
- no durable, privacy-safe record of copied-real/local smoke validation

## Current Claude Structural Evidence

A read-only structural inspection on 2026-07-09 found:

- 43 root-session JSONL files
- 50 subagent JSONL files
- 50 subagent metadata files
- 5 persisted tool-result files
- 11 memory files

No message text or tool-result content was printed during the inspection.

Observed root record types include:

```text
assistant
user
system
attachment
file-history-snapshot
queue-operation
last-prompt
permission-mode
mode
ai-title
```

Observed message content forms include:

```text
string
text
thinking
tool_use
tool_result
```

Observed tools include Bash, Read, Edit, Write, Agent, WebFetch, ToolSearch,
AskUserQuestion, and Skill. Tool-result data may be an object or a string.

The inspected corpus also confirms:

- a root transcript can span multiple Claude Code versions
- a root transcript can contain more than one `cwd`
- many non-message records omit `sessionId`
- subagent records use `isSidechain=true`
- subagent JSONL contains `agentId` and `sourceToolAssistantUUID` linkage
- subagent `.meta.json` contains useful agent type, task, model, nesting, and
  permission metadata
- persisted tool-result files can be large and must never be stored verbatim by
  default

These observations are structural evidence, not a stable public Claude schema.
The parser must expect version drift.

## Resolved Implementation Decisions

The milestone should follow these decisions:

- keep the existing phase numbering and use this named milestone
- use three sequential pull requests with no overlapping implementation work
- merge PR 1 before either Claude parsing PR
- do not declare Claude parsing complete until PR 3 lands
- parse evolving native Claude records with tolerant dictionary access
- keep emitted normalized Pydantic models strict
- preserve every valid native JSONL record as a hashed `RawEvent`
- preserve original native command/path values for provenance
- derive canonical command/path identities once in the normalization layer
- allow a clean pre-1.0 schema/model change if first-class canonical fields make
  the design clearer
- do not preserve old local DuckDB compatibility if it compromises the model;
  rebuild fixtures and local databases instead
- use event-level `cwd` when available and record cwd drift explicitly
- never decode an encoded Claude project directory as the source of truth for
  `cwd`
- do not persist assistant thinking/reasoning text
- do not persist raw tool output, command output, diffs, edit bodies, write
  content, or tool-result sidecar content
- use synthetic fixtures in automated tests
- use copied local files only for manual smoke validation
- keep Phase 8 trends, report generation, graph projection, exports, wrappers,
  LLM calls, embeddings, and ML dependencies out of this milestone

## Cross-Cutting Data Contracts

### Canonical Command Identity

Command normalization must be conservative and explainable.

Required behavior:

- preserve the native command text
- normalize insignificant surrounding whitespace
- unwrap only recognized shell invocation forms where one payload is clearly
  passed to `sh`, `bash`, or `zsh` through `-c`/`-lc`
- do not attempt general shell equivalence, command reordering, variable
  expansion, alias expansion, or semantic interpretation
- derive a stable canonical value or signature for grouping
- retain a redacted canonical example for terminal/JSON display
- keep secret redaction separate from identity derivation
- attach normalization metadata describing whether and how a wrapper was
  removed

The exact storage shape should be first-class rather than buried in arbitrary
metadata. A clean pre-1.0 model may add canonical command fields to
`CommandRun` and DuckDB.

### Canonical File Identity

Required behavior:

- preserve the native path exactly as captured
- normalize `.` and `..` lexically
- anchor relative paths to the event-level cwd when available, otherwise the
  session cwd/project path
- do not require the target file to exist
- do not use filesystem resolution that changes identity through symlinks
- retain an absolute canonical path when a trustworthy base exists
- retain a project-relative identity when the canonical path is under the
  project root
- group by project plus project-relative path when possible, otherwise by the
  canonical path
- display home paths with the existing redaction behavior
- record missing-base ambiguity rather than inventing an absolute path

The exact storage shape should be first-class. A clean pre-1.0 model may add
canonical/project-relative fields to `FileActivity` and DuckDB.

### Ingestion Failure Semantics

Required behavior:

- introduce explicit recoverable source read/format exceptions
- let database errors, invariant failures, programmer errors, and unexpected
  exceptions abort ingestion
- fail immediately when explicit single-file ingestion cannot parse or persist
  that file
- for directory/default-root ingestion, continue only after known recoverable
  per-source failures
- show every skipped source and a stable error category
- return a non-zero exit status if any requested source was skipped
- never describe a database write failure as a skipped parser source
- keep malformed individual JSONL records non-fatal when an adapter can emit a
  precise `ParseWarning` and continue the rest of the file

### Claude Native Versus Normalized Validation

Claude's on-disk format is external and version-drifting. Native records should
therefore be read through tolerant dictionary helpers, with missing/unknown
fields converted into deliberate counts or parse warnings.

Normalized entities remain strict Pydantic models with `extra="forbid"`. The
adapter boundary, not the normalized model, owns tolerance.

## PR 1: Ingestion And Aggregate Hardening

### Goal

Make existing Codex/Pi normalized records and aggregate summaries reliable
enough for a third adapter and future trend queries.

### Likely Codepaths

```text
src/session_doctor/schemas/tools.py
src/session_doctor/schemas/files.py
src/session_doctor/adapters/common.py
src/session_doctor/adapters/codex_commands.py
src/session_doctor/adapters/codex_files.py
src/session_doctor/adapters/pi_commands.py
src/session_doctor/adapters/pi_files.py
src/session_doctor/ingest_workflow.py
src/session_doctor/cli.py
src/session_doctor/store/migrations.py
src/session_doctor/store/models.py
src/session_doctor/store/row_loaders.py
src/session_doctor/store/row_mappers.py
src/session_doctor/store/summary_readers.py
src/session_doctor/summary_payload.py
src/session_doctor/analysis/feature_factories.py
tests/
README.md
docs/session-doctor-design.md
```

### Tasks

Canonical commands:

- [x] Define the shared canonical command identity contract.
- [x] Add first-class normalized command fields if the clean model requires
  them.
- [x] Normalize recognized `sh`/`bash`/`zsh -c` and `-lc` wrappers.
- [x] Preserve native command text and normalization provenance.
- [x] Make repeated-failure and aggregate grouping use the canonical identity
  where appropriate.
- [x] Keep display redaction applied after identity derivation.
- [x] Add positive, negative, and near-miss normalization tests.

Canonical files:

- [x] Define the shared canonical file identity contract.
- [x] Add first-class canonical/project-relative path fields if required.
- [x] Anchor relative Codex and Pi paths against trustworthy cwd/project data.
- [x] Preserve native paths and missing-base ambiguity.
- [x] Make same-file analysis and aggregate grouping use canonical identity.
- [x] Add tests for relative/absolute equivalence, `.`/`..`, outside-project
  paths, missing cwd, home redaction, and non-existent paths.

Ingestion failures:

- [x] Define explicit recoverable source exceptions.
- [x] Stop catching every `Exception` in `ingest_sources()`.
- [x] Fail explicit single-file ingestion immediately on source failure.
- [x] Continue directory ingestion only for known recoverable source failures.
- [x] Return non-zero when any requested source is skipped.
- [x] Abort immediately on DuckDB/persistence and unexpected failures.
- [x] Add CLI tests for complete success, partial recoverable failure, total
  recoverable failure, and persistence failure.

Summary and evidence:

- [x] Add `prompt_clarity_risk` and `project_complexity_signal` to recent-risk
  models, terminal output, and JSON.
- [x] Round JSON risk scores consistently while keeping them numeric.
- [x] Ensure `max_risk_score` can always be explained by an exposed score.
- [x] Deduplicate aggregate message/source event IDs while preserving
  marker-family message features.
- [x] Add regression tests for score visibility, precision, and evidence
  deduplication.

Documentation and validation scaffolding:

- [x] Refresh the design document's stale package-shape section.
- [x] Replace obsolete absolute home paths with `~` or neutral examples.
- [x] Document the canonical command/path contracts.
- [x] Add a privacy-safe copied-local validation note template.
- [x] Update this plan's PR 1 status and evidence when complete.

### PR 1 Acceptance Criteria

- [x] Equivalent Codex and Pi commands group together when they differ only by
  a recognized shell wrapper.
- [x] Commands that are not provably equivalent remain separate.
- [x] Equivalent relative and absolute file paths group together under the same
  project.
- [x] Ambiguous paths remain explicit instead of being assigned a fabricated
  base.
- [x] Native command and path provenance remains available.
- [x] Single-file source failure exits non-zero.
- [x] Partial directory failure is visible and exits non-zero after processing
  remaining valid sources.
- [x] Persistence and unexpected errors abort instead of becoming skipped
  sources.
- [x] Summary output exposes all five Phase 6 scores with stable numeric
  precision.
- [x] Aggregate evidence IDs are unique.
- [x] No new raw sensitive content is persisted or displayed.
- [x] Existing Codex/Pi ingestion, analysis, and summary behavior remains green
  except for the deliberate contract changes above.
- [x] The full quality gate passes.

## PR 2: Claude Root-Session Vertical Slice

### Goal

Prove the existing normalized pipeline end to end for ordinary Claude Code root
transcripts before adding subagent and sidecar correlation.

The target vertical slice is:

```text
Claude root JSONL
  -> Claude adapter
  -> normalized session/events/messages/tools/commands/files/usage
  -> DuckDB
  -> sessions list
  -> analyze
  -> summary
```

### Scope Boundary

PR 2 parses only `SourceKind.ROOT_SESSION`.

Subagent JSONL, subagent `.meta.json`, persisted `tool-results/*`, memory files,
and auxiliary files remain deliberately unparsed until PR 3. Discovery should
still classify them correctly and default ingestion should not mistake them for
root sessions.

### Likely Codepaths

```text
src/session_doctor/adapters/claude.py
src/session_doctor/adapters/claude_*.py
src/session_doctor/adapters/common.py
src/session_doctor/cli.py
src/session_doctor/cli_options.py
src/session_doctor/cli_renderers.py
tests/fixtures/claude/
tests/test_claude_adapter.py
tests/test_cli.py
tests/test_store.py
```

### Parsing Rules

Session metadata:

- use native `sessionId` when present and validate consistency across records
- derive the stable internal session ID from agent, source, and native/filename
  identity
- use the first chronological trustworthy cwd as the session cwd/project hint
- preserve distinct observed cwd values and cwd-change count in metadata
- derive start/end timestamps from the normalized event timeline
- preserve Claude Code version, git branch, entrypoint, model, and provider when
  present
- do not infer cwd by decoding the project directory name

Messages:

- normalize assistant, user, and system records deliberately
- extract assistant text only from `text` blocks
- record `thinking` as a block type/count without persisting thinking text
- extract user string/text content without folding `tool_result` content into
  user text
- preserve message IDs, parent IDs, timestamps, content block types, model, stop
  reason, and sidechain flags where available
- keep unsupported content blocks non-fatal and visible

Tools and results:

- create tool calls from assistant `tool_use` blocks
- hash full arguments and preserve only selected safe metadata
- link user `tool_result` blocks through `tool_use_id`
- handle tool-result content represented as a string or structured blocks
- treat missing `is_error` as unknown, not false
- use top-level `toolUseResult` only for safe structural/error/output metadata
- hash output and record length; never store raw output

Commands and files:

- create command runs for Bash tool calls/results
- preserve command text, cwd, exit/error/interruption state, duration, and hashed
  stdout/stderr
- create file activity for Read, Edit, Write, and other clearly mapped file
  tools
- use the canonical identity contracts established in PR 1
- hash structural content metadata without storing write/edit bodies or diffs

Usage:

- normalize model/provider and available token counts
- preserve unmapped safe usage keys only as metadata
- do not invent costs that Claude does not expose

Other record types:

- preserve every valid record as a raw event
- count known metadata-only types without noisy warnings
- emit warnings for unknown future types and unsupported shapes
- preserve system/API error evidence without storing raw large output

### Tasks

Fixtures and parser foundation:

- [x] Add small synthetic root-session fixtures covering observed structural
  variants without copying private content.
- [x] Add Claude record readers, raw-event construction, and session metadata
  extraction.
- [x] Add deliberate known metadata-only type handling.
- [x] Add malformed JSON, missing ID, cwd drift, version drift, and unknown type
  tests.

Normalized entities:

- [x] Parse user, assistant, and system messages.
- [x] Exclude thinking text while preserving safe structural metadata.
- [x] Parse tool calls and correlate tool results.
- [x] Parse Bash command runs with hashed output.
- [x] Parse safe file activity for read/edit/write operations.
- [x] Parse model usage.
- [x] Preserve parent/native IDs and timestamps.
- [x] Add privacy regression assertions for arguments, output, patches, thinking,
  and write/edit content.

CLI and persistence:

- [x] Allow `ingest --agent claude`.
- [x] Make default Claude ingestion select root sessions only in PR 2.
- [x] Prove repeated root-session ingestion delete-and-replaces old rows.
- [x] Show Claude sessions and normalized counts through `sessions list`.
- [x] Run `analyze` over an ingested Claude fixture without Claude-specific
  analysis branches.
- [x] Include Claude sessions in filtered/unfiltered summary output.
- [x] Add terminal and JSON CLI coverage.
- [x] Update this plan's PR 2 status and evidence when complete.

### PR 2 Acceptance Criteria

- [x] `ClaudeCodeAdapter.parse_source()` returns a normalized bundle for
  representative root fixtures.
- [x] Every valid fixture record has a raw event.
- [x] Messages, tool calls/results, commands, files, usage, and warnings are
  normalized deliberately.
- [x] Unknown record/content shapes warn without stopping the file.
- [x] Native version and cwd drift do not crash parsing.
- [x] Thinking text, raw tool output, raw command output, diffs, and write/edit
  bodies are not persisted.
- [x] `ingest --agent claude --source <root-fixture>` succeeds.
- [x] Default Claude ingestion does not ingest memory/metadata/sidecar files as
  root sessions.
- [x] Re-ingestion does not duplicate rows.
- [x] `sessions list`, `analyze`, and `summary --agent claude` work.
- [x] Existing Codex/Pi behavior remains green.
- [x] Claude is documented as root-session MVP only, not complete.
- [x] The full quality gate passes.

## PR 3: Claude Completion And Validation

### Goal

Complete the Claude source topology by adding subagent linkage, metadata and
tool-result sidecar correlation, deliberate memory handling, and copied-local
validation.

### Subagent Identity And Linkage

Subagent JSONL should produce a separate normalized `Session`:

- `is_sidechain=true`
- a stable internal session ID derived from its source path and native agent
  identity
- `parent_source_id` pointing to the root transcript source when determinable
- `parent_session_id` pointing to the root normalized session when determinable
- preserved `agentId`, `sourceToolAssistantUUID`, nesting depth, task kind,
  agent type, model, and permission metadata where available

Do not assume a subagent's native `sessionId` is unique from the root session.
Ambiguous or missing parent linkage should produce explicit warnings rather
than fabricated relationships.

### Sidecar Handling

Subagent metadata:

- correlate `agent-*.meta.json` with the matching subagent transcript
- enrich the subagent session/source metadata
- do not create a standalone normalized session for the metadata file
- warn on malformed, orphaned, or mismatched metadata

Persisted tool results:

- correlate sidecars only through an explicit transcript/path/tool-result link
  or another deterministic identity
- use the sidecar to fill missing output hash/length/truncation facts when useful
- never store or display raw sidecar content
- do not create a standalone normalized session for a sidecar
- keep orphaned sidecars visible as counts/warnings without guessing linkage

Memory and auxiliary files:

- keep memory files excluded from session ingestion
- keep unrelated auxiliary files excluded
- report discovery counts so ignored categories remain visible

### Tasks

Subagents:

- [ ] Add synthetic root-plus-subagent fixture trees.
- [ ] Derive parent source/session identity from the directory and event linkage.
- [ ] Parse subagent JSONL through the shared Claude record handlers.
- [ ] Preserve sidechain/agent linkage and metadata.
- [ ] Add missing, ambiguous, and mismatched parent-link tests.
- [ ] Prove root and subagent re-ingestion replacement behavior.

Metadata and tool-result sidecars:

- [ ] Correlate subagent `.meta.json` without creating standalone sessions.
- [ ] Correlate explicitly referenced tool-result sidecars.
- [ ] Hash sidecar output and record safe length/truncation/error facts.
- [ ] Add malformed/orphaned sidecar warnings.
- [ ] Assert that raw sidecar content never enters models, DuckDB, artifacts, or
  CLI output.

End-to-end behavior:

- [ ] Analyze root and subagent Claude fixtures through the existing analysis
  workflow.
- [ ] Include root/subagent Claude sessions in aggregate summary counts and
  filters.
- [ ] Verify canonical commands/files group correctly across Codex, Pi, and
  Claude fixtures.
- [ ] Add discovery/ingest output for parsed versus deliberately ignored Claude
  source kinds.

Copied-local validation:

- [ ] Copy selected root and subagent sources to a temporary isolated tree.
- [ ] Run discovery, ingestion, listing, analysis, and summary against the copy.
- [ ] Record source counts, Claude versions, normalized row counts, warning
  counts/codes, unsupported shapes, and observed false positives.
- [ ] Record whether any session/file was skipped and why.
- [ ] Confirm privacy invariants without retaining message or output content.
- [ ] Remove temporary copied sources and databases after validation.

Documentation:

- [ ] Mark Claude parsing complete in `docs/session-doctor-design.md`.
- [ ] Update the implemented vertical slice and current limitations.
- [ ] Update README phase/capability and usage sections.
- [ ] Document Claude root/subagent/sidecar semantics.
- [ ] Update this plan's progress ledger and PR 3 status/evidence.
- [ ] Mark this milestone complete only after every milestone criterion passes.

### PR 3 Acceptance Criteria

- [ ] Root and subagent Claude sessions are normalized as separate sessions.
- [ ] Deterministic parent links are correct and ambiguous links warn.
- [ ] Metadata and tool-result sidecars enrich related records without becoming
  sessions.
- [ ] Memory and auxiliary files remain deliberately excluded.
- [ ] Root and subagent sessions can be listed, analyzed, and summarized.
- [ ] Claude-derived analysis requires no Claude-specific feature or
  classification rules.
- [ ] Canonical command/file identities work across all three adapters.
- [ ] No raw thinking, tool output, command output, sidecar content, diff, or
  write/edit body is persisted or displayed.
- [ ] Copied-local validation evidence is recorded in a privacy-safe form.
- [ ] Design and README describe the actual completed behavior.
- [ ] The full quality gate passes.

## Milestone Acceptance Criteria

The pre-Phase-8 milestone is complete when:

- [ ] PR 1, PR 2, and PR 3 are complete in order.
- [ ] All PR-specific acceptance criteria are checked.
- [ ] Codex, Pi, and Claude pass the same ingest/list/analyze/summary contract.
- [ ] Cross-adapter command and file aggregation has explicit regression tests.
- [ ] Ingestion cannot silently turn systemic failures into successful skips.
- [ ] Aggregate ranking is fully explainable from exposed score fields.
- [ ] Claude root and subagent topology is represented without guessed links.
- [ ] Ignored Claude source categories remain visible and deliberate.
- [ ] Privacy invariants are covered by fixtures and copied-local validation.
- [ ] No Phase 8 trend query or command has been introduced.
- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run ty check` passes.
- [ ] `uv run pytest -q` passes.
- [ ] `docs/session-doctor-design.md` and README match the implemented state.
- [ ] The progress ledger at the top of this document is complete.

## Suggested Validation Note Format

Do not record copied message text, commands, paths outside home-redacted form,
tool output, diffs, or file content.

```text
Date:
Commit:
Claude Code versions observed:
Copied root sources:
Copied subagent sources:
Metadata sidecars:
Tool-result sidecars:
Parsed sessions:
Skipped sources:
Normalized row counts:
Parse warning counts by code:
Unsupported structural shapes:
Observed analysis false positives/negatives:
Privacy checks:
Temporary files removed:
Quality gate result:
```

## Implementation Order

1. Review and approve this plan.
2. Implement and merge PR 1.
3. Rebuild local/fixture DuckDB data if the clean model changes.
4. Implement and merge PR 2.
5. Implement and merge PR 3.
6. Update the progress ledger and mark the milestone complete.
7. Reassess and plan Phase 8 using the resulting three-adapter aggregate model.

## Open Questions During Implementation

No product decision currently blocks starting PR 1.

Implementation should stop for steering if evidence forces a material change to
one of these contracts:

- a shell wrapper cannot be unwrapped without changing command meaning
- a relative file path has no trustworthy cwd/project base
- Claude parent linkage is ambiguous across more than one root session
- a sidecar cannot be correlated without guessing
- copied-local evidence shows a proposed normalized field has unstable meaning
- a privacy requirement conflicts with a desired analysis feature

In those cases, preserve the uncertainty and compare the available choices
before changing the plan or implementation.
