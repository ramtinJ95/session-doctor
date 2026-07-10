# session-doctor Design Plan

## Intent

`session-doctor` is a local-first CLI for analyzing AI agent sessions across
tools like Codex, Claude Code, and Pi.

The goal is to detect when a session shows signs that:

- the user is stuck
- the user is repeating the same request
- the agent is looping or misunderstanding the task
- prompts are unclear
- the task is too large or too hard for the current agent
- tooling or environment failures are blocking progress
- a project has grown complex enough that refactoring or task decomposition is
  warranted

The initial product should diagnose individual sessions well. The design should
also preserve enough structure to support project-level trends across many
sessions later, because that is the long-term goal.

## Product Shape

The durable product is a CLI, not a skill.

Skills, MCP servers, and agent integrations can wrap the CLI later. The CLI
itself should remain usable directly by a person and by any agent that can run
terminal commands.

Current command shape:

```bash
session-doctor version
session-doctor doctor
session-doctor adapters list [--scan]
session-doctor db init
session-doctor db info
session-doctor ingest --agent codex [--source PATH] [--db PATH]
session-doctor ingest --agent claude [--source PATH] [--db PATH]
session-doctor ingest --agent pi [--source PATH] [--db PATH]
session-doctor sessions list
session-doctor analyze <session-id> [--format terminal|json] [--artifact PATH] [--no-artifact]
session-doctor analyze --all [--project PATH] [--agent AGENT] [--force]
session-doctor summary [--format terminal|json] [--project PATH] [--agent AGENT] [--limit N]
session-doctor trends [--project PATH] [--agent AGENT] [--bucket week|month] [--periods N]
session-doctor projects list [--agent AGENT] [--limit N] [--format terminal|json]
session-doctor report <session-id> [--format terminal|markdown|json] [--limit N] [--show-text]
session-doctor graph <session-id> [--format json]
```

`report` and `graph` are exact-session, on-demand read surfaces. `explain` and
`export` remain design ideas but are not current CLI commands.

The CLI should be local-only by default. It should not call an LLM or external
API on its own unless an explicit future option enables that. LLM capability can
come naturally from agents invoking the CLI and using their own reasoning over
the structured output.

## Initial Scope

The first complete product iteration should support:

- Codex sessions
- Claude Code sessions
- Pi sessions
- single-session diagnosis
- normalized event storage
- DuckDB-backed local analysis
- deterministic features and explainable scoring
- deterministic project-level trend and recurrence views
- privacy-safe reports and a complete conservative graph projection model

The current implementation parses Codex, Pi, and Claude Code root/subagent
transcripts. Claude metadata and explicitly referenced persisted tool results
enrich their related sessions without becoming standalone sessions. OpenCode
and other agents should be considered future adapters. The architecture should
make adding them straightforward, but they are not part of the first
implementation slice.

## Current Repository State

As of the current repository state, Phase 5 deterministic feature hardening,
Phase 6 classification scoring, Phase 7 aggregate summaries, Phase 8
project-level trends, and Phase 9 reports/graph projection are implemented.
The repository has a working local CLI for ingesting and analyzing Codex, Pi,
and Claude Code root/subagent logs. Claude discovery also classifies metadata,
persisted tool results, memory, and auxiliary files. Related sidecars are
correlated deliberately; unrelated categories remain excluded but visible in
discovery and ingest counts. The implemented vertical slice is:

```text
Codex/Pi/Claude root or subagent JSONL source
  -> adapter-specific parser
  -> normalized Pydantic bundle
  -> DuckDB store
  -> session listing
  -> deterministic feature extraction
  -> deterministic classification
  -> persisted analysis rows + optional JSON artifact
  -> aggregate summary queries over ingested/analyzed sessions
  -> aligned project/agent/cohort trend and recurrence views
  -> exact-session terminal/Markdown/JSON reports
  -> exact-session conservative JSON evidence graphs
```

### Implemented Capabilities

The tool can currently:

- inspect local prerequisites and adapter roots with `doctor`
- discover built-in adapter roots for Codex, Claude Code, and Pi
- count candidate source files by adapter/source kind with `adapters list --scan`
- initialize and inspect a local DuckDB database
- ingest Codex JSONL sessions from the default root, a directory, or one file
- ingest Pi JSONL sessions from the default root, a directory, or one file
- ingest Claude Code root/subagent JSONL from the default root, a directory, or
  one file while correlating matched metadata and referenced tool-result
  sidecars
- delete and replace normalized rows for a source when that source is re-ingested
- list ingested sessions with agent, start time, message count, command count,
  warning count, and source path
- load an ingested session bundle back from DuckDB for analysis
- extract deterministic message and session features
- classify sessions with explainable rule-based labels
- persist derived analysis runs, message features, session features, and session
  classifications
- write machine-readable JSON analysis artifacts by default, or print the same
  payload with `analyze --format json`
- restore stale or missing current analysis deliberately with filtered
  `analyze --all`, without writing batch artifacts by default
- summarize all ingested sessions with optional agent/project filters
- print aggregate summaries as terminal tables or JSON, including analysis
  coverage, labels, risky sessions, failed commands, repeated files, and
  deterministic next-step recommendations
- calculate read-only weekly/monthly series anchored to the latest matching
  timed session, with explicit empty periods and analysis compatibility
- keep top-level and sidechain scores, classifications, risk rates, judgments,
  and agent observations separate
- list exact observed project/CWD hints without inferred repository identity
- correlate recurring failed commands, opaque failed-tool fingerprints, and
  problematic files only across distinct valid root-session families

Implemented commands:

