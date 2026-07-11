---
name: session-doctor
description: Inspect and diagnose local Codex, Claude Code, and Pi sessions with the session-doctor CLI. Use for setup checks, adapter discovery, ingestion, deterministic analysis, aggregate summaries, project trends, exact-session reports, or evidence graphs.
license: MIT
compatibility: Requires session-doctor CLI version 0.1.0.
metadata:
  session-doctor-version: "0.1.0"
---

# Session Doctor

Use the public `session-doctor` CLI as the only interface to session data. The
CLI owns parsing, persistence, analysis, privacy, and evidence semantics. This
skill orchestrates commands and provides guarded interpretation; it does not
reimplement business logic.

## Hard Boundaries

- Never read native session transcripts, sidecars, prompts, or agent session
  directories directly.
- Never open or query the DuckDB database directly, including read-only SQL.
- Never recreate parser, score, classification, recurrence, report, or graph
  logic in the agent context.
- Never infer a repository root or project identity. Use only exact hints shown
  by `projects list` or ask the user.
- Never merge top-level and sidechain sessions.
- Never turn correlation into causality, assign blame, infer intent, or invent
  unsupported statistical conclusions.
- Never install, update, remove, or overwrite agent skills automatically.
- Never call unavailable `explain`, `export`, MCP, transcript-replay, or graph
  visualization surfaces. State that they are not implemented.

## Establish The Contract

Before a workflow:

1. Run `session-doctor version`.
2. Require exactly version `0.1.0`, matching this skill's metadata. If it differs,
   stop, report both versions, and ask the user to locate the matching skill with
   that CLI's `session-doctor integrations path` command.
3. Run `session-doctor doctor` when setup, database, or adapter availability is
   uncertain.
4. Use `session-doctor COMMAND --help` or
   `session-doctor GROUP COMMAND --help` whenever option details matter. Do not
   rely on remembered flags.
5. Keep a user-provided `--db` path consistent through the workflow. If no path
   was provided, say that the CLI default will be used before any write.

## Classify Commands Before Running Them

Inspection/read commands:

```text
session-doctor version
session-doctor doctor
session-doctor adapters list [--scan]
session-doctor db info
session-doctor sessions list [--agent AGENT]
session-doctor summary
session-doctor trends
session-doctor projects list
session-doctor report
session-doctor graph
session-doctor integrations path
session-doctor --show-completion
```

Commands that write database rows, artifacts, or shell configuration:

```text
session-doctor db init
session-doctor ingest
session-doctor analyze
session-doctor --install-completion
```

Do not run a write command until the confirmation protocol below is complete.
A request to diagnose, inspect, fix, or review is not write authorization.

## Write Confirmation Protocol

Before each write, show the exact proposed public command and state:

- the database path, including when the CLI default will be used
- the agent, source, project, or session scope
- for `db init`, that a local schema will be created
- for `ingest`, that re-ingestion can replace normalized rows and derived
  analysis for affected sessions
- for `analyze`, that derived rows for selected sessions will be replaced
- whether an analysis artifact will be written
- for `--install-completion`, that shell configuration will be changed

Ask for explicit confirmation for that command and scope. Do not reuse consent
for another source, session, database, or write.

For wrapper-driven single-session analysis, prefer `--no-artifact` unless the
user requests an artifact. Batch `analyze --all` defaults to no artifacts; do
not add `--write-artifacts` without a separate request.

Never delete or rebuild a database implicitly. If the CLI reports an
incompatible schema, explain that 0.x databases may require rebuilding and ask
how the user wants to proceed. Do not remove the file yourself as an automatic
recovery step.

## Message-Text Confirmation

Default reports and every graph are message-text-free. Before every use of
`report --show-text`:

1. Explain that displayed persisted evidence-message text will be revealed to
   the active agent context.
2. Name the selected session and database scope.
3. Obtain a separate explicit confirmation for this disclosure.

Write confirmation does not authorize `--show-text`. Never use it routinely or
speculatively. It authorizes only displayed evidence-message text, never full
transcripts, tool output, command output, arguments, commands, diffs, file
content, or sidecar content.

## Choose Scope Without Guessing

Use public discovery surfaces only:

```text
session-doctor adapters list --scan
session-doctor sessions list [--agent AGENT]
session-doctor projects list --format json
```

Ask when more than one session, adapter, database, or exact project hint could
match. Do not decode source directory names or inspect files to disambiguate.
Parent/child IDs are topology references, not authorization to combine session
content.

## Command Workflows

### Version And Setup

Use `version` to verify the skill contract and `doctor` to inspect Python,
DuckDB, database-path, and adapter-root readiness. Do not convert a warning into
a failure or claim a check that `doctor` did not perform.

