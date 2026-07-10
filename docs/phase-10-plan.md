# Phase 10 Plan: Optional Agent Integration And v0.1.0 Dogfood Release

Status: complete; annotated tag pending explicit approval.

## Goal

Finish the first roadmap without expanding the diagnostic core. Phase 10 ships
one portable, optional agent skill for Codex, Claude Code, and Pi, makes the
skill discoverable from an installed `session-doctor` package, reconciles the
design with the implemented product, and marks a safe dogfood baseline with an
annotated `v0.1.0` Git tag.

The CLI remains the product and the sole owner of discovery, parsing,
normalization, persistence, analysis, reporting, graph projection, privacy, and
error semantics. The skill is only an orchestration and interpretation layer.

This is a lightweight dogfood milestone, not a stable public package release.
Phase 10 does not add CI, publish to PyPI, create a GitHub Release object, or
promise 0.x compatibility.

## Approved Decisions

The grilling decisions are load-bearing:

1. Ship wrappers for Codex, Claude Code, and Pi; defer MCP/query-server work.
2. Implement one portable Agent Skills-standard `session-doctor` skill rather
   than three divergent copies.
3. Install the skill manually. The CLI may reveal its bundled path but must not
   mutate agent configuration or home directories.
4. Cover the entire public CLI while using `--help` as the option-level source
   of truth rather than duplicating every option in skill prose.
5. Use only public `session-doctor` commands. Never query DuckDB directly, read
   native transcripts directly, or duplicate parser/classifier logic.
6. Inspect before mutation and obtain explicit confirmation before `db init`,
   `ingest`, or `analyze` writes.
7. Obtain a separate explicit confirmation before every use of
   `report --show-text`.
8. Permit guarded interpretation only: preserve uncertainty, cite report/graph
   evidence IDs, distinguish observations from hypotheses, and never invent
   causality, intent, blame, project identity, or unsupported statistics.
9. Bundle the exact skill in the wheel and expose its location through a
   read-only `session-doctor integrations path` command.
10. Validate the package contract automatically and smoke-test discovery and
    explicit invocation in Codex, Claude Code, and Pi, retaining structural
    results only.
11. Add an MIT license with `ramtinJ95` as the copyright holder.
12. Keep one package-version source and retain pre-1.0 rebuild semantics until
    `v1.0.0`.
13. Add a privacy-safe dogfood issue template; add no telemetry.
14. Land Phase 10 in three PRs, each reviewed/fixed until exactly
    `NO FINDINGS`, then request explicit approval before pushing the annotated
    `v0.1.0` tag.

## Starting Point

At Phase 10 start:

- Phases 0 through 9 are complete.
- Codex, Claude Code roots/subagents, and Pi are supported native adapters.
- normalized and analysis rows live in local DuckDB schema version 4.
- summary, trends, project discovery, recurrence, report, and graph commands are
  implemented.
- reports and graphs are exact-session, deterministic, privacy-safe read
  projections.
- `pyproject.toml` and `session_doctor.__version__` both say `0.1.0`, but the
  duplicated version source can drift.
- the public repository has no license, no agent wrapper, no CI, no tags, and no
  release workflow.
- Phase 9 closed with 261 tests and a final holistic `NO FINDINGS` review.

## Scope

### In Scope

- reconcile `docs/session-doctor-design.md` with the completed Phase 9 product
- one bundled `session-doctor` Agent Skill
- documented manual installation for Codex, Claude Code, and Pi
- `session-doctor integrations path`
- strict skill/package tests
- guarded full-CLI workflow instructions
- local three-harness structural validation
- MIT license
- single-source package version
- package metadata needed for a truthful source/tag dogfood release
- privacy-safe dogfood issue template and known-limitations guidance
- local build, clean-install, CLI, skill, and full quality gates
- annotated and pushed `v0.1.0` tag after explicit user approval

### Out Of Scope

- MCP server, MCP tools, or a query protocol
- agent plugins, extension code, hooks, or background processes
- automatic wrapper installation or removal
- writing `~/.agents`, `~/.claude`, `~/.pi`, or project settings
- direct DuckDB queries or transcript reads from the skill
- a new diagnosis algorithm, score, label, report section, or graph relation
- new adapters, including OpenCode
- `explain`, `export`, JSONL export, or Parquet export
- raw tool output, command output, arguments, diffs, or transcript disclosure
- CI or branch-protection configuration
- PyPI publication
- a GitHub Release object or attached release artifacts
- semantic-version compatibility guarantees during 0.x
- schema migrations for pre-1.0 databases
- telemetry, analytics upload, crash reporting, or network calls by the CLI
- graphical graph rendering, graph algorithms, or persisted graph rows