```bash
session-doctor --help
session-doctor version
session-doctor doctor [--db PATH]
session-doctor adapters list [--scan]
session-doctor db init [--db PATH]
session-doctor db info [--db PATH]
session-doctor ingest --agent codex [--source PATH] [--db PATH]
session-doctor ingest --agent pi [--source PATH] [--db PATH]
session-doctor ingest --agent claude [--source PATH] [--db PATH]
session-doctor sessions list [--db PATH]
session-doctor analyze <session-id> [--db PATH] [--format terminal|json]
session-doctor analyze <session-id> [--artifact PATH | --no-artifact]
session-doctor analyze --all [--project PATH] [--agent codex|claude|pi]
session-doctor analyze --all [--force] [--write-artifacts] [--format terminal|json]
session-doctor summary [--db PATH] [--format terminal|json]
session-doctor summary [--agent codex|claude|pi] [--project PATH] [--limit N]
session-doctor trends [--db PATH] [--format terminal|json]
session-doctor trends [--project PATH] [--agent codex|claude|pi]
session-doctor trends [--bucket week|month] [--periods 1..120] [--limit N]
session-doctor projects list [--db PATH] [--agent codex|claude|pi] [--limit N]
session-doctor report <session-id> [--db PATH] [--format terminal|markdown|json]
session-doctor report <session-id> [--limit N] [--show-text]
session-doctor graph <session-id> [--db PATH] [--format json]
```

Commands not currently present: `explain`, `export`.

### Current Package Shape

```text
src/session_doctor/
  cli.py, cli_options.py, cli_renderers.py
  ingest_workflow.py, analysis_workflow.py, batch_analysis.py
  diagnostic_models.py, report_models.py
  summary_payload.py, trend_payload.py, report_payload.py, report_renderers.py
  graph_projection.py, graph_payload.py
  normalization.py, privacy.py, ids.py, config.py
  adapters/   discovery plus agent-specific record/entity normalization
  analysis/   deterministic features, scores, evidence, and classifications
  schemas/    strict normalized and derived graph Pydantic entities
  store/      DuckDB persistence/loading plus diagnostic, recurrence, summary,
              trend, project, and pattern readers
```

The map stays at responsibility level deliberately. Individual adapter,
analysis, and store modules are split by concern and may continue to evolve
without making this overview stale after every refactor.

### Adapter Coverage

Codex parsing currently normalizes:

- session metadata and turn context
- raw events for every valid JSONL record
- `response_item` user/assistant/developer messages
- `event_msg` user/agent message fallback rows when no nearby response message
  duplicates them
- function/custom/web-search tool calls and tool results
- command completions from `exec_command_end`
- patch/file activity from `patch_apply_end`
- token/model usage rows
- compaction counts, expected ignored event counts, and parse warnings for errors
  or unsupported shapes

Pi parsing currently normalizes:

- session records, including timestamps, cwd when present, model/provider from
  model-change records, and source-path project hints only in metadata
- raw events for every valid JSONL record
- user, assistant, tool-result, and bash-execution messages
- assistant `toolCall` blocks with stable fallback IDs for idless calls
- structured tool results with hashed output and recursive failure-signal
  detection
- command runs from `exec_command`, `bash` tool results, and separate
  `bashExecution` records while avoiding duplicate command rows
- file activity for read, write, edit, multi-edit, move, delete, and
  `apply_patch`-style operations
- model usage/cost rows from assistant usage payloads
- metadata-only record counts and parse warnings for unsupported roles/types

Claude Code parsing currently normalizes:

- raw events for every valid native record, with malformed rows represented as
  parse warnings
- session IDs, chronological timestamps, the first trustworthy cwd, cwd/version
  drift, git branches, entrypoints, model, and provider metadata
- user, assistant, and system messages while excluding thinking and tool-result
  text from message text
- assistant tool calls and correlated user tool results with hashed arguments
  and output
- Bash command runs and Read/Edit/Write file activity through the shared
  canonical identity contracts
- model usage and deliberate metadata-only/unknown-shape counts and warnings
- root and subagent transcripts as separate sessions, with `is_sidechain`,
  deterministic parent source/session links, native agent IDs, and nesting
  metadata
- top-level subagents linked to the one root in the same native session
  directory only when metadata reports `spawnDepth=0`; nested subagents linked
  through agreeing `parentAgentId` and `toolUseId` evidence
- adjacent subagent metadata through safe agent/task/model/permission fields
  plus hashes and lengths for descriptions
- explicitly referenced persisted tool results through hashes, lengths, and
  truncation facts without raw sidecar content
- missing, ambiguous, mismatched, malformed, and orphan topology as explicit
  warnings rather than guessed links
- parsed-versus-ignored source-kind counts while memory and unrelated auxiliary
  files remain excluded

### Storage And Analysis Coverage

The current internal DuckDB schema marker is `4`. This marker is for local
inspection and rebuild coordination only; throughout 0.x, existing DuckDB files
and JSON artifacts may be regenerated instead of migrated for compatibility.
The current store creates these tables:

```text
schema_migrations
session_sources
sessions
raw_events
messages
tool_calls
tool_results
command_runs
file_activities
model_usage
parse_warnings
analysis_runs
message_features
session_features
session_classifications
```

Ingestion is source-scoped delete-and-replace: re-ingesting one source removes
that source's normalized rows and any derived analysis rows for the affected
session IDs before inserting the newly parsed bundle. Analysis is
session-scoped delete-and-replace: rerunning `analyze` for a session replaces
that session's previous derived analysis rows.

Current deterministic message features include repeated-request similarity and
user text markers for correction, frustration, scope boundaries, ambiguity, and
stop/pause/defer signals. Marker features are deduplicated by marker family per
message while preserving matched strings in evidence. Repeated-request features
include matched prior message IDs, source event IDs, similarity score, and
threshold evidence. Current session features include message counts,
command/tool failure counts and ratios, repeated failure groups with group types
and source event IDs, repeated command failure groups, edited file counts,
same-file repeated edit counts with per-path source event IDs, maximum edits to
one file, conservative unresolved-ending evidence, and reusable Phase 6 risk
scores:

- `friction_score`
- `stuckness_score`
- `prompt_clarity_risk`
- `agent_fit_risk`
- `project_complexity_signal`

Current classification labels are:

- `user_stuck`
- `tooling_blocked`
- `agent_looping`
- `resolved_after_corrections`
- `healthy`
- `agent_misunderstood`
- `prompt_ambiguous`
- `task_too_large`
- `repo_complexity_high`
- `abandoned_or_stopped`

Every classification stores score, confidence, evidence event IDs, a short
evidence summary, and rule metadata with thresholds and contributing features
where applicable.

### Current Limitations

