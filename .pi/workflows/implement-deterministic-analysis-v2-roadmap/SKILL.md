---
name: "Implement deterministic analysis v2 roadmap"
description: "Implement and merge every PR in the deterministic-analysis-v2 roadmap using gated review loops."
---

# Implement deterministic analysis v2 roadmap

Use this workflow to implement `docs/deterministic-analysis-v2-plan.md` from PR 1 through PR 23 without pausing between successfully merged PRs.

## Invariants

- Follow the roadmap in order; do not start a PR before its dependencies and merge gate are satisfied.
- Maintain one active semantic implementation. Make invalid downstream commands explicitly unavailable as specified by the plan.
- Keep each PR limited to its roadmap scope. Do not mix calibration and implementation work.
- Use current documentation via `npx ctx7 ...` for relevant libraries and frameworks.
- Use conventional, logical commits. Do not add `Co-Authored-By` lines.
- Keep PR descriptions concise, without checkboxes or generated-by footers.
- Never bypass hooks, approval guards, required checks, or destructive-operation safeguards.
- During code inspection, add any unrelated issue to `scratch/BACKLOG.md` with file and line references.
- High- and medium-severity reviewer findings and required CI checks are hard merge gates.
- Continue automatically between merged PRs. Stop only when continuing safely is impossible, such as an unresolvable product ambiguity, a blocked command requiring user approval, or a persistent external failure.

## Loop

For the next unmerged roadmap PR:

1. **Synchronize main**
   - Confirm the prior PR is merged and the working tree contains no unintended changes.
   - Check out `main`.
   - Pull using fast-forward-only semantics from the configured remote.
   - Re-read the target PR section, its dependencies, gate, and the global requirements.

2. **Create the branch**
   - Use a branch name like `v2/pr-NN-short-slug`.
   - Branch from the freshly synchronized `main`.

3. **Implement only that PR**
   - Inspect existing behavior before adding new structures.
   - Resolve implementation-level choices owned by this PR.
   - Prefer simplifying or replacing stale code over preserving parallel v1/v2 behavior.
   - Add or update focused tests as the implementation proceeds.
   - Run focused checks frequently and the plan's full required checks before opening the PR.

4. **Create logical commits**
   - Group changes into independently understandable conventional commits.
   - Keep generated files with the change that generates them.
   - Verify the branch diff and commit history against `main`.

5. **Push and open the PR**
   - Push the branch and set its upstream.
   - Open a concise PR describing what changed, why, tests run, and the roadmap gate addressed.
   - Do not merge yet.

6. **Run the reviewer loop**
   - Spawn a fresh `reviewer` subagent against the complete PR diff and current PR requirements.
   - Require prioritized findings with exact file and line references.
   - Fix every valid high- and medium-severity finding.
   - If a high/medium finding is intentionally rejected, record a concrete technical reason in the working notes and obtain another reviewer pass; do not silently dismiss it.
   - Log unrelated or deferred findings in `scratch/BACKLOG.md` with file and line references.
   - Run affected tests, create logical conventional commits, and push each fix batch.
   - Spawn a new reviewer against the updated PR.
   - Repeat until a fresh review reports no high- or medium-severity findings.

7. **Pass merge gates**
   - Run the full required local checks from the plan:

     ```bash
     uv run ruff format --check .
     uv run ruff check .
     uv run ty check
     uv run pytest -q
     ```

   - Confirm required remote checks pass on the latest pushed commit.
   - Confirm the roadmap PR gate is satisfied and no stale v1/partial-v2 fallback is exposed.
   - Confirm the PR is mergeable and still based on current `main`; update it safely if required and rerun affected checks/review.

8. **Merge and continue**
   - Rebase-merge the PR and delete the remote branch.
   - Check out `main` and fast-forward it from the remote.
   - Verify the PR landed and the working tree is clean.
   - Move immediately to the next roadmap PR.

## Completion

After PR 23:

- verify every roadmap PR and final gate passed;
- verify the final-test and release-state requirements in PR 23 were followed exactly;
- run the full required checks once more on synchronized `main`;
- report merged PR links, final versions, validation results, and any remaining backlog entries.