## Portable Skill Contract

### Canonical Asset

Store one canonical skill inside the Python package:

```text
src/session_doctor/integrations/session-doctor/SKILL.md
```

The directory and frontmatter name must both be `session-doctor`. The skill must
follow the Agent Skills standard subset supported by all three target harnesses:

```yaml
---
name: session-doctor
description: ...
license: MIT
compatibility: Requires session-doctor CLI version 0.1.0.
metadata:
  session-doctor-version: "0.1.0"
---
```

The compatibility field must require exactly `0.1.0`, not “or later”; 0.x does
not promise forward compatibility. The metadata version is the machine-checkable
skill/CLI contract and must match the package's single version source.

Do not use platform-specific frontmatter, executable helper scripts, network
access, dependencies, or symlinks. Keep the skill self-contained. If one
platform later requires divergent behavior, add a tested narrow adapter then;
do not preemptively fork the instructions.

The skill is optional. Importing or running the Python package must not load it
into an agent automatically.

### Supported Install Destinations

Document explicit copy destinations:

```text
Codex global:       ~/.agents/skills/session-doctor/
Claude Code global: ~/.claude/skills/session-doctor/
Pi global shared:   ~/.agents/skills/session-doctor/
Pi global native:   ~/.pi/agent/skills/session-doctor/  (alternative)
```

Codex and Pi can share the same installed `.agents` copy. Claude Code receives
the same canonical directory under its skill root. Project-local destinations
may be documented as optional alternatives, but Phase 10 must not create them.

Installation instructions must:

1. run `session-doctor integrations path`
2. inspect the skill before installation
3. check whether the destination already exists
4. require the person to choose replacement/removal behavior
5. copy the whole `session-doctor` directory explicitly
6. explain each platform's invocation syntax

Never provide a destructive overwrite command as the default. Never silently
merge an older skill directory.

### Full Public CLI Coverage

The skill may orchestrate every implemented command group:

```text
version
doctor
adapters list
db init
db info
ingest
sessions list
analyze
summary
trends
projects list
report
graph
integrations path
```

The skill must not become a second CLI manual. It should classify the user's
request, run `session-doctor <command> --help` when option details matter, then
compose the smallest public workflow needed.

The skill must identify `explain`, `export`, MCP, raw transcript replay, graph
visualization, and unsupported adapters as unavailable rather than inventing a
substitute through direct SQL or source inspection.

### Lifecycle And Write Confirmation

Classify operations before execution.

Read-only or inspection operations:

```text
version
doctor
adapters list [--scan]
db info
sessions list
summary
trends
projects list
report
graph
integrations path
```

Database/artifact-writing operations:

```text
db init
ingest
analyze
--install-completion
```

Typer's framework-level `--install-completion` option writes shell
configuration and is therefore subject to the same explicit confirmation gate.
The non-mutating `--show-completion` option may be used without confirmation.

Before any writing operation, the skill must state:

- the exact public command it proposes to run
- the target database path, including when the CLI default will be used
- the source/agent/session scope
- that `ingest` can replace normalized and analysis rows for re-ingested
  sources
- that `analyze` replaces derived rows for selected sessions
- whether an analysis artifact would be written

Then it must obtain explicit confirmation. For wrapper-driven diagnosis,
prefer `analyze ... --no-artifact` unless the user specifically requests an
artifact. `analyze --all` already defaults to no artifacts; do not add
`--write-artifacts` without a separate explicit request.

A broad request such as “diagnose this session” is not write authorization.
Do not combine confirmation with unrelated questions. Do not treat prior
confirmation as permanent consent for later sources, sessions, or databases.

The skill must not bypass schema mismatch/rebuild errors. Explain the CLI's
reported state and ask before destructive local database removal; it must never
remove a database itself as an implicit recovery step.

### Session And Project Selection

Never guess a session ID, source path, repository root, or project identity.
Use public discovery surfaces:

- `adapters list --scan` for adapter source counts
- `sessions list` for ingested sessions
- `projects list` for exact observed project/CWD hints

If those surfaces cannot disambiguate the user's target, ask. Exact observed
path hints remain hints, not inferred repository identities. Top-level and
sidechain sessions remain distinct.