- Claude memory files and unrelated auxiliary files are deliberately excluded;
  orphan metadata/tool-result sidecars are reported rather than guessed into a
  session.
- Reports deliberately omit full transcripts and disclose message text only
  for displayed persisted evidence under `--show-text`.
- Graphs are derived on demand as typed JSON; graphical rendering, graph
  algorithms, persistence, and merged-family graphs remain deferred.
- Observed project paths are hints, not inferred VCS roots or a project
  registry; nested paths remain distinct.
- Directional judgments deliberately remain unavailable without explicit
  project scope or when fixed density, sample, and coverage gates fail.
- Recurring fingerprints are correlational identifiers, not secrets and not
  protection against guessing low-entropy tool output.
- Export commands are not implemented.
- The tool is local-only and deterministic; it does not call LLMs or external
  APIs.
- Privacy hardening is partial: command/file paths and user/assistant message
  text are persisted locally because current deterministic analysis needs them;
  tool outputs, command outputs, patch/content payloads, and structured
  arguments are generally stored as hashes/lengths plus metadata rather than raw
  output text.

The repository also has synthetic tests for CLI behavior, schema validation,
DuckDB initialization and round-tripping, adapter discovery, Codex parsing, Pi
parsing, Claude root parsing, ingest behavior, session listing, feature
extraction, classification, analysis persistence, analysis artifacts, aggregate
summaries, weekly/monthly trends, project discovery, family recurrence, and
privacy helpers, plus typed diagnostic reads, report payload/rendering, graph
projection, and native three-adapter report/graph end-to-end behavior.

The named Pre-Phase-8 work in `docs/pre-phase-8-plan.md` hardened cross-adapter
identities and ingestion failures, added Claude root parsing, and completed
Claude subagents, sidecars, and copied-local validation before project-level
trends. Phase 9 reports and graph projection now reuse the same normalized,
current-analysis, topology, and recurrence contracts rather than inventing a
parallel evidence layer.

## Local Session Inspection Findings

This section captures the initial read-only inspection of real local session
stores. Keep it as implementation evidence so adapter work does not need to
rediscover the same format details.

### Codex

Location:

```text
~/.codex/sessions
```

Layout:

```text
~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl
```

Observed corpus:

- 148 `.jsonl` files
- all inspected lines parsed as JSON
- no other extensions observed in the session store

Representative paths:

```text
~/.codex/sessions/2026/05/03/rollout-<timestamp>-<uuid>.jsonl
~/.codex/sessions/2026/05/04/rollout-<timestamp>-<uuid>.jsonl
~/.codex/sessions/2026/04/30/rollout-<timestamp>-<uuid>.jsonl
```

Every inspected record had this top-level envelope:

```text
timestamp: ISO-8601 Z string
type: string
payload: object
```

Top-level `type` values observed:

```text
session_meta
turn_context
response_item
event_msg
compacted
```

`session_meta` appears once per file. `payload.id` matches the UUID suffix in
the filename in inspected examples. Useful keys:

```text
id
timestamp
cwd
originator
cli_version
source
model_provider
base_instructions
git
agent_nickname
agent_role
```

`turn_context` appears per turn and carries runtime context:

```text
turn_id
cwd
model
current_date
timezone
approval_policy
sandbox_policy
permission_profile
effort
user_instructions
developer_instructions
summary
```

Messages appear in two overlapping representations:

```text
response_item payload.type=message
event_msg payload.type=user_message or agent_message
```

`response_item` message fields:

```text
type
role
content
phase
```

Observed roles:

```text
user
assistant
developer
```

Content is an array of blocks with keys like `type` and `text`. Observed content
types:

```text
input_text
output_text
```

Assistant phases include:

```text
commentary
final_answer
```

Tool calls are mostly `response_item` records:

```text
function_call
function_call_output
custom_tool_call
custom_tool_call_output
```

Useful tool-call fields:

```text
name
arguments
input
call_id
status
output
```

Observed function names include:

```text
exec_command
write_stdin
update_plan
spawn_agent
wait_agent
close_agent
```

Command completion has a richer `event_msg` form:

```text
payload.type=exec_command_end
call_id
turn_id
command
cwd
parsed_cmd
stdout
stderr
aggregated_output
exit_code
duration
formatted_output
status
process_id
```

File edits are represented as:

```text
payload.type=patch_apply_end
call_id
turn_id
stdout
stderr
success
status
changes
```

`changes` is keyed by absolute file path. Values can include:

```text
type
unified_diff
move_path
content
```

Errors appear as:

```text
payload.type=error
message
codex_error_info
```

or as failed command events with nonzero `exit_code` or failed `status`.

Codex parser risks:

- The store is live and can change while being scanned.
- Do not assume `payload.type` exists on every record.
- There are overlapping message formats; prefer `response_item` for normalized
  message parsing and use `event_msg` for richer command/edit events.
- Command output, function arguments, tool output, message text, and patch diffs
  may contain sensitive content.
- `cwd` appears at session, turn, and command levels and can differ.
- `model` is most reliable in `turn_context`; `session_meta` reliably provides
  model provider.
- Absolute paths in patch data should be normalized/redacted for reporting.
- Small or aborted sessions may not have final answers.
- `compacted` records indicate truncated or summarized prior context.

### Claude Code

Location:

```text
~/.claude/projects
```

Layout:

```text
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-*.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-*.meta.json
~/.claude/projects/<encoded-cwd>/<session-id>/tool-results/*
```

The encoded project directory is a path-like value with `/` replaced by `-`.
Use the event-level `cwd` as source of truth instead of trying to decode the
directory name.

Observed corpus:

- 307 `.jsonl` transcript files
- 72 root session JSONL files
- 235 subagent JSONL files
- 138 `.json` metadata files, mostly subagent metadata
- 37 persisted tool result files under `tool-results/`
- memory files also live under this tree and should not be treated as session
  transcripts

Representative paths:

```text
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/tool-results/<result-id>.txt
```

Each JSONL line is one event object. Main top-level event types observed:

```text
assistant
user
progress
system
file-history-snapshot
attachment
queue-operation
last-prompt
permission-mode
pr-link
custom-title
agent-name
```

For `assistant`, `user`, `progress`, and `system`, these fields were
consistently present:

