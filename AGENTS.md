# AGENTS.md instructions for session-doctor

These instructions apply to this directory and every child directory beneath it.

## Project Rules

- While scanning code, if anything looks off, log a stand-alone entry in `./scratch/BACKLOG.md` with filename and line numbers so it can be picked up later.
- Always use the ctx7 CLI, for example `npx ctx7 ...`, to fetch up-to-date documentation relevant to the project currently being worked on.
- Always try to give suggestions, compare solutions, and ask the user which one to use before implementing when the choice is not already clear.
- Ask questions when something is unclear. Do not assume risky details.
- Use parallel tool calls for anything that can be done in parallel.
- Never add yourself as a co-author in git commits. Do not include any `Co-Authored-By` line in commit messages.
- Do not add comments that restate obvious function, method, variable, or parameter names. Prefer clearer names over explanatory comments.
- Keep PR descriptions concise. Do not add checkboxes or generated-by footers.
- When a hook blocks a command, do not self-approve by writing to an approval file. Tell the user what was blocked and ask how they want to proceed.

## Pre-1.0 Compatibility

`session-doctor` has not released version 1.0 yet. Do not preserve backward
compatibility when it would limit the design, schema, data model, CLI contract,
artifact shape, or implementation quality.

Before version 1.0, prefer clean model and schema changes over migration
compatibility. Existing local DuckDB files, generated artifacts, fixtures, and
internal version markers may be rebuilt or regenerated as needed.