### Analysis Compatibility

Reports and graphs may return `current`, `stale`, or `missing` analysis. The
skill must preserve that state. It may offer the exact `analyze SESSION_ID`
recovery command, subject to write confirmation, but must never:

- describe stale/missing output as complete
- silently run analysis
- combine stale findings with current metrics
- weaken fixed trend/recurrence gates
- infer findings absent from the payload

### Message Disclosure

Default reports and all graphs remain message-text-free.

Before `report --show-text`, the skill must separately say that displayed
persisted evidence-message text will enter the active agent context and obtain
an explicit per-use confirmation. Confirmation to ingest/analyze does not count.
The skill must not use `--show-text` speculatively, retain revealed text in a
report, or request tool output, arguments, diffs, commands, or full transcripts.

### Interpretation Contract

The agent may summarize deterministic output and offer next actions, but every
interpretation must separate:

1. observed normalized facts
2. persisted deterministic findings/scores
3. recurrence/trend availability and fixed-gate status
4. agent hypotheses requiring human review

Use stable evidence IDs from JSON report/graph output when discussing a
specific finding. Do not claim that one event caused another, assign fault to a
user or agent, infer intent, invent project identity, treat fingerprints as
secrets, or make population/statistical claims from small deterministic
samples.

If the CLI says `insufficient_data`, `stale`, `missing`, unresolved, omitted, or
excluded, retain that limitation. Do not fill gaps from direct source reads.

### Failure Behavior

- If `session-doctor` is absent, show installation-from-source guidance; do not
  install software without a request.
- If the CLI version does not exactly match the skill metadata version, stop
  and report both versions. Obtain the matching skill from that CLI package;
  never assume a later 0.x CLI is compatible.
- If a command exits nonzero, surface the stable CLI error and proposed next
  inspection step; do not retry mutation with broader scope.
- If JSON cannot be parsed, retain stdout/stderr locally in the active context
  only long enough to explain the command failure; do not fabricate a payload.
- If a privacy boundary cannot be satisfied, stop.

## Integration Path Command

Add a Typer subgroup and command:

```bash
session-doctor integrations path
```

Contract:

- read-only and local-only
- prints one absolute filesystem path to the bundled
  `session-doctor` skill directory
- no labels, table, ANSI styling, JSON wrapper, or surrounding prose on stdout
- stable trailing newline is allowed
- resolves package data through `importlib.resources`, not repository-relative
  assumptions or the current working directory
- validates that `SKILL.md` exists before printing
- exits nonzero with a stable package-integrity message if the asset is missing
- never creates/copies/updates/removes files
- does not accept a destination or platform flag

A raw path is intentionally composable with shell/manual install instructions.
There is no `integrations install` command in Phase 10.

## Packaging Contract

Use one version source:

```text
src/session_doctor/__init__.py::__version__
```

Configure Hatch to read that value dynamically for wheel/sdist metadata. Tests
must prove CLI version and built distribution metadata agree.

Explicitly configure Hatch package data so the canonical `SKILL.md` appears in
both wheel and sdist. Do not rely on accidental inclusion. A clean wheel install
must make `session-doctor integrations path` point to an existing skill.

Add truthful project metadata appropriate for a source/tag dogfood release:

- MIT license expression/file
- source/repository URL
- concise keywords/classifiers only if accurate
- Python `>=3.12`
- README as package description

Do not add publishing credentials, package indexes, release automation, CI, or
PyPI-specific workflow configuration.

## License

Add the standard MIT license text:

```text
Copyright (c) 2026 ramtinJ95
```

The Python package and bundled skill use the same repository license. Reference
`MIT` in skill frontmatter and package metadata; do not duplicate a modified
license inside the skill directory.

## Pre-1.0 Compatibility

The `v0.1.0` dogfood tag does not freeze schemas, CLI contracts, payloads,
artifacts, adapters, or analysis formulas. Until `v1.0.0`, clean model changes
may require users to rebuild local DuckDB files and regenerate artifacts.

Documentation must correct any statement that migration compatibility begins at
the first release. Compatibility begins only when the project deliberately
declares a stable contract, no earlier than 1.0. Dogfood fixes should favor a
clean model over migration machinery.

Every 0.x breaking change must still be explicit in release notes and error
messages. “No compatibility guarantee” is not permission for silent corruption,
stale analysis reuse, or ambiguous fallback behavior.