```text
type
sessionId
uuid
parentUuid
timestamp
cwd
gitBranch
isSidechain
userType
version
```

Common but not universal fields:

```text
entrypoint
slug
promptId
agentId
sourceToolAssistantUUID
```

Assistant messages are nested under `message`:

```text
message.id
message.type
message.role
message.model
message.content
message.stop_reason
message.stop_sequence
message.usage
```

Assistant content is a list. Observed assistant content block types:

```text
text
thinking
tool_use
```

User messages are also nested under `message`. User `message.content` can be a
string or a list. Observed user list block types:

```text
tool_result
text
image
document
```

Tool calls are assistant content blocks:

```text
type=tool_use
id
name
input
```

Common tool names:

```text
Bash
Read
Edit
Grep
Glob
TaskUpdate
Write
Agent
TaskCreate
MCP tools
```

Tool input fields by tool:

```text
Bash: command, description, timeout, run_in_background
Read: file_path, limit, offset
Edit: file_path, old_string, new_string, replace_all
Write: file_path, content
Grep: pattern, path, glob, output_mode, head_limit
```

Tool results are user content blocks:

```text
type=tool_result
tool_use_id
content
is_error
```

`is_error` is not always present. Absence should be treated as unknown, not
false.

Claude Code also adds a top-level `toolUseResult` on many user events. Useful
keys for Bash:

```text
stdout
stderr
interrupted
isImage
noOutputExpected
```

Useful keys for edits:

```text
filePath
oldString
newString
originalFile
structuredPatch
replaceAll
userModified
```

Command output can appear in several places:

```text
message.content[].tool_result.content
toolUseResult.stdout
toolUseResult.stderr
progress.data.output
progress.data.fullOutput
tool-results/* persisted files
```

Error signals:

```text
tool_result.is_error=true
toolUseResult.stderr
toolUseResult.interrupted
system subtype=api_error
assistant top-level error
assistant isApiErrorMessage
assistant stop_reason=max_tokens or stop_sequence
```

Claude parser risks:

- Discovery must classify root sessions, subagent sessions, subagent metadata,
  persisted tool result files, memory files, and ignored auxiliary files.
- Do not assume every line has `sessionId`.
- Do not assume `message.content` is always a string.
- Do not rely on encoded project directory names for `cwd`.
- `entrypoint`, `slug`, `promptId`, `agentId`, and
  `sourceToolAssistantUUID` are not universal.
- Tool output and file patches can be large and sensitive.
- Version drift is visible in the same tree; optional keys vary across Claude
  Code versions.

### Pi

Location:

```text
~/.pi/agent/sessions
```

Layout:

```text
~/.pi/agent/sessions/<cwd-derived-folder>/<timestamp>_<uuid>.jsonl
```

Observed corpus:

- 147 `.jsonl` files as of the Phase 4 planning check
- 15 project/cwd folders
- all inspected lines parsed as JSON
- filenames match `YYYY-MM-DDTHH-MM-SS-mmmZ_<uuid>.jsonl`

Representative paths:

```text
~/.pi/agent/sessions/<cwd-derived-folder>/<timestamp>_<uuid>.jsonl
```

Every line is one top-level object with stable common fields:

```text
type
id
timestamp
parentId
```

Top-level `type` values observed:

```text
message
thinking_level_change
model_change
session
compaction
custom
branch_summary
session_info
custom_message
label
```

`session` row fields:

```text
type
id
timestamp
cwd
version
```

`version` was observed as `3`. `cwd` is reliable. The containing folder is
lossy and should not be used as source of truth.

Timestamp and ID notes:

- top-level `timestamp` is an ISO UTC-like string
- nested `message.timestamp` is epoch milliseconds
- session event `id` matched filename UUID in 142 of 143 inspected files
- keep both filename-derived and native session IDs
- event IDs are unique within a file but not globally unique

Message rows have `type=message` and a nested `message` object. Observed roles:

```text
user
assistant
toolResult
bashExecution
```

User messages:

```text
message.role=user
message.content=[{type, text}]
```

Assistant messages:

```text
message.role=assistant
message.api
message.provider
message.model
message.usage
message.stopReason
message.responseId
message.content
message.timestamp
```

Assistant content can include:

```text
thinking
text
toolCall
```

Tool-call content blocks:

```text
type=toolCall
id
name
arguments
partialJson
```

Observed tool names:

```text
bash
read
edit
write
webfetch
websearch
todo
subagent
deep_research
deep_research_lite
```

Tool results:

```text
message.role=toolResult
message.toolCallId
message.toolName
message.isError
message.content
message.details
message.timestamp
```

`bashExecution` is a separate representation and should be handled explicitly:

```text
command
output
exitCode
cancelled
truncated
excludeFromContext
```

Model metadata:

```text
model_change.provider
model_change.modelId
message.provider
message.model
message.api
message.usage
```

Usage fields:

```text
input
output
cacheRead
cacheWrite
totalTokens
cost
```

Error signals:

```text
toolResult.message.isError=true
assistant message.errorMessage
assistant message.stopReason=error or aborted
bashExecution.exitCode != 0
bashExecution.cancelled
bashExecution.truncated
tool-specific details.error
```

Pi parser risks:

- Event IDs are not globally unique.
- Filename UUID does not always match native session ID.
- Do not derive `cwd` from the containing folder.
- `parentId` can be null for bootstrap events.
- Assistant content is mixed and may contain thinking, text, and tool calls in
  one message.
- `partialJson` exists; prefer parsed `arguments` when present.
- Tool result text can contain command output or file contents.
- `edit` and `write` arguments and details can contain full source content or
  patches.
- Some tool results include images.
- Compaction and branch summary events contain useful file metadata but are
  summaries, not direct turns.

## Minimal Common Schema v0

Codex, Claude Code, and Pi all have enough structure to normalize into one
timeline model plus derived tool/file/usage tables.

The first common schema should include:

```text
SessionSource
RawEvent
Session
Message
ToolCall
ToolResult
CommandRun
FileActivity
ModelUsage
ParseWarning
```

Lowest common reliable fields:

