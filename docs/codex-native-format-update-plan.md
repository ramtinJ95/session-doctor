# Current Codex Native Format Update Plan

Status: approved; PR 1 complete, PR 2 response-command implementation in review.

## Purpose

Bring Codex `0.144.0` response-item execution and newer record shapes into the
same deterministic, privacy-safe normalized model already used by Claude Code
and Pi. This is a dogfood correction to the `v0.1.0` baseline, not a new causal
analysis model.

The durable boundaries remain unchanged:

- source files are read-only;
- normalized DuckDB rows are local;
- command arguments and tool output are transformed during parsing and are not
  retained verbatim;
- reports and graphs remain read-only and text-hidden by default;
- unsupported relationships stay explicit rather than guessed;
- pre-1.0 databases may be rebuilt instead of migrated.

## Native Evidence

A privacy-safe structural scan covered all 70 locally discovered Codex JSONL
sources. It retained only record/payload types, field names, value types,
cardinalities, and recognized envelope markers. It retained no source paths,
commands, argument values, output text, IDs, hashes, prompts, or messages.
The retained aggregate is `docs/codex-native-format-scan.json`; its explicit
allowlist and denylist make the evidence reviewable without retaining native
session material.

Observed response-item execution contract:

- 1,775 `function_call` records named `exec_command`, all with a JSON string
  containing `cmd`; 1,757 also had `workdir`.
- 1,234 `custom_tool_call` records named `exec`, all with a non-JSON free-form
  string in `input`.
- 226 `function_call` records named `write_stdin`, all with integer
  `session_id`, string `chars`, and bounded-output options.
- Every one of those 3,235 calls had a call-ID-matched response-item output in
  the scanned snapshot.
- All 1,775 `exec_command` outputs had `Chunk ID` and `Wall time` envelope
  headers. 1,610 reported a process exit code; 165 reported a still-running
  process session.
- 161 `write_stdin` outputs reported a process exit and 62 reported a
  still-running process. Three had no recognized process envelope.
- `exec` outputs were opaque and had no recognized execution envelope.

The current adapter produced 3,009 `exec`/`exec_command` tool calls but zero
`CommandRun` rows because it only normalizes legacy
`event_msg.exec_command_end` records.

Observed newer records:

- 63 `response_item.agent_message` records with `author`, `recipient`, content,
  and internal turn metadata;
- 72 `event_msg.sub_agent_activity` records with agent/thread/event fields;
- 63 `inter_agent_communication_metadata` records with trigger-turn data;
- one matched `tool_search_call`/`tool_search_output` pair.

The scan found no malformed JSONL lines.

## Contract Decisions

### Response-item commands

`exec_command` becomes a `CommandRun` when and only when:

1. `arguments` is valid JSON;
2. the decoded value is an object;
3. `cmd` is a non-empty string; and
4. a call ID is present.

The command uses existing canonical command identity rules. `workdir` is used
only when it is a string. Numeric limits and TTY settings do not enter command
identity.

Free-form `custom_tool_call` `exec` becomes a `CommandRun` when `input` is a
non-empty string and a call ID is present. Its output remains opaque, so exit
status stays unknown unless a future evidenced contract adds one.

Malformed recognized execution calls remain generic tool calls and emit a
stable warning. They never produce a guessed command.

### Correlation and precedence

Response calls and outputs correlate only by exact non-empty native call ID.
They never borrow neighboring records. Cardinality behavior is exact:

- duplicate calls for one native call ID are wholly excluded from normalized
  tool, result, and command rows for that ID and emit
  `ambiguous_codex_tool_call` with structural counts;
- one call with no output keeps its tool and command rows with unknown outcome
  and emits `missing_codex_tool_result`;
- one call with multiple outputs keeps the tool and all uniquely identified
  result rows, keeps the command with unknown outcome, and emits
  `ambiguous_codex_tool_results`;
- an output without a call is retained as an unlinked result and emits
  `orphan_codex_tool_result`; it does not receive a guessed tool-call edge.

Warning metadata contains only the warning code and cardinalities, never native
call IDs, arguments, commands, or output.

