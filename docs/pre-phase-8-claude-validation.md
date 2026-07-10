# Pre-Phase-8 Claude Copied-Local Validation

Date: 2026-07-09

Branch: `feat/claude-completion-validation`

Validation used a temporary isolated copy of one Claude root transcript, its
session directory, and the related subagent and sidecar files. No native
message text, commands, paths, descriptions, tool output, diffs, or file
content are recorded here.

## Source And Parser Evidence

- Claude Code versions observed: `2.1.205`
- copied root sources: 1
- copied subagent sources: 9
- subagent metadata sidecars: 9
- tool-result sidecars: 1
- parsed sessions: 10
- skipped sources: 0
- parse warnings: 0
- unsupported record/content-shape warnings: 0

Every subagent received one deterministic parent link. The five native
`spawnDepth=0` subagents linked to the single root transcript in the same
session directory. The four nested subagents linked through matching
`parentAgentId` and `toolUseId` evidence. The explicitly referenced persisted
tool result correlated after its native absolute path was mapped to the same
`tool-results/<filename>` location inside the isolated copy.

## Normalized Row Counts

| Table | Rows |
| --- | ---: |
| session sources | 10 |
| sessions | 10 |
| raw events | 1,060 |
| messages | 983 |
| tool calls | 364 |
| tool results | 364 |
| command runs | 54 |
| file activities | 282 |
| model usage | 590 |
| parse warnings | 0 |
| analysis runs | 10 |
| message features | 36 |
| session features | 250 |
| session classifications | 10 |

Discovery, ingestion, session listing, analysis for all 10 sessions, and a
Claude-filtered aggregate summary completed successfully.

The repository quality gate passed after the implementation: Ruff formatting
and lint, `ty check`, and 181 tests.

## Privacy Checks

The validation compared substantive native thinking text, tool-result content,
tool input bodies, native result output/edit bodies, subagent descriptions, and
persisted sidecar content against every persisted DuckDB text column. Values
that also occurred through an allowed user/assistant text channel were treated
as message text, not as evidence that a forbidden field was persisted. No
forbidden value was found in normalized storage.

The copied source tree, DuckDB database, command output captures, and temporary
validation scripts were removed after the run.

## Interpretation Limits

This smoke validates the observed source topology and privacy/storage
contracts for one copied session family. It does not establish that every
future Claude Code version or tool-result representation is supported. The
analysis pipeline completed without Claude-specific rules, but classification
labels were not manually adjudicated against the private conversation, so this
run makes no claim about semantic false-positive or false-negative rates.