```text
agent
source_path
record_index
native_event_type
native_event_id
native_parent_event_id
session_id
timestamp
cwd
project_path
agent_version
model
model_provider
role
content_block_types
tool_call_id
tool_name
tool_result_is_error
command_present
command_exit_code
file_paths_touched
is_compaction_or_summary
```

### SessionSource

```text
source_id
agent
source_path
source_kind
discovered_at
modified_at
size_bytes
content_hash
adapter_version
```

`source_kind` examples:

```text
root_session_jsonl
subagent_session_jsonl
subagent_meta_json
persisted_tool_result
memory_file
ignored_auxiliary
```

### Session

```text
session_id
native_session_id
filename_session_id
agent
source_path
started_at
ended_at
cwd
project_path
git_branch
agent_version
parser_version
is_subsession
parent_session_id
has_compaction
```

Keep both native and filename-derived session IDs where available. The stable
internal `session_id` can be derived from:

```text
agent + source_path + native_session_id_or_filename_id
```

### RawEvent

```text
event_id
session_id
agent
source_path
record_index
timestamp
native_type
native_subtype
native_event_id
native_parent_event_id
payload_shape
```

`event_id` should be a synthetic stable key. Native event IDs are not globally
unique across all agents and, in Pi, are only unique within a file.

Recommended synthetic key:

```text
agent + session_id + source_path + record_index
```

Also preserve native IDs separately for graph edges and traceability.

### Message

```text
message_id
session_id
event_id
parent_event_id
timestamp
role
normalized_role
phase
model
provider
stop_reason
text
text_length
text_hash
content_block_types
is_sidechain
```

`text` should be stored locally for user and assistant messages because
classification requires it. Reports should redact or summarize text by default
unless the user explicitly asks to show it.

### ToolCall

```text
tool_call_id
session_id
event_id
timestamp
tool_name
normalized_tool_name
argument_keys
safe_path
safe_url
safe_query
command_text
has_large_or_sensitive_args
```

Do not store raw full arguments by default when they may contain file content,
patches, secrets, or large command payloads. Preserve selected safe fields and
argument key summaries.

### ToolResult

```text
tool_result_id
session_id
event_id
timestamp
tool_call_id
tool_name
is_error
output_length
output_hash
content_block_types
persisted_output_path
truncation_status
```

Store structural and error information by default, not raw output. Raw output
can contain file contents, SQL, secrets, diffs, or long command logs.

### CommandRun

```text
command_run_id
session_id
event_id
tool_call_id
command_text
cwd
exit_code
status
duration_ms
stdout_length
stderr_length
interrupted
cancelled
truncated
```

Command text is useful for diagnosis and should be stored locally, but reports
should redact obvious secrets.

### FileActivity

```text
file_activity_id
session_id
event_id
tool_call_id
action
path
first_changed_line
has_diff
diff_length
success
```

Actions:

```text
read
write
edit
patch
delete
move
unknown
```

Store paths and structural patch metadata by default. Do not store raw diffs,
raw old/new strings, or write content by default.

### ModelUsage

```text
usage_id
session_id
event_id
model
provider
input_tokens
output_tokens
cache_read_tokens
cache_write_tokens
total_tokens
cost
```

Each adapter should map native usage keys into this common table and preserve
unmapped keys only as optional metadata if needed later.

### ParseWarning

```text
warning_id
session_id
event_id
source_path
record_index
severity
code
message
native_type
```

Use parse warnings for unsupported event types, malformed records, missing
expected IDs, inconsistent filename/native session IDs, and redaction decisions
that drop raw content.

## Normalization Rules v0

Normalize roles to:

```text
user
assistant
tool
system
developer
progress
unknown
```

Known mappings:

```text
Codex response_item.message.role=user       -> user
Codex response_item.message.role=assistant  -> assistant
Codex response_item.message.role=developer  -> developer
Codex function_call_output                  -> tool
Codex custom_tool_call_output               -> tool
Codex event_msg exec_command_end            -> tool / command
Codex event_msg patch_apply_end             -> tool / file activity

Claude top-level assistant                  -> assistant
Claude top-level user with text content      -> user
Claude top-level user with tool_result       -> tool
Claude top-level system                     -> system
Claude top-level progress                   -> progress

Pi message.role=user                        -> user
Pi message.role=assistant                   -> assistant
Pi message.role=toolResult                  -> tool
Pi message.role=bashExecution               -> tool / command
```

Normalize tool names conservatively:

```text
Bash, bash, exec_command -> shell
Read, read               -> read
Edit, edit               -> edit
Write, write             -> write
Grep, grep               -> search
Glob, glob               -> glob
Agent, subagent          -> subagent
webfetch                 -> web_fetch
websearch                -> web_search
```

Keep the native tool name in addition to the normalized tool name.

## Privacy And Storage Defaults

The CLI is local-only by default, but local session logs still contain sensitive
data. The parser and reports should be conservative.

Default storage behavior:

- store user and assistant message text locally because classification depends
  on it
- do not show message text in reports unless `--show-text` is explicitly passed
- do not store raw tool output by default
- do not store raw command stdout/stderr by default
- do not store raw diffs by default
- do not store raw `write.content`, `old_string`, `new_string`, or full edit
  bodies by default
- store lengths, hashes, content types, paths, error flags, truncation flags,
  and selected safe metadata
- store command text locally, but redact obvious secrets in reports
- store full paths locally, but display home-relative or redacted paths in
  reports

Raw-content support can be added later behind explicit flags, for example:

```bash
session-doctor ingest --store-tool-output
session-doctor report <session-id> --show-text
session-doctor report <session-id> --show-tool-output
```

The default should optimize for safe local analytics and classification, not
verbatim replay.

## Adapter Implementation Order

Recommended first implementation order:

1. Codex
2. Pi
3. Claude Code

Reasoning:

- Codex has a clean envelope, strong `turn_context`, and rich command/edit
  events.
- Pi has regular event IDs, parent IDs, explicit session rows, and clean
  tool-call/result linkage, making it a good validation source for graph
  projection.
- Claude Code is very important but has the most discovery complexity because
  root sessions, subagent sessions, subagent metadata, persisted tool results,
  memory files, and auxiliary files share the same tree.

## Graphify Inspiration

