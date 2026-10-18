---
name: skip-review-fixes
description: >-
  Records the current canvas fixes as dismissed without applying them.
  Use when the user says skip-review-fixes or @skip-review-fixes.
  Does not launch the reviewer.
disable-model-invocation: true
---

# Skip review fixes

Project rules come from `PROJECT.md` `## Quality bar` and from INDEX/notes. Settings come from `.agent-tools/config.md`.

## Resolve the engine

Use the first directory that contains `bin/apply-review-fixes`:

1. `$MERGE_REVIEWER_HOME`
2. `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer`
3. `<git-root>/tools/merge-reviewer`

Read only `MERGE_REVIEWER_HOME` and `CURSOR_TEMPLATE_HOME` from `~/.config/review-change/cursor.env`. Do not print other keys.

## Do

1. From the git root, run `<engine>/bin/apply-review-fixes --skip`.
2. Quote the `dismissed N note(s)` line. The ledger keeps those anchors for this branch, so the reviewer does not raise them again.
3. Do not edit the files listed in `var/review-change.fixes.json`.
4. If `.cursor/review-change.skip.request` exists, leave its first line as `canvas-trigger`. The engine rewrites the trigger.
5. Tell the user the diffs were ignored. They can still apply them later while the JSON exists.
6. Do not commit, push, or launch the reviewer.
