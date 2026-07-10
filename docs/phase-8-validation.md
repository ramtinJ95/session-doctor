# Phase 8 Cross-Adapter Validation

Date: 2026-07-10

Base revision: `main` at `b44b214`, plus the Phase 8 completion test and
documentation changes recorded with this note.

Validation covered native Codex, Claude Code, and Pi fixtures plus temporary
isolated copies of one recent completed root session from each local adapter.
No source paths, messages, commands, tool output, arguments, diffs, file
content, native hashes, or project names are recorded here.

## Native Fixture End-To-End Coverage

One automated flow now:

1. ingests each adapter's repeated-failure fixture through the CLI
2. ingests Claude's native linked root/nested-subagent topology directory
3. restores analysis for all six sessions with `analyze --all --format json`
4. runs a 20-week global trend window that includes both Claude sidechains
5. runs project-scoped monthly trends in terminal format
6. runs `projects list` in JSON
7. verifies all three top-level agent observations, the separate Claude
   sidechain cohort, current coverage, parent-linked analysis, honest
   `insufficient_data`, observed project output, read-only trend behavior, and
   absence of default batch artifacts

The full automated gate contains 235 tests after adding this coverage.

## Copied-Local Source Evidence

- adapter/parser version: `0.1.0`
- discovered completed-root candidates: Codex 70, Claude 43, Pi 121
- copied sources: 3 total, one completed root per adapter
- parsed sessions: 3
- skipped sources: 0
- native versions: Codex `0.144.0`, Claude Code `2.1.201`, Pi not present
- parse warnings: 1 deliberate `codex_turn_aborted`; unsupported warnings: 0
- batch analysis: 3 matching, 3 selected, 3 succeeded, 0 skipped, 0 failed
- current analyzer coverage: 3/3 matching and 3/3 windowed
- unknown project sessions: 0
- exact observed project rows: 2
- malformed family exclusions: 0 orphan, 0 cycle, 0 cross-agent

## Normalized Row Counts

| Table | Rows |
| --- | ---: |
| session sources | 3 |
| sessions | 3 |
| raw events | 3,141 |
| messages | 926 |
| tool calls | 786 |
| tool results | 783 |
| command runs | 280 |
| file activities | 308 |
| model usage | 758 |
| parse warnings | 1 |
| analysis runs | 3 |
| message features | 59 |
| session features | 75 |
| session classifications | 7 |

## Trend And Recurrence Evidence

- weekly top-level output emitted all 12 buckets with 2 non-empty buckets
- monthly top-level output emitted all 12 buckets with 1 non-empty bucket
- no selected session was a sidechain, so sidechain totals and bucket arrays
  remained explicitly empty
- global and project-scoped judgments returned `insufficient_data`
- observed reasons included sparse non-empty periods, too few comparison
  samples, and insufficient earlier coverage/sample coverage
- global output also reported `project_scope_required`, while the explicit
  project scope did not
- recurring failed-command, failed-tool-result, and problematic-file sections
  were empty because no pattern crossed two selected root families
- both terminal and JSON project discovery completed successfully

These sparse results are the expected honest outcome. The smoke did not weaken
sample, density, coverage, or materiality gates and did not fabricate sessions.

## Privacy, Interpretation, And Cleanup

The validation retained only parser/native versions, structural counts,
warning codes, coverage counts, bucket counts, judgment statuses/reasons,
family exclusions, and recurring-section counts. Temporary copied sources,
the validation database, captured command output, and ad-hoc scripts were
removed after the run.

The three private conversations were not manually adjudicated. This smoke
therefore validates deterministic execution, adapter neutrality, coverage and
empty-state honesty, and privacy boundaries; it makes no semantic
false-positive or false-negative claim. Opaque failed-tool fingerprints remain
correlational and are not secret against guessing low-entropy output.
