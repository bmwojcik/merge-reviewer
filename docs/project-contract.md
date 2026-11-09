# Project contract

A project does not vendor this repository. It has:

- `.agent-tools/config.md` with the tool keys in [configuration.md](configuration.md). Comments there are HTML comments only.
- `PROJECT.md` for guidance. `## Quality bar` is the blocking bar (fallback `## Review rules`). `## Agent rules` may be passed as bounded context.
- Optional `docs/features/INDEX.md`. Rows are a markdown table. Column 1 is the slug, column 3 is modules, column 4 is keywords. A path matches a row when the slug is a path segment or file stem, or when a backticked path (or `Vendor_Module`, read as `app/code/Vendor/Module`) appears in the diff path.
- Optional `docs/notes/<slug>.md` and `docs/notes/_general.md`. Matching notes are inlined. If neither the index nor the notes exist, the reviewer uses the diff alone.
- A git hook whose body is `install/post-commit.sh`.

## Hook resolution

The hook sources `~/.config/review-change/cursor.env` when that file exists, then:

1. `$MERGE_REVIEWER_HOME/bin/post-commit-review`
2. `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer/bin/post-commit-review`
3. `<project>/tools/merge-reviewer/bin/post-commit-review`

If none exist it prints `merge-reviewer: engine not found (set MERGE_REVIEWER_HOME)` and exits 0.

## What gets linked

`install/links.txt` lists `path-in-this-repo path-in-project`. A leading `[stack]` limits the line to that **Stack** value. A leading `[host:cursor]` or `[host:claude]` limits it to a project whose **Agent host** (or `link-project.sh --host`) includes that host; the link of a host that is no longer wanted is removed when this repository owns it. The linker creates absolute symlinks. It refuses to replace a real file unless `--force`, and prints `conflict`.

Claude Code gets `.claude/skills/review-change` (the Claude version), `.claude/skills/apply-review-fixes`, `.claude/skills/skip-review-fixes`, and `.claude/agents/project-reviewer.md`. Each points straight into this repository. Nothing under `.claude/commands` and no `.claude/settings.json` is created or edited. The Claude Code hooks are merged into `.claude/settings.local.json` (see the README); a project's own committed `.claude/settings.json` is left alone. Claude Code cannot render the canvas; the Claude host writes `var/review-change.html` and `.md`.

`.cursor/hooks.json` is reviewer-only (`{"version": 1, "hooks": {}}`). If the project already has a `hooks.json` that is not our symlink, the linker leaves it and prints `ok`. Merge the empty hooks object by hand if the project needs both. The companion `.cursor/hooks/apply-review-on-open.py` is a no-op stdin stub; canvas buttons are handled by the `review-apply` extension, not by that hook.

For a project whose host includes Cursor, the linker also writes three real files (not symlinks; they are removed when the host narrows to claude):

- `.cursor/review-change.apply.request`
- `.cursor/review-change.skip.request`
- `.cursor/review-change.mr.request`

The same three files are mirrored into `.cursor/` beside the nearest `*.code-workspace` found by walking up to four parent directories. The review watcher listens on the repository `.cursor` and that workspace `.cursor`. When the workspace directory sits inside a git checkout, those paths are added to that checkout's `.git/info/exclude` under the same managed block. A directory that is not a git checkout still receives the files; there is nothing to exclude.

## Settings file

`.agent-tools/config.md` is read first. Bullets are `- **Key:** value`. HTML comments are ignored. If the file is missing, the same keys are taken from `PROJECT.md` and the engine logs one line: `merge-reviewer: settings still in PROJECT.md — run cursor-template install.sh --migrate-config`. A key missing from the chosen file uses the default. It is not filled from the other file.

A bullet value is empty when it is `none`, `empty`, `(empty)`, `REPLACE_ME`, starts with `leave empty` or `empty`, is a lone dash, or starts with an em dash, an en dash, or a hyphen comment (`- note`). A token that merely begins with a hyphen, such as `-sit=origin/sit`, is kept.

` — ` or ` – ` drops the text after the dash only when that text is an explanatory comment: it starts with a lower-case word or a bracket, or it contains `. , ; :`, or it is three or more words. A short name on both sides (`Acme — North`) stays whole. Text inside the first pair of backticks is the value; a comment after the closing backtick is ignored. In `.agent-tools/config.md`, write comments as HTML comments instead of em dashes.

## Laptop opt-out

`<git-dir>/agent-tools.local` with `- **Merge reviewer:** off` disables the reviewer on this laptop. The hook still starts the engine; the engine exits without a review. The same key in `PROJECT.md` or `config.md` is ignored, and the engine logs that. `--check` prints `opted_out`. The local file stays inside `.git` and is not added to the exclude block.

It refreshes a managed block in the repository's `info/exclude` (shared by linked worktrees):

```text
# >>> merge-reviewer
…linked paths…
.claude/settings.local.json
var/review-change.*
# <<< merge-reviewer
```

The refresh keeps every existing line whose path still exists in some worktree of the repository, so two worktrees linked for different hosts do not remove each other's entries.

Links are local. Do not commit them.

## Local tool layer

When **Agent layer** in `.agent-tools/config.md` is `committed`, the analysis prompt includes a bounded `## Local tool layer` block. The script builds it. It lists **Keep local** paths, symlinks named in the `# >>> cursor-template`, `# >>> merge-reviewer`, and `# >>> agent-layer` exclude blocks that currently point at cursor-template or merge-reviewer, and paths from `.agent-tools/vendored.lock` when that file exists. A lock file is either one path per line or a JSON list.

A commit that deletes a tracked file which is now one of those symlinks (or a file inside a linked directory) is not a loss on this laptop. The script records that path under `reviewed` with empty findings so it is not `not_reviewed`. The model must not review the deleted blob as the current text. A real deletion of a project file is still reviewed.

In committed mode, a committed file in the diff that tells a teammate to read or run a path that exists only as a local symlink is a `team-ref-to-local` finding (nit, or blocking when the instruction is mandatory). Keep-local and vendored paths are not that finding. When **Agent layer** is `local`, the block is omitted: the whole layer is local.

## Analysis file

The model writes `var/review-change.analysis.json`:

```json
{
  "verdict": "approve|approve-with-nits|request-changes",
  "summary": "one line",
  "reviewed": [{"path": "src/a.py", "findings": [{"anchor": "snippet", "severity": "blocking|nit", "category": "short", "note": "one sentence"}]}],
  "not_reviewed": [],
  "files": [{"path": "src/a.py", "replacements": [{"old": "unique", "new": "fix"}]}],
  "mr": {"title": "", "body": ""}
}
```

The script stamps git blobs onto `files` before apply. Apply skips a path whose blob or worktree changed. Note files under `docs/notes/` must stay append-only or the replacement is dropped. A proposed test-file edit is executed with **Test file command** when that key is set (`{path}` is replaced with the file). Otherwise the **Pre-review command** is used when it contains `{path}` or names a known test runner.

## Create merge request

The button runs `bin/post-commit-review --create-mr` in a terminal: `git push -u origin HEAD`, then the new-request URL. **Git host** in `.agent-tools/config.md` (or `REVIEW_CHANGE_GIT_HOST`) wins over `origin`. An empty host is parsed from `origin`. An SSH alias whose name starts with `github.com`, `gitlab.com`, or `bitbucket.org` followed by `-` or `.` is that host. When `~/.ssh/config` is readable, an exact `Host` token's `HostName` is used first. A host whose name contains `github` uses the compare URL. A host whose name contains `bitbucket` uses the pull-request URL. Any other host uses the GitLab-style `/-/merge_requests/new` path.
