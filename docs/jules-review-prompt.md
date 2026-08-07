# Review task prompt for Jules

Copy this prompt into a Jules task. Replace the bracketed fields. Keep the
prompt in English so the output follows the contract in `AGENTS.md`.

## Prompt

```text
Perform a read-only review of the Magic-Lab-Studio/observer repository at
commit <commit-sha> (or current <branch> head). Do not modify any tracked file
and do not create a PR; report the review as a GitHub PR comment on the default
branch unless the task explicitly asks for a PR.

Context:

- Product name is "Observer" (a.k.a. "LLM Observatory", package
  magic-lab-observer). "ManitOS" is an integration lane, not the product name.
- Read AGENTS.md at the repository root and follow its review output contract
  exactly (Summary / Findings / Checklist / Verdict).
- Baseline: <commit-sha of last accepted review> for delta comparison.

What to review:

1. Confirm the repository identity and layout match AGENTS.md.
2. Lint: run `ruff check backend cli sdk/python` from the root. Report the
   actual result; if you cannot run it, say "not run" and use CI status.
3. Tests: run backend, sdk/python, and cli suites (see AGENTS.md). Report
   actual pass counts. If you cannot run them, say "not run" and rely on CI.
4. Check CI status for the branch (`gh pr checks` / Actions); do not invent
   results.
5. Diff against the baseline commit: list changed files, new/removed public
   endpoints or SDK exports, and any migration changes.
6. Consistency: scan for wrong product names ("LLM Observatory service",
   "ManitOS" as product), stale test counts in README/docs, and docs that
   contradict code.
7. Do not report "none" as a finding. Every finding must carry evidence
   (path:line) and a concrete action. If nothing is actionable, state the
   verdict with the evidence that supports PASS and the checklist items you
   actually ran.

Output exactly per the contract in AGENTS.md.
```

## Rules baked into the prompt

- **Anti-hallucination:** never report tests, lint, or CI results you did not
  produce or read; otherwise mark "not run".
- **Evidence required:** no `path:line`, no finding.
- **Delta thinking:** compare against a real baseline commit, not a blank page.
- **No useless PRs:** read-only reviews with no changes go in a comment or
  issue, not a PR that only adds `pr_body.txt`.

## Troubleshooting

- If the output contradicts itself (e.g., "tests not run" in the description
  but "119 passed" in an attached file), the task was generated from a stale
  template. Regenerate from this file.
- If the output references a product other than Observer/LLM Observatory, the
  task context leaked from another repository. Re-run with this prompt.
