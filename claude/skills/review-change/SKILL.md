---
name: review-change
description: >-
  Claude Code only. Starts the post-commit reviewer for this project from Claude Code. One
  analysis by a fresh headless Claude, then a script-rendered HTML and
  Markdown report. Use when a coherent feature is done, or the user says
  review-change. Do not review in this chat. Do not use it to apply or skip
  fixes (those are apply-review-fixes and skip-review-fixes).
---

# Review change (Claude Code)

Do not review the diff in this chat. Do not write the report yourself. The engine runs a separate headless `claude -p` and renders the report from its JSON.

## Resolve the engine

Use the first directory that contains `bin/post-commit-review`:

1. `$MERGE_REVIEWER_HOME`
2. `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer`
3. `<git-root>/tools/merge-reviewer`

Both variables are also keys in `~/.config/review-change/cursor.env`. Read only those key names. Do not print any other value from that file.

## Do

1. From the git root, run `REVIEW_CHANGE_HOST=claude <engine>/bin/post-commit-review`. It starts the review in the background and returns at once.
2. Tell the user the report paths from the command output: `var/review-change.html` (open in a browser), `var/review-change.md`, and `var/review-change.log`.
3. Say that the review is running and that you will mention the verdict when it finishes. A hook announces a finished review on the next prompt. Do not poll in a loop.

The engine reads settings from `.agent-tools/config.md`, applies `## Quality bar` from `PROJECT.md`, runs the pre-review command when it is set, and writes `var/review-change.analysis.json`. The model is **Claude review model** (default `sonnet`); `REVIEW_CHANGE_MODEL` overrides it.

Do not launch a second reviewer. Do not split the diff across models. Skip when `HEAD` is the default branch. The engine already does that.

## After the review

- To apply the proposed replacements the user says `/apply-review-fixes`. To dismiss them, `/skip-review-fixes`.
- Create a merge request only when the user asks: `REVIEW_CHANGE_HOST=claude <engine>/bin/post-commit-review --create-mr` pushes the current branch and opens the host's new-merge-request page.
