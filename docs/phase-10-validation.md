# Phase 10 Integration And v0.1.0 Dogfood Validation

Date: 2026-07-10

Base revision: `main` at `e61f156`, plus the Phase 10 release-completion changes
recorded with this note.

Validation covered package metadata and artifacts, a clean wheel installation,
one synthetic installed-CLI diagnostic flow, and isolated explicit skill
invocation in current Codex, Claude Code, and Pi harnesses. It retained no
model prose, prompts, local paths, account information, session IDs, native IDs,
hashes, transcript text, commands from real sessions, tool output, database, or
temporary artifacts.

## Package And Version Contract

- package/CLI/skill version: `0.1.0`
- version owner: `src/session_doctor/__init__.py`
- Hatch metadata version: dynamic from the package owner
- license expression: MIT
- license file: standard MIT text, copyright 2026 `ramtinJ95`
- source and issue URLs: present
- wheel: `session_doctor-0.1.0-py3-none-any.whl`
- sdist: `session_doctor-0.1.0.tar.gz`
- canonical Agent Skill present in wheel and sdist: yes
- wheel metadata, bundled license, and skill metadata version agree: yes

No package was uploaded and no release object or tag was created during this
validation.

## Clean Wheel Installation

A fresh temporary virtual environment installed the locally built wheel and its
runtime dependencies. The installed executable then completed this structural
smoke:

| Check | Result |
| --- | --- |
| `version` reports `0.1.0` | pass |
| root help includes integrations | pass |
| `doctor` succeeds against a temporary database path | pass |
| `integrations path` resolves installed `SKILL.md` | pass |
| synthetic Codex fixture ingests | pass |
| exact synthetic session analyzes without artifact | pass |
| JSON report has current analysis | pass |
| graph node/edge IDs are unique | pass |
| graph endpoints all resolve | pass |
| report/graph reads leave table-row totals unchanged | pass |
| analysis/report/graph artifacts absent | pass |

The environment, database, output, and ad-hoc script were removed after the
run.

## Three-Harness Skill Smoke

Each smoke used an isolated temporary copy of the canonical skill and explicit
invocation. It permitted only `session-doctor version`; it did not scan adapter
roots, inspect source/session files, open a database, ingest, analyze, disclose
message text, or write a persistent agent session where the harness offered an
ephemeral mode.

Harness versions:

- Codex CLI `0.144.0`
- Claude Code `2.1.205`
- Pi `0.80.6`

| Contract | Codex | Claude Code | Pi |
| --- | --- | --- | --- |
| skill discovered | pass | pass | pass |
| explicit invocation | pass | pass | pass |
| CLI version matched | pass | pass | pass |
| write confirmation rule retained | pass | pass | pass |
| separate `--show-text` confirmation retained | pass | pass | pass |
| public-CLI-only boundary retained | pass | pass | pass |
| invocation exited successfully | pass | pass | pass |

Temporary skill copies and model output were deleted. These structural smokes
show that each harness can discover and invoke the shared instructions; they do
not establish diagnosis quality or cross-model behavioral guarantees.

## Automated And Local Gate

The final repository gate contains 272 tests, including:

- integration path success, stable failure, and no-write behavior
- Agent Skills frontmatter/version semantics
- complete public CLI and deferred-surface markers
- write, message disclosure, stale-analysis, privacy, and guarded-interpretation
  rules
- wheel/sdist content inspection
- fresh no-dependency wheel asset resolution
- single-source version, license, changelog, and dogfood-template contracts
- all prior adapter, persistence, analysis, summary, trend, recurrence, report,
  graph, and native end-to-end coverage

Local checks:

```text
ruff format --check: pass
ruff check: pass
ty check: pass
pytest: 272 passed
uv build: wheel and sdist pass
git diff --check: pass
```

There is deliberately no CI claim for this dogfood baseline.

## Dogfood Safety Boundary

The repository now includes a privacy-safe dogfood issue template. It requests
version/environment, adapter, safe public command shape, analysis state,
structural counts/warning codes, expected/actual behavior, and synthetic
reproduction. It warns against transcripts, prompts, message text, paths,
project/file names, native IDs, hashes/fingerprints, secret-bearing commands,
outputs, arguments, diffs, content, DuckDB files, and private artifacts.

No telemetry, crash upload, package publication, MCP/query server, CI workflow,
or compatibility migration was added. During 0.x, clean schema/artifact rebuilds
remain allowed and must stay explicit.

## Tag Boundary

This record completes implementation and local validation only. The annotated
`v0.1.0` tag must point at synchronized final `main`, requires a separate user
approval after the final PR is merged, and must not be moved afterward.