### Adapter Discovery

Use `adapters list` for supported adapters and default roots. `--scan` reads
candidate structure and counts only; it does not ingest. Preserve source-kind
and excluded counts rather than describing every discovered file as a session.

### Database Management

Use `db info` before assuming a database exists or has the current schema. `db
init` is a confirmed write. During 0.x, report schema incompatibility as an
explicit rebuild decision, not an automatic migration or silent fallback.

### Ingestion

Select one of `codex`, `claude`, or `pi`. Prefer an explicit user-selected
`--source`; if using the adapter default, state that scope before confirmation.
Do not claim that discovered candidates all parsed. Report selected, parsed,
ignored, warning, and failed counts separately when available. A nonzero ingest
exit remains a failure even when some sources succeeded.

### Session Listing

Use `sessions list --agent AGENT` when the agent is known; otherwise present the
agent column with ingested session IDs and structural summaries. Do not expose
or reinterpret source paths beyond the CLI's display. Ask the user to select an
exact session when needed.

### Analysis

Use one exact session ID or explicit `--all` filters, never both. Confirm first.
For one-session diagnosis, normally run:

```text
session-doctor analyze SESSION_ID --agent AGENT --no-artifact [--db PATH]
```

Use JSON only when structured interpretation is needed. `--all` is deliberate
coverage recovery; preserve succeeded, skipped, and failed counts and never
present partial failure as success.

### Aggregate Summary

Use `summary --format json` for deterministic aggregate interpretation. Preserve
analysis coverage, stale/missing distinctions, limits, redacted displays, and
recommendation wording. A project filter is an exact observed path scope, not a
repository identity.

### Trends And Project Discovery

Use `projects list --format json` to obtain exact hints before a project-scoped
trend request. Use `trends --format json` for aligned weekly/monthly cohorts.
Never weaken sample, density, coverage, or materiality gates. Keep top-level and
sidechain cohorts separate. Preserve `insufficient_data`, empty periods,
untimed/stale exclusions, and non-causal agent observations.

### Exact-Session Reports

Use `report SESSION_ID --agent AGENT --format json` for structured
interpretation, terminal for concise review, or Markdown when the user asks for
Markdown. Reports are read-only and do not ingest or analyze. Preserve
`current`, `stale`, or `missing` analysis status, bounded
`total/displayed/omitted` counts, unresolved evidence, limitations, and the
exact suggested analysis action. Do not silently recover stale/missing
analysis.

Use `--show-text` only through the separate disclosure protocol.

### Evidence Graphs

Use `graph SESSION_ID --agent AGENT --format json`. Graphs are exact-session,
complete, directed multigraph projections with topology-only parent/child
references. They never include message text. Treat edges as persisted
structural or deterministic evidence relations, not causal relations. Preserve
excluded rows, unresolved references, edge direction, and stale/missing
analysis state. Do not run graph algorithms or combine family graphs in the
agent context.

### Integration Asset

Use `integrations path` only to locate this bundled skill for manual inspection
or installation. It prints one path and never installs anything. If the asset is
missing, report the package-integrity failure and recommend reinstalling the
matching CLI; do not synthesize a replacement skill.

### Shell Completion

`--show-completion` is inspection-only. `--install-completion` modifies shell
configuration and requires the write confirmation protocol. Do not run either
unless the user asks about completion.

## Guarded Interpretation

Structure a diagnosis as:

1. **Observed facts** — normalized counts, timestamps, warning codes, and
   structural relations directly shown by the CLI.
2. **Deterministic findings** — current-version features, scores,
   classifications, and recurrence/trend outputs with their evidence IDs.
3. **Limitations** — stale/missing analysis, unresolved references, omitted
   bounded evidence, exclusions, sparse cohorts, or unavailable timestamps.
4. **Hypotheses for review** — clearly labeled possibilities, never facts.
5. **Next actions** — concrete human-review or CLI steps, with confirmation
   before writes or disclosure.

When discussing a specific finding, cite stable report or graph evidence IDs.
Do not cite guessed native IDs. Do not call a fingerprint secret-safe or treat
it as proof that two failures had the same cause.

## Fail Closed

- If the CLI is missing, show source-install guidance but do not install it
  without a request.
- If versions differ, stop rather than assuming 0.x compatibility.
- If the target is ambiguous, ask rather than broadening scope.
- If a command exits nonzero, report the stable error and the smallest public
  inspection step; do not retry a broader mutation.
- If JSON is invalid, report the parse failure; do not fabricate fields.
- If stale/missing analysis is returned, keep output partial until a separately
  confirmed analysis succeeds.
- If a privacy boundary cannot be maintained, stop.
