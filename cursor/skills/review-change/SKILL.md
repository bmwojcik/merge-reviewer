---
name: review-change
description: >-
  Starts the post-commit reviewer for this project. One analysis, then a
  script-rendered Cursor canvas. Use when a coherent feature is done, or
  the user says review-change. Do not review in this chat. Do not launch
  this skill when applying or skipping canvas diffs.
---

# Review change

Do not review in this chat. Do not write the canvas yourself.

## Resolve the engine

Use the first directory that contains `bin/post-commit-review`:

1. `$MERGE_REVIEWER_HOME`
2. `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer`
3. `<git-root>/tools/merge-reviewer`

Both variables are also keys in `~/.config/review-change/cursor.env`. Read only those key names. Do not print any other value from that file.

## Do

1. From the git root, run `<engine>/bin/post-commit-review`.
2. The engine reads settings from `.agent-tools/config.md` (or legacy `PROJECT.md` keys), applies `## Quality bar` from `PROJECT.md`, runs the pre-review command when it is set, writes one analysis JSON, and renders the canvas.
3. Tell the user the canvas path and `var/review-change.log` from the command output.

If this host cannot show a Cursor canvas, stop. Say that the canvas renders only in Cursor.

Do not launch a second reviewer. Do not split the diff across models.

## Modes

| User said | What to run |
| --- | --- |
| review, review-change | `<engine>/bin/post-commit-review` |
| create the merge request | `<engine>/bin/post-commit-review --create-mr` |

`--create-mr` pushes the current branch and opens the host's new-merge-request page. Run it only when the user asked.

Skip when `HEAD` is the default branch. The engine already does that.
