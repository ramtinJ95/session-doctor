# Current Codex Native Format Rebuild Validation

Date: 2026-07-11

Validated Codex CLI: `0.144.0`

Validation target: synchronized `main` after the response-command and
current-record implementation PRs, plus this documentation-only completion PR.

The rebuild used the default local database because the user explicitly
approved replacing all persisted Codex normalized and analysis rows. Native
source files remained read-only. Batch analysis used `write_artifacts=false`.
No source path, project/file name, command, argument value, output text, prompt,
message text, native ID, hash, fingerprint, or database was copied into this
record.

## Rebuild Result

- discovered eligible Codex sources: 70
- successfully reingested: 70
- failed/skipped sources: 0
- selected Codex analyses: 70
- successful Codex analyses: 70
- failed/skipped analyses: 0
- current Codex analysis coverage: 70/70
- current machine-wide analysis coverage: 285/285
- generated artifacts: 0

The rebuild left Claude Code and Pi normalized rows and current analyses in
place. The default database was 129 MiB after the rebuild.

## Structural Before And After

| Row or state | Before | After |
| --- | ---: | ---: |
| sessions | 70 | 70 |
| raw events | 16,390 | 16,390 |
| messages | 1,376 | 1,376 |
| tool calls | 3,441 | 3,442 |
| tool results | 3,443 | 3,444 |
| command runs | 0 | 3,009 |
| commands with observed nonzero exit | 0 | 125 |
| commands with deliberately unknown outcome | 0 | 1,399 |
| file activities | 762 | 762 |
| model-usage rows | 2,965 | 2,965 |
| unsupported-format warnings | 202 | 0 |
| explicit `codex_turn_aborted` warnings | 7 | 7 |

The added tool call/result is the one evidenced matched tool-search pair.
Unknown command outcomes comprise free-form opaque `exec` results and
`exec_command` calls whose initial result reported a still-running process.
`write_stdin` remains a separate continuation tool by contract and does not
retroactively supply a guessed command outcome.

Forty-nine sessions contained at least one normalized command. Twenty-seven had
at least one observed nonzero command exit. No command ID was duplicated and no
command-to-tool link was dangling in the in-memory native check.

## Expected Exclusions

The full current snapshot retained raw-event provenance and counted these known
non-diagnostic records instead of warning or inventing semantics:

| Exclusion | Count |
| --- | ---: |
| `event_msg.sub_agent_activity` | 72 |
| `record.inter_agent_communication_metadata` | 63 |
| `response_item.agent_message` | 63 |
| `event_msg.mcp_tool_call_end` | 2 |

The two MCP lifecycle records had call IDs matching already-normalized
response-item call/output pairs. Inter-agent records did not create messages,
sessions, parentage, intent, success/failure, or causal graph edges.

## Diagnostic Validation Across All Codex Sessions

Every one of the 70 rebuilt sessions was loaded through the exact-session
diagnostic reader. For each session, the validator independently built two
reports and two graphs and checked equality.

| Check | Result |
| --- | --- |
| current analysis | 70/70 pass |
| deterministic default report | 70/70 pass |
| deterministic graph | 70/70 pass |
| default report message text absent | 70/70 pass |
| private normalized keys absent | 70/70 pass |
| unique graph node IDs | 70/70 pass |
| unique graph edge IDs | 70/70 pass |
| all graph endpoints resolve | 70/70 pass |
| unresolved graph references | 0 |
| report/graph row-count mutation | 0 |

The 70 graphs contained 30,975 nodes and 78,831 edges. Validation retained only
these aggregate cardinalities, not graph payloads or identifiers.

## Analysis Observations

The parser correction changed analysis inputs, so before/after classifications
are not measurements of changed agent behavior. They are two analyses of the
same sessions under different normalization coverage.

| Classification | Before | After |
| --- | ---: | ---: |
| healthy | 54 | 51 |
| agent looping | 3 | 3 |
| agent misunderstood | 1 | 1 |
| prompt ambiguous | 2 | 2 |
| repo complexity high | 0 | 5 |
| resolved after corrections | 2 | 1 |
| task too large | 0 | 1 |
| tooling blocked | 0 | 2 |
| user stuck | 5 | 5 |

The deterministic risky-session gate changed from 8/70 to 10/70. Average
friction changed from 0.049 to 0.066 and average stuckness from 0.067 to 0.074.
These differences show that the outputs are sensitive to expanded normalization
coverage; they do not isolate a cause or establish agent quality.

Codex recurrence now contains one failed-command pattern supported by four
events in four sessions from four distinct root families. The observation is
structural and does not expose or describe the command.

## Automated Gate

The repository gate contains 283 tests before this completion record, including:

- wholly synthetic current response-item and cardinality fixtures;
- failed, successful, running, opaque, malformed, missing, duplicate,
  multiple-output, orphan, and legacy-precedence command cases;
- strict execution-envelope handling;
- tool-result error derivation from validated nonzero exits;
- store, analysis, report, and graph command integration;
- tool-search privacy and exact linkage;
- expected-exclusion and no-private-content checks;
- all prior adapter, persistence, analysis, report, graph, trend, recurrence,
  integration, and release contracts.

Final local checks are recorded only after the completion test is added:

```text
ruff format --check: pass
ruff check: pass
ty check: pass
pytest: 284 passed
git diff --check: pass
```

## Remaining Deliberate Limitation

The update does not join `write_stdin` continuations back to an initial
still-running command. Doing so safely requires a separate native process
lifecycle contract. Until then, those command outcomes remain explicitly
unknown rather than inferred.