## Dogfood Feedback Contract

Add a GitHub issue form or template for dogfood findings. It must request only:

- `session-doctor` version
- operating system and Python version
- adapter (`codex`, `claude`, or `pi`)
- public command and safe flags, with secrets removed
- current/stale/missing analysis state when relevant
- structural counts, warning codes, and exit status
- expected behavior
- actual behavior in privacy-safe terms
- minimal synthetic reproduction when available

It must prominently forbid:

- transcripts or message text
- prompts
- source paths or project names
- commands containing secrets
- tool/command output
- arguments, diffs, or file content
- native session/event IDs and hashes/fingerprints
- DuckDB files or analysis artifacts from private sessions

Add no telemetry or automatic feedback export. The issue template is guidance,
not a claim that submitted material is automatically safe.

## Documentation Reconciliation

PR 1 updates the design document so it no longer contradicts the completed
product:

- extend the implemented vertical slice through reports and graph projection
- include Phase 9 diagnostic/report/graph coverage in current tests
- replace obsolete causal graph aspirations with the conservative implemented
  node/edge vocabulary and explicitly deferred graph ideas
- mark the Claude metadata question resolved
- change compatibility wording from “first release” to `v1.0.0`
- describe Phase 10 as optional integration and dogfood release completion
- retain `explain`, `export`, MCP, OpenCode, ML, and graphical views as deferred

Historical phase descriptions may describe what existed at that phase. Do not
rewrite historical constraints as if later work was already present.

## Validation

### Automated Contract Tests

Add tests that prove:

- `session-doctor integrations path` exits zero and emits exactly one absolute
  existing directory
- the emitted directory contains exactly the canonical expected skill assets
- command execution does not change package files, DuckDB tables, artifacts, or
  agent configuration
- missing bundled `SKILL.md` produces the stable integrity failure
- skill frontmatter has the exact name, non-empty bounded description, MIT
  license, exact package compatibility/metadata version, and no unsupported
  platform-specific fields
- directory and skill names match
- skill prose names every implemented command group
- unsupported/deferred surfaces are explicitly rejected
- direct SQL/transcript/native-source access is forbidden
- write and `--show-text` confirmation rules are present
- guarded interpretation/privacy rules are present
- wheel and sdist contain the canonical skill
- clean wheel installation resolves the same asset and reports `0.1.0`
- package metadata and `session_doctor.__version__` agree

Tests should verify durable semantic markers, not snapshot the entire prose or
make harmless wording edits expensive.

### Three-Harness Smoke

After automated tests pass, validate copied/installed skill discovery and
explicit invocation in current local Codex, Claude Code, and Pi harnesses.
Use temporary or deliberately isolated skill locations where supported. Do not
modify existing user skills without explicit user action.

The smoke prompt should ask only for a structural response proving that the
skill loaded, located the CLI, reported the CLI version, and described its
write/privacy gates. It must not ingest, analyze, inspect a real database, scan
native stores, or disclose session content.

Retain only:

- harness name/version when safely available
- skill discovered: yes/no
- explicit invocation succeeded: yes/no
- CLI version matched: yes/no
- write confirmation rule present: yes/no
- `--show-text` confirmation rule present: yes/no
- direct-source/SQL prohibition present: yes/no

Delete temporary skill copies, prompts, command output, and ad-hoc scripts after
validation. Do not retain model prose, local paths, account information, native
IDs, or hashes.

### Local Release Gate

