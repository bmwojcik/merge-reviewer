---
name: apply-review-fixes
description: >-
  Applies unique old-to-new replacements from var/review-change.fixes.json.
  Use when the user says apply-review-fixes or @apply-review-fixes.
  Does not commit, push, or create a merge request.
disable-model-invocation: true
---

# Apply review fixes

Project rules come from `PROJECT.md` `## Quality bar` and from INDEX/notes. Settings come from `.agent-tools/config.md`.

## Resolve the engine

Use the first directory that contains `bin/apply-review-fixes`:

1. `$MERGE_REVIEWER_HOME`
2. `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer`
3. `<git-root>/tools/merge-reviewer`

Read `MERGE_REVIEWER_HOME` and `CURSOR_TEMPLATE_HOME` from the environment or from `~/.config/review-change/cursor.env`. Do not print any other key from that file.

## Do

1. From the git root, run `<engine>/bin/apply-review-fixes`.
2. Quote the script's `applied`, `unchanged`, `skip missing`, and `skip changed` lines. `skip changed` means the file was edited after the review.
3. If `var/review-change.fixes.json` is missing, stop. There is nothing to apply.
4. Do not commit, push, or create a merge request.
5. Do not launch the reviewer.

Applying edits changes the git blob, so the next commit reviews those files again and carries the previous notes in the prompt.

## Do not

- Re-type the replacements by hand unless the script exits non-zero.
- Apply a subset. Each `old` string must match once.
- Touch files that are not listed in the JSON.