Graphify is useful inspiration for product and architecture shape, but it should
not be treated as a session-log parser.

Useful ideas to borrow:

- staged pipeline
- CLI-first package with optional agent integration
- platform-specific install surfaces
- stable IDs
- provenance on extracted artifacts
- confidence labels
- local reports
- query/MCP surface later
- incremental cache/manifest design

Important distinction:

Graphify supports many agents by installing skills, hooks, and rules into those
agents. It does not appear to parse historical Codex, Claude, OpenCode, or Pi
session logs. `session-doctor` needs deterministic per-agent session adapters as
its foundation.

## Architecture

The core pipeline should be:

```text
discover -> parse -> normalize -> persist -> feature -> classify -> report
                                                    |
                                                    v
                                               graph projection
```

Each stage should have a narrow responsibility and should be testable without an
LLM.

## Layer 1: Discovery

Discovery finds candidate session files for supported agents.

Initial discovery targets:

```text
Codex       ~/.codex/sessions
Claude Code ~/.claude/projects
Pi          ~/.pi/agent/sessions
```

Discovery currently produces candidate records, not parsed sessions:

```text
SessionSource
  source_id
  agent_name
  source_path
  source_kind
  discovered_at
  native_session_id
  parent_source_id
  metadata
```

Pi's current session location was re-verified during Phase 4 planning. The
adapter should still use copied local session files for smoke tests rather than
reading directly from the live store in automated tests.

Detailed Pi parsing plan: `docs/phase-4-plan.md`.

## Layer 2: Agent Adapters

Each supported agent gets its own adapter module.

```text
session_doctor/adapters/codex.py
session_doctor/adapters/claude.py
session_doctor/adapters/pi.py
session_doctor/adapters/common.py
session_doctor/adapters/patches.py
session_doctor/adapters/pi_tools.py
```

Adapters should parse native files into the normalized event model. They should
not compute product-level classifications.

Adapter responsibilities:

- read native session files
- preserve native event IDs and event types where available
- normalize role/message/tool-call structure
- attach source provenance
- emit parse warnings for ambiguous or unsupported events
- remain deterministic and unit-testable

The adapter registry should make future support for OpenCode and other agents
explicit:

```text
agent name -> discovery defaults -> parser -> adapter version
```

Adapter version should be part of the cache key so old parsed results can be
invalidated safely when parsing logic changes.

## Layer 3: Normalized Event Model

The normalized model is the source of truth. Graphs, reports, and metrics should
be derived from it.

Use Pydantic for schemas and validation.

Core entities:

```text
Session
SessionSource
RawEvent
Message
ToolCall
ToolResult
CommandRun
FileActivity
ModelUsage
ParseWarning
AnalysisRun
MessageFeature
SessionFeature
SessionClassification
GraphNode
GraphEdge
```

Every normalized record should include provenance:

```text
agent
session_id
source_path
source_line_or_index
native_event_type
native_event_id
timestamp
project_path
parser_version
confidence
```

The model should allow incomplete timestamps or missing fields, because agent
logs will not all expose the same metadata.

### Canonical Aggregate Identities

`CommandRun.command` preserves the native command. A separate identity is
derived from trimmed text and unwraps only a bare `sh`, `bash`, or `zsh` name,
or its explicitly recognized `/bin` or `/usr/bin` path, with one payload passed
through `-c` or `-lc`. The unredacted canonical text is hashed for grouping,
while only a redacted canonical example is stored for display. Other paths,
near-miss wrappers, and commands that require shell interpretation remain
distinct.

`FileActivity.path` preserves the native path. Normalization removes `.` and
`..` lexically, anchors a relative path to a trustworthy event/session cwd or
project path, and records canonical absolute and project-relative forms when
available. It never requires the file to exist or resolves symlinks. Relative
paths without a trustworthy base remain explicitly unresolved and do not group
across sessions.

These fields are part of the normalized schema and DuckDB tables so analysis,
summaries, and future trends share one identity contract.

Confidence labels should be used consistently:

```text
EXTRACTED   directly present in the source log
INFERRED    derived from surrounding events or text
AMBIGUOUS   plausible but uncertain; should be surfaced for review
```

## Layer 4: Persistence

DuckDB should be included early because it is a key part of the tool.

DuckDB is not the classifier. It is the local analytical store for normalized
events, features, classifications, and future project-level trends.

Current storage includes:

```text
schema_migrations
session_sources
sessions
raw_events
messages
tool_calls
tool_results
command_runs
file_activities
model_usage
parse_warnings
analysis_runs
message_features
session_features
session_classifications
```

DuckDB is the primary local query/report substrate. JSONL/Parquet export remains
future work.

Design requirements:

- local-only by default
- no background service required
- deterministic versioned rebuilds throughout 0.x; migration compatibility
  begins only with an explicitly stable contract, no earlier than `v1.0.0`
- easy deletion/rebuild of derived tables
- future export to JSONL and Parquet

Ingestion continues a multi-source directory scan only for explicit recoverable
source read/format errors and returns a failing exit status if anything was
skipped. Explicit single-file failures stop immediately. Persistence errors,
invariant failures, and unexpected exceptions abort and are never relabeled as
parser skips.

## Layer 5: Feature Extraction

Feature extraction should start deterministic and explainable.

Message-level features:

```text
frustration_marker_count
correction_marker_count
scope_boundary_marker_count
repeat_request_similarity
mentions_prior_attempt
asks_to_stop_or_pause
ambiguity_signal
urgency_signal
negative_sentiment_hint
```

Session-level features:

```text
turn_count
user_message_count
assistant_message_count
tool_call_count
failed_tool_call_count
failed_tool_ratio
same_error_repeated_count
same_file_edited_repeatedly_count
correction_count
repeat_request_count
scope_boundary_count
unresolved_ending_signal
```

Sentiment analysis should be treated as one feature, not the primary classifier.
Generic sentiment can miss domain-specific frustration such as:

```text
"we already tried that"
"this is still broken"
"why are you changing unrelated files"
"stop"
"no, that is not what I asked"
```

Domain-specific markers and repetition signals are more important than generic
positive/negative sentiment.

## Layer 6: Classification

