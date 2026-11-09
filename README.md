# merge-reviewer

A post-commit reviewer that works the same way in any git project, from Cursor or from Claude Code. The engine lives here. A project keeps no copy of it: `install/link-project.sh` symlinks the skills, agent, commands, and extension, and the git hook calls `bin/post-commit-review`.

Two review hosts share one engine, one prompt, and one analysis JSON:

| Host | Runs the analysis | Shows the result |
| --- | --- | --- |
| Cursor | `cursor-sdk` or the `agent` CLI, model `grok-4.7` | A Cursor canvas with Apply / Skip / Create MR buttons |
| Claude Code | headless `claude -p`, model `sonnet` | `var/review-change.html` and `var/review-change.md`, announced by a Claude Code hook |

Nothing is pushed until a person asks for the merge request (the canvas button, or `--create-mr`).

## How it works

1. You commit on a laptop. The git hook (installed by the project template from `install/post-commit.sh`) starts `bin/post-commit-review` and returns immediately, so git is never blocked.
2. The engine reads `.agent-tools/config.md`. If that file is missing it uses the old keys in `PROJECT.md` and logs one migration line. Opt out on this laptop with `- **Merge reviewer:** off` in `<git-dir>/agent-tools.local`. Skip markers are `REVIEW_CHANGE=0`, `REVIEW_CHANGE_SKIP=1`, `[skip review]` in the subject, the default branch, a merge commit, and an in-progress rebase or merge.
3. Optional unit tests. When **Pre-review command** is set, that command runs first. Its summary is shown on the canvas. A failure is logged and does not fail the commit. `REVIEW_CHANGE_PHPUNIT=0` skips the command.
4. One model analysis (on the Claude host: one headless `claude -p`, see [Claude Code](#claude-code)). The script matches the diff against `docs/features/INDEX.md` (when that file exists) and inlines only the matching rows plus `docs/notes/<slug>.md` and `docs/notes/_general.md`. When **Agent layer** is `committed`, it also inlines a local-tool block so a tracked file replaced by a symlink is not judged lost. The model writes one JSON file, `var/review-change.analysis.json`. There is no shard split and no second model.
5. The script renders the canvas from that JSON (Cursor host). If rendering throws, one model fallback may write the canvas. The fallback does not re-review the diff. The Claude host renders the HTML and Markdown report instead.
6. Cursor host: buttons on the canvas open trigger files. The `review-apply` extension pastes **Apply fixes** or **Skip fixes** into the current chat, or opens a terminal that pushes and opens the host's new-merge-request page. Claude host: there are no trigger files; the report carries copy buttons for `/apply-review-fixes`, `/skip-review-fixes`, and the shell commands.

Default scope is the triggering commit (`HEAD~1...HEAD`). `REVIEW_CHANGE_SCOPE=branch` reviews the whole branch against the base. A file is remembered by git blob, so an unchanged file is not reviewed again. Skip stores the finding anchors on the branch so they are not raised again.

## Screenshots

English demo from `examples/demo` (`src/widget.py`). The finding, the proposed diff, and **Apply fixes** are the same blocks the canvas renders. Shop reviews stay on the laptop; these pictures do not use a real repository.

![Review in progress](docs/images/canvas-running.png)

The canvas while the analysis is still running.

![Verdict and finding](docs/images/canvas-verdict.png)

Verdict and the blocking note after the script renders the JSON.

![Proposed diff](docs/images/canvas-filediff.png)

The proposed replacement for `return name`.

![Buttons](docs/images/canvas-buttons.png)

Apply fixes, Skip fixes, and Create merge request.

Regenerate the live canvas with `bin/post-commit-review --demo` from `examples/demo`, then open the printed canvas in Cursor. The component paints only inside Cursor.

## Choosing the host

`- **Agent host:** cursor | claude | both` in `.agent-tools/config.md` (missing means `both`) says which agent the project is used from. A laptop override in `<git-dir>/agent-tools.local` wins. `link-project.sh` links only the files of that host.

The host that runs the review is the effective **Review host**: `REVIEW_CHANGE_HOST`, then **Review host** in `agent-tools.local`, then in `config.md`, then derived from **Agent host** (`cursor` is cursor, `claude` is claude, `both` is `auto`). `auto` is decided when the commit happens: `CLAUDECODE=1` (Claude Code sets it for its Bash tool, so a commit made by Claude Code inherits it) means claude, anything else means cursor. The detached review process receives the decision in `REVIEW_CHANGE_HOST`.

## Claude Code

The Claude host needs the `claude` CLI, logged in (a subscription or `ANTHROPIC_API_KEY`). It needs no Cursor key. It does this:

- Runs one `claude -p --model sonnet` with the analysis prompt on stdin. Tools are limited to Read, Grep, Glob, read-only `git diff/show/log/status/blame`, and a Write that is allowed only for `var/review-change.analysis.json` (`--permission-mode dontAsk`). The child runs with `REVIEW_CHANGE=0`, no project hooks, no MCP servers, and no session file, so it cannot start another review. The timeout is 900 s (`REVIEW_CHANGE_CLAUDE_TIMEOUT`). A failure or timeout is logged to `var/review-change.log` and never fails git.
- Writes a running placeholder at once, then `var/review-change.html` (single file, no external assets, light and dark, collapsible findings, diff highlighting, copy buttons) and `var/review-change.md`. With `REVIEW_CHANGE_OPEN=1` it opens the HTML with `wslview`, `xdg-open`, or `explorer.exe`.
- Writes `var/review-change.status.json`. The hook `claude/hooks/review-status.py` reads it. On `SessionStart` and `UserPromptSubmit` it adds one context message per session when a review has finished (verdict, counts, paths, the `/apply-review-fixes` and `/skip-review-fixes` skills). After a `git commit` run through the Bash tool (`PostToolUse`) it says the review started in the background. It runs in about 20 ms and always exits 0.
- `/apply-review-fixes` and `/skip-review-fixes` are the same skills Cursor uses. `/review-change` (`claude/skills/review-change`) starts a review by hand. `claude/agents/project-reviewer.md` holds the reviewer rules; the engine passes its body as the system prompt of the headless run.

`link-project.sh` merges the hook entries into `<project>/.claude/settings.local.json` (laptop-only; created if missing). Every other key and hook in that file is kept. Our entries carry the marker `# merge-reviewer-hook` in the command, and `--unlink` or a narrowed host removes only those. `.claude/settings.json` and `.claude/commands` are never touched.

## Install

1. Clone this repository.
2. `bash install/install-runtime.sh [--host cursor|claude|both]`  
   `cursor` (the default) installs the agent CLI, `pip install --user cursor-sdk`, the review-apply extension, and asks for the `crsr_` dashboard key. The key is stored in `~/.config/review-change/cursor.env` (mode 600). `claude` only checks that the `claude` CLI exists and is logged in; it installs and asks for nothing. `both` does both. Every host writes `MERGE_REVIEWER_HOME` to the env file. The script never prints a key.
3. Link a project with the project-template installer, or directly:

   ```bash
   bash install/link-project.sh /path/to/project [--host cursor|claude|both]
   ```

   The project's git hook is `install/post-commit.sh`, copied by the project template. It resolves `MERGE_REVIEWER_HOME`, then `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer`, then `<project>/tools/merge-reviewer`. If none of those exist it prints one line and exits 0.
4. Reload the Cursor window (Cursor host) or start a new Claude Code session (Claude host).

Without `--host` the linker reads **Agent host** (see above). Narrowing the host removes the links this repository owns for the dropped host. `install/link-project.sh /path/to/project --check` prints `ok`, `missing`, `stale`, or `conflict` for each link, then `host:`, `review_host:`, and `opted_out:`. `--unlink` removes only links that point into this repository, our hook entries, our part of the managed exclude block, and trigger files whose first line is `canvas-trigger`. The exclude block lives in the repository's shared `info/exclude`; lines that a sibling worktree still needs are kept.

## Configuration

Tool settings are bullets in `.agent-tools/config.md`. Project blocking rules stay in `PROJECT.md` under `## Quality bar`. See [docs/configuration.md](docs/configuration.md) for every key, its default, and the environment variables. The contract a project must satisfy is [docs/project-contract.md](docs/project-contract.md).

## Limits

- The canvas renders only in Cursor. The Claude host renders an HTML and Markdown report instead and has no click-to-apply buttons; apply and skip run as the skills or as the shell commands in the report.
- One analysis per commit. Large diffs stay one pass unless `REVIEW_CHANGE_MAX_DIFF_KB` is set.
- The hook never fails git. Tests that fail are reported on the canvas and in `var/review-change.log`.
- Create merge request is the only push, and only after the click. It uses the host URL from **Git host** / **Git project**, or from `origin` when those are empty (GitHub, GitLab-style, Bitbucket).
