# Phase 9 Report And Graph Validation

Date: 2026-07-10

Base revision: `main` at `1f793d9`, plus the Phase 9 completion test and
documentation changes recorded with this note.

Validation covered native Codex, Claude Code, and Pi fixtures plus isolated
temporary copies of one recent completed root per adapter and one linked Claude
sidechain. No source paths, project names, session IDs, messages, commands, tool
output, arguments, diffs, native hashes, file names/content, or report prose are
recorded here.

## Native Fixture End-To-End Coverage

One automated CLI flow now:

1. ingests each adapter's repeated-failure fixture
2. ingests Claude's linked root/nested-sidechain topology fixture
3. restores current analysis for all six sessions without artifacts
4. runs terminal, Markdown, and JSON reports for a top-level session from every
   adapter
5. runs JSON graphs for every adapter and report/graph JSON for a linked Claude
   sidechain
6. verifies exact-session counts, topology-only references, stable graph
   vocabulary, unique IDs, resolvable endpoints, current analysis, privacy mode,
   unchanged table counts, and absence of artifacts

The full automated gate contains 261 tests after adding this coverage.

## Copied-Local Source Evidence

- adapter/parser version: `0.1.0`
- discovered completed-root candidates: Codex 70, Claude 43, Pi 122
- copied sources: 4 total; one Codex root, one Pi root, one Claude root, and one
  linked Claude sidechain
- parsed sessions: 4; 3 top-level and 1 sidechain
- current analysis: 4/4
- native versions present: Codex `0.144.0`, Claude Code `2.1.205`; Pi absent
- parse warnings: one deliberate `codex_turn_aborted` and one
  `missing_tool_result_sidecar` caused by the deliberately minimal Claude copy
- report executions: 4 each in terminal, Markdown, JSON, and evidence-only
  `--show-text` modes
- graph executions: 4

## Normalized Row Counts

| Table | Rows |
| --- | ---: |
| session sources | 4 |
| sessions | 4 |
| raw events | 3,711 |
| messages | 1,508 |
| tool calls | 1,069 |
| tool results | 1,065 |
| command runs | 463 |
| file activities | 313 |
| model usage | 1,045 |
| parse warnings | 2 |
| analysis runs | 4 |
| message features | 74 |
| session features | 100 |
| session classifications | 9 |

## Report Evidence

Across four selected reports, bounded sections retained their full
`total/displayed/omitted` accounting. Notable aggregate counts were:

| Section | Total | Displayed | Omitted |
| --- | ---: | ---: | ---: |
| repeated requests | 19 | 19 | 0 |
| corrections | 3 | 3 | 0 |
| frustration markers | 10 | 10 | 0 |
| scope boundaries | 30 | 21 | 9 |
| ambiguity markers | 8 | 8 | 0 |
| stop/pause markers | 4 | 4 | 0 |
| command failures | 51 | 11 | 40 |
| tool failures | 58 | 11 | 47 |
| repeated failure groups | 1 | 1 | 0 |
| repeated file edits | 0 | 0 | 0 |
| classification references | 349 | 26 | 323 |

Five generated observations were reviewed against their referenced persisted
evidence and fixed template semantics. All five were supported; zero were
unsupported and zero required wording corrections. This small adjudication does
not establish false-positive, false-negative, or statistical calibration rates.

## Graph Evidence

Four graphs contained 8,427 nodes and conservative relations only. Aggregate
structural highlights:

- 4 session anchors and 2 topology-only session references
- 3,711 raw-event provenance nodes
- 1,508 messages, 1,069 tool calls, 1,065 tool results, and 463 command runs
- 313 file activities targeting 106 deduplicated/session-local files
- 183 current-analysis feature/classification nodes and 1 failure-group node
- 1,045 model-usage rows explicitly counted as excluded
- zero duplicate IDs and zero dangling edges
- zero unresolved references except 1,317 explicit `parent_message`
  occurrences whose persisted parent IDs had no exact normalized message target

The unresolved parent references produced no guessed or dangling edges. All
other direct, provenance, tool-result, command, file, repeated-request,
failure-group, score, classification, warning, and topology-reference
relations resolved in the copied sample.

## Privacy, Mutation, And Cleanup

- default terminal/Markdown/JSON report message-text leaks: 0
- graph message-text leaks: 0
- unauthorized `--show-text` disclosures: 0
- graph duplicate IDs: 0
- graph dangling edges: 0
- pre/post table-count changes from report/graph reads: 0
- report/graph artifacts created: 0

The validation retained only versions, warning codes, structural counts,
availability/count summaries, invariant results, and adjudication totals.
Temporary copied roots, the temporary database, rendered output, and the
ad-hoc validation script were removed after the run.