If a source contains both a response-item command and a legacy
`event_msg.exec_command_end` for the same call ID, the legacy record wins because
it carries explicit command lifecycle fields. Exactly one `CommandRun` is
emitted.

The command's `tool_call_id` uses the same stable call-ID convention as the
existing `ToolCall`, so existing graph `runs_command` and tool-result edges work
without schema changes.

### Execution output envelope

For `exec_command`, only this exact three-line prefix is structural:

1. `Chunk ID: ...`
2. `Wall time: ... seconds`
3. either `Process exited with code N` or `Process running with session ID N`

The parser may extract integer exit code from the exited form. Remaining text is
hashed as undifferentiated stdout; stderr stays unknown. Chunk IDs, process IDs,
and wall-time values are not persisted in command metadata. If the exact prefix
is absent, output remains opaque and exit status stays unknown.

### `write_stdin`

`write_stdin` remains a generic continuation tool for this update. It does not
become a separate command and does not retroactively change the initial command.
This deliberately leaves 165 initially running native executions with unknown
command outcomes. Joining continuations requires a separately evidenced process
lifecycle contract and is not guessed here.

### Newer multi-agent records

`response_item.agent_message` is inter-agent communication, not an ordinary
user/assistant message. The current core message role model cannot represent
its author/recipient semantics without blending cohorts, so it remains a raw
provenance event and becomes an explicit expected exclusion rather than a parse
warning.

`sub_agent_activity` and `inter_agent_communication_metadata` likewise remain
raw provenance plus explicit expected-exclusion counts. They do not create
sessions, parentage, messages, or causal delegation edges. No checked native
evidence ties them to separately discovered source files.

The matched tool-search pair becomes a generic `tool_search` call/result with
exact call-ID correlation. Tool definitions and search arguments are hashed or
counted structurally and never retained verbatim.

## Four-PR Delivery

### PR 1 — Contract and synthetic fixtures

- Add this reviewed contract.
- Add a wholly synthetic current-format fixture.
- Add wholly synthetic duplicate, missing, multiple-output, and orphan cases.
- Test fixture shape, exact call/output cardinality, envelope variants, retained
  scan schema, and absence of copied-local data.

No production parser behavior changes in PR 1.

### PR 2 — Response-item command reconstruction

- Add strict typed parsing for `exec_command` arguments and envelopes.
- Add free-form `exec` command normalization.
- Correlate response calls/results in memory.
- Preserve legacy precedence and exact tool links.
- Cover malformed, missing, duplicate, running, failed, successful, opaque, and
  privacy cases through adapter, store, analysis, report, and graph tests.

### PR 3 — Newer record handling

- Normalize matched tool-search call/results.
- Convert evidenced multi-agent records to named expected exclusions.
- Preserve raw provenance without inventing topology or normal messages.
- Add warning/exclusion and no-private-content tests.

### PR 4 — Native rebuild validation

- Add synthetic end-to-end current-format validation.
- Rebuild/reingest all local Codex sources after all code PRs merge.
- Reanalyze affected sessions without artifacts.
- Record only structural counts, warning codes, graph integrity, deterministic
  reports, and privacy checks.
- Compare command coverage and classifications while explicitly noting cohort
  and parser-version differences.

Each PR starts from updated `main`, receives blocker-only review/fix cycles until
exactly `NO FINDINGS`, and is rebase-merged before the next branch.

## Acceptance Gates

The update is complete only when:

- all current eligible Codex sources ingest without an unrecoverable failure;
- current response-item `exec_command` and free-form `exec` calls produce bounded
  command coverage under the strict contract;
- no exact call ID creates more than one command;
- malformed/ambiguous calls stay explicit;
- output text, argument text, native IDs, and source paths do not enter default
  reports or graphs;
- graph IDs are unique and all endpoints resolve;
- report and graph reads are deterministic and non-mutating;
- all 285-session analysis coverage is current after the rebuild;
- no artifacts are written by batch analysis;
- the final review reports exactly `NO FINDINGS`.