Classification should be multi-label.

Message-level labels:

```text
neutral
clarification
correction
scope_boundary
frustration
repeat_request
blocked
stop_or_pause
positive_resolution
```

Session-level labels:

```text
healthy
agent_misunderstood
agent_looping
task_too_large
prompt_ambiguous
repo_complexity_high
tooling_blocked
user_stuck
resolved_after_corrections
abandoned_or_stopped
```

Scores should be explainable:

```text
friction_score
stuckness_score
prompt_clarity_risk
agent_fit_risk
project_complexity_signal
```

Every classification should include evidence:

```text
label
score
confidence
evidence_event_ids
evidence_summary
```

Initial scoring can be weighted deterministic rules. Later, once enough real
sessions are labeled, the tool can add local ML or agent-assisted classification
as an optional layer.

## Layer 7: Graph View

Graph view should be planned from the beginning, but it should be implemented
after aggregate summary/project trend views. The aggregate views will clarify
which cross-session entities and evidence anchors are most important before the
tool commits to graph semantics.

The graph is a derived projection over the normalized timeline. It is not the
source of truth.

Primary source of truth:

```text
SessionSource -> Session -> RawEvent -> Message / ToolCall / ToolResult / CommandRun / FileActivity
```

Implemented conservative derived graph view:

```text
nodes = session, topology-only session references, raw events, messages,
        tool calls/results, command runs, file activities/files, failure groups,
        message/session features, classifications, parse warnings
edges = contains, parent_message, derived_from, has_tool_result, runs_command,
        targets_file, member_of_failure_group, repeats_request_of, detected_in,
        contributes_to_score, supports_classification, has_warning,
        parent_session_reference, child_session_reference
```

Example:

```text
message_feature_17 --repeats_request_of--> message_3
tool_result_8 --member_of_failure_group--> failure_group_2
command_run_12 --targets_file--> file:src/foo.py
classification_4 --derived_from--> raw_event_13
raw_event_13 --supports_classification--> classification_4
```

Graph view exposes persisted structure and deterministic findings that can help
a reviewer inspect:

- exact-session normalized timeline and raw-event provenance
- persisted repeated-request relations
- repeated command/tool failure-group membership
- file targets and repeated file-activity evidence
- feature, score, and classification evidence relations
- parse warnings and explicitly unresolved references
- parent/child session topology as lightweight references only

The command should exist in the public plan:

```bash
session-doctor graph <session-id>
```

Output is typed deterministic JSON nodes/edges. Causal edges, inferred goals,
agent blame, and invented error entities are deliberately excluded. Graphviz,
HTML, algorithms, persistence, merged-family graphs, and MCP/query access remain
deferred until dogfooding establishes a concrete need.

## Layer 8: Reporting

Reporting should reuse the aggregate summary and project trend query layer. The
first report surface should therefore come after there is already a useful way
to summarize all ingested sessions and sessions under a specific project/folder.

The first report should focus on single-session diagnosis.

Report sections:

```text
summary
session metadata
classification labels
friction/stuckness scores
key evidence
repeated requests
corrections and scope boundaries
tool failure loops
same-error repeats
same-file edit loops
ending state
recommended interpretation
```

The report should be useful to a person reviewing the session and to an agent
trying to understand why the session went poorly.

Example interpretation:

```text
This session likely became stuck because the user repeated the same goal three
times, the same test failure appeared twice, and the final user message corrected
the assistant's previous approach.
```

Reports should be available as terminal output and Markdown. Machine-readable
JSON should be available for agents and tests.

## Phase Plan

### Phase 0: Design Document

Create and review this design document before implementation.

Status: complete.

### Phase 1: Project Skeleton

Set up:

- Python package
- uv project metadata
- Typer CLI
- test structure
- DuckDB dependency
- initial schema modules

No real classification yet.

Status: complete. The implemented Phase 1 also includes adapter discovery,
DuckDB migration scaffolding, graph placeholder schemas/tables, privacy helpers,
and reserved CLI commands.

### Phase 2: Codex Parse And Ingest MVP

Implemented the first real vertical slice using Codex only:

- parse Codex JSONL session files into the existing normalized models
- persist parsed bundles into DuckDB
- implement `session-doctor ingest` for Codex sources
- add `session-doctor sessions list` so ingestion can be inspected
- keep parsing deterministic, local-only, and covered by synthetic fixtures

This phase should tighten schemas and storage only where the Codex vertical
slice proves gaps. It should not add feature extraction, classification,
reports, graph projection, ML dependencies, or privacy/redaction hardening.

Status: complete.

Detailed plan: `docs/phase-2-plan.md`.

### Phase 3: Codex Analysis MVP

Implemented deterministic analysis over the Phase 2 Codex data:

- add derived feature and classification schemas
- add DuckDB tables and write APIs for analysis rows
- implement deterministic repeated-request similarity
- implement correction, frustration, scope-boundary, failed-command,
  repeated-failure, same-file-edit, and unresolved-ending signals
- add a small deterministic classification layer
- implement `session-doctor analyze <session-id>`
- write a machine-readable JSON artifact by default
- keep analysis local-only, deterministic, and covered by fixtures

This phase should persist derived rows by default and rebuild them for the
target session whenever analysis runs. It should not add a second adapter,
Markdown reports, graph projection, ML dependencies, LLM calls, or
privacy/redaction hardening.

Status: complete.

Detailed plan: `docs/phase-3-plan.md`.

### Phase 4: Pi Adapter

Implemented Pi as the second native adapter after Codex:

- parse Pi JSONL session files
- normalize Pi session, raw event, message, tool call, tool result, command
  run, file activity, model usage, and parse warning records
- support `session-doctor ingest --agent pi`
- support Pi session listing through `session-doctor sessions list`
- support deterministic analysis over ingested Pi sessions through the existing
  `session-doctor analyze <session-id>` command
- keep Claude Code parsing, graph projection, and report generation out of
  scope

Status: complete. A follow-up refactor split shared adapter utilities, Pi tool
parsing, and patch parsing into focused modules while keeping behavior stable.

Detailed plan: `docs/phase-4-plan.md`.

### Phase 5: Deterministic Feature Hardening