No CI is required for this dogfood tag. Run and record locally:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv build
```

Then:

1. inspect wheel and sdist contents
2. install the wheel into a fresh temporary environment
3. run `session-doctor version`, `doctor`, `integrations path`, and `--help`
4. validate the bundled skill from the installed wheel
5. run a synthetic ingest/analyze/report/graph smoke in the clean install
6. confirm no unexpected repository changes or retained private material
7. run `git diff --check`
8. run a final holistic blocker review until exactly `NO FINDINGS`

Do not claim CI coverage. Record this as local validation with date, commit, and
structural outcomes.

## Release Blockers

The `v0.1.0` tag is blocked by any known:

- privacy or secret disclosure through default output or wrapper behavior
- source-log mutation, database corruption, silent row loss, or unsafe retry
- nondeterministic diagnostic payload under identical inputs
- unsupported causal, intent, blame, project-identity, or statistical claims
- stale/missing analysis presented as current
- exact-session, sidechain, recurrence, or topology boundary violation
- documented command failure
- wheel/sdist omission or clean-install failure
- wrapper discovery/invocation failure in any target harness
- version mismatch across CLI, package metadata, skill compatibility, and tag
- unresolved blocker review finding

Ordinary wording, convenience, UI, performance, and broader feature gaps should
enter the privacy-safe dogfood backlog instead of delaying the tag unless they
make a documented workflow unusable.

## Three-PR Delivery

### PR 1: Approved Plan And Design Reconciliation

Files:

```text
docs/phase-10-plan.md
docs/session-doctor-design.md
README.md
```

Work:

- land this approved plan
- correct stale Phase 9/design statements
- define Phase 10 as optional integration plus dogfood release completion
- link the plan from public documentation

Gate:

- blocker-only plan review/fix cycles until exactly `NO FINDINGS`
- documentation consistency scan
- `git diff --check`

### PR 2: Portable Skill And Integration Surface

Expected files:

```text
src/session_doctor/integrations/session-doctor/SKILL.md
src/session_doctor/integration_assets.py
src/session_doctor/cli.py
pyproject.toml
tests/test_integrations.py
README.md
```

Work:

- add the canonical full-CLI skill
- add read-only `integrations path`
- explicitly bundle package data
- document safe manual installation for all three harnesses
- test skill semantics, command behavior, and package-data presence

Gate:

- full quality suite
- source-tree path command smoke
- blocker-only review/fix cycles until exactly `NO FINDINGS`

### PR 3: Dogfood Release Completion

Expected files:

```text
LICENSE
CHANGELOG.md
.github/ISSUE_TEMPLATE/dogfood.yml
docs/phase-10-validation.md
docs/session-doctor-design.md
README.md
pyproject.toml
uv.lock
tests/...
```

Work:

- add MIT license
- establish single-source version and truthful package metadata
- add privacy-safe dogfood reporting guidance
- run/record three-harness structural smoke
- build and clean-install wheel/sdist
- run/record the full local release gate
- mark Phase 10 complete only after evidence exists

Gate:

- all safety/correctness release blockers closed
- full quality/build/install/synthetic smoke
- final holistic review/fix cycles until exactly `NO FINDINGS`
- clean synchronized `main` after rebase merge

### Tag Step

The tag is not part of a PR. After PR 3 is merged:

1. checkout `main`
2. `git pull --ff-only origin main`
3. rerun the minimal final status/version checks
4. show the exact target commit and proposed annotated tag message
5. obtain explicit user approval
6. create annotated tag `v0.1.0`
7. push only `refs/tags/v0.1.0`
8. verify the remote tag resolves to the approved commit

Do not create or move the tag before approval. Never retag a different commit
under the same version. If a blocker appears after tagging, fix it in a later
0.x release rather than rewriting the public tag.

## Acceptance Criteria

Phase 10 is complete when:

- the design and README accurately describe Phases 0–10
- one canonical Agent Skills-standard wrapper ships in source, wheel, and sdist
- Codex, Claude Code, and Pi installation/invocation are documented
- `session-doctor integrations path` is read-only, stable, and package-relative
- the skill covers the public CLI without direct SQL/source access or duplicated
  business logic
- all writes and message-text disclosure require the approved confirmations
- wrapper interpretations remain evidence-citing, guarded, and non-causal
- MIT licensing and package metadata are truthful
- version has one source and equals `0.1.0` everywhere
- pre-1.0 rebuild policy is explicit through 0.x
- privacy-safe dogfood reporting exists without telemetry
- automated package/skill tests and three live harness smokes pass
- wheel/sdist build and clean-install smoke pass locally
- every Phase 10 PR review returns exactly `NO FINDINGS`
- PR 3 marks Phase 10 complete and records local validation
- after separate approval, remote annotated tag `v0.1.0` points at the final
  synchronized `main` commit

## Dogfood Follow-Up

After tagging, use the tool for several weeks before adding CI, publishing to
PyPI, or broadening integrations. Prioritize observed failures in this order:

1. privacy/security or mutation safety
2. parser correctness and source-format drift
3. deterministic evidence/classification correctness
4. exact-session, topology, trend, and recurrence boundaries
5. wrapper workflow friction
6. performance and UX
7. speculative features

Dogfood evidence may justify `v0.1.1`, `v0.2.0`, schema rebuilds, or revised
analysis formulas. It should not be laundered into compatibility promises or
population-level accuracy claims.