Hardened the existing deterministic Codex and Pi analysis before adding
aggregate summaries, project trends, reports, or graph views:

- preserve raw-event ordering when loading analysis-relevant records from
  DuckDB
- normalize offset-aware timestamps before DuckDB writes
- make unresolved-ending evidence conservative around later final answers and
  short sessions with no final answer
- preserve marker match evidence while avoiding duplicate marker-family feature
  rows
- add repeated-failure group types and source event IDs
- let `agent_looping` use repeated failing command text or repeated command
  stdout/stderr hashes while ignoring repeated non-command tool-output failures
  by themselves
- enrich same-file edit-loop and repeated-request evidence
- keep existing `analyze` output shape while exposing richer JSON evidence

Status: complete.

Detailed plan: `docs/phase-5-plan.md`.

### Phase 6: Classification Scoring

Expand features into richer labels and scores over the already-ingested Codex
and Pi normalized records. This phase should not add Claude Code parsing,
aggregate summaries, reports, graph projection, exports, LLM calls, embeddings,
or new ML dependencies.

Expected additions:

- reusable session score features: `friction_score`, `stuckness_score`,
  `prompt_clarity_risk`, `agent_fit_risk`, and `project_complexity_signal`
- threshold metadata and formula versions on score and label rows
- clearer evidence summaries for existing labels
- conservative new labels only where the current deterministic evidence supports
  them
- compact terminal output and backward-compatible JSON artifacts that expose the
  new score features

Keep the scoring simple, deterministic, and explainable at first. Every
classification should include evidence event IDs and an evidence summary.

Status: complete. The existing `analyze` command now emits Phase 6 score
features as session features, surfaces them in terminal and JSON output, and
stores metadata-rich deterministic labels without adding new command surfaces or
schema tables.

Detailed plan: `docs/phase-6-plan.md`.

### Phase 7: Aggregate Summary MVP

Implemented the first aggregate view over the local DuckDB store before
investing in graph or polished report surfaces.

The MVP should answer:

- how many sessions have been ingested in total?
- how many sessions exist for a given project/folder?
- which agents produced those sessions?
- how many sessions have analysis rows?
- what classifications are most common?
- which recent sessions look most stuck, blocked, or looping?
- which commands fail most often?
- which files are repeatedly edited in problematic sessions?
- where should a user or agent look next?

Command shape:

```bash
session-doctor summary [--db PATH]
session-doctor summary [--db PATH] --format json
session-doctor summary [--db PATH] --project /path/to/project
session-doctor summary [--db PATH] --agent pi
session-doctor summary [--db PATH] --limit 10
```

Status: complete. The command queries existing normalized and analysis tables,
requires an existing database, supports terminal and JSON output, filters by
stored `project_path`/`cwd` and agent, redacts displayed commands and home paths,
and avoids graph projection or polished Markdown reports.

Detailed plan: `docs/phase-7-plan.md`.

### Phase 8: Project-Level Trends

Implemented a read-only trend query layer over normalized sessions and latest
persisted analysis, plus an explicit batch-analysis recovery command:

- aligned weekly/monthly project scopes
- current-analyzer coverage, explicit stale analysis, and a filtered
  `analyze --all` recovery path
- separate top-level and sidechain cohorts
- project-scoped guarded outcome directions and neutral signal directions
- cohort-specific non-causal agent observations
- recurring failed commands, failed tool-result fingerprints, and problematic
  files across distinct top-level session families
- exact observed project-path discovery without guessed repository identity

This phase should provide the first useful view over all sessions and over a
specific folder/project. It should still stay deterministic and local-only.

Command shape:

```bash
session-doctor trends
session-doctor trends --project /path/to/project
session-doctor projects list
```

Status: complete. Trends and project discovery remain local-only and read-only;
batch analysis is a separate explicit mutation. Fixture and copied-local
validation cover Codex, Claude Code, and Pi. Sparse copied-local history returned
honest `insufficient_data` rather than weakening fixed gates.

Detailed plan: `docs/phase-8-plan.md`.
Validation: `docs/phase-8-validation.md`.

### Phase 9: Reports And Graph Projection

Build polished report and graph surfaces after summary/trend queries exist.

Single-session reports should provide terminal and Markdown views that include
evidence references back to normalized event IDs. Reports should reuse summary
and trend metrics where useful, for example by showing whether the current
session resembles repeated project-level failures.

Graph projection should implement the derived graph model and
`session-doctor graph <session-id>`.

Start with JSON output:

```json
{
  "nodes": [],
  "edges": []
}
```

Later output formats can be added after the graph semantics stabilize.

Status: complete. Phase 9 keeps both commands read-only,
generates exact-session reports and graphs on demand, exposes stale/missing
analysis honestly, limits message disclosure to explicit evidence-only
`--show-text`, and uses conservative provenance rather than causal graph edges.

Detailed plan: `docs/phase-9-plan.md`.
Validation: `docs/phase-9-validation.md`.

### Phase 10: Optional Agent Integration And v0.1.0 Dogfood Release

Finish the first roadmap with optional integration and a lightweight dogfood
release:

- one portable Agent Skills-standard wrapper for Codex, Claude Code, and Pi
- a read-only CLI command that locates the bundled wrapper for manual install
- guarded full-CLI orchestration without direct SQL or transcript access
- explicit confirmation before writes and evidence-message disclosure
- release licensing, package/version checks, dogfood guidance, and local
  three-harness validation
- an annotated `v0.1.0` source tag after explicit approval

MCP/query access, CI, PyPI publication, and a GitHub Release remain deferred
until dogfooding establishes their contracts.

Status: planned; grilling approved.

Detailed plan: `docs/phase-10-plan.md`.

## Non-Goals For First Iteration

- Cloud service
- web app
- required LLM calls
- full OpenCode support
- training a supervised ML model
- perfect sentiment analysis
- cloud-hosted/project-wide dashboards
- graphical UI for graph visualization

## Resolved Implementation Questions

Claude role/tool metadata and source-kind boundaries are implemented and
validated across root transcripts, nested subagents, metadata sidecars, and
explicitly referenced persisted tool results. Ambiguous or orphaned relations
remain warnings rather than guessed links.
