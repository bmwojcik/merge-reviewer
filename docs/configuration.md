# Configuration

Tool settings live in `.agent-tools/config.md` in the project git root. Bullets look like `- **Key:** value`. Comments in that file are HTML comments (`<!-- … -->`) only. A missing key uses the default in the table. If `config.md` is missing, the same keys are read from `PROJECT.md` and the engine logs `merge-reviewer: settings still in PROJECT.md — run cursor-template install.sh --migrate-config`.

`PROJECT.md` holds project guidance. `## Quality bar` is copied into the analysis prompt. If that heading is absent, `## Review rules` is used. `## Agent rules` is extra context, bounded. A `Merge reviewer` on/off bullet in either committed file is ignored.

## `.agent-tools/config.md` keys

| Key | Default | Meaning |
| --- | --- | --- |
| Stack | `generic` | `magento2`, `shopify`, or `generic`. Informational. `Kind` is read only when `Stack` is absent. |
| Merge reviewer version | empty | Minimum tag. When this checkout is older, one warning is logged and the run continues. |
| Review language | English | `Polish` or `pl` selects the Polish canvas. Anything else is English. |
| Review canvas labels | built-in trio | Three labels separated by ` / `: approve, approve with notes, needs changes. |
| Pre-review command | empty | Shell command run before the analysis. Empty skips tests. A non-zero exit does not fail git. |
| Test file command | empty | Command template with `{path}` used to verify one proposed test-file edit. When empty, a `{path}` in **Pre-review command**, or a known test-runner name, is the fallback. |
| Test paths | `**/Test/**,**/tests/**,**/test/**` | Globs for the non-additive test-edit list in the prompt. |
| Base branch | `origin/main`, then `main`, `origin/master`, `master` | Diff base. `REVIEW_CHANGE_BASE` overrides it. |
| Git host | parsed from `origin` | Host used to build the new-merge-request URL. |
| Git project | parsed from `origin` | `group/repo` path on that host. |
| Review model | `grok-4.7` | Model for the one analysis. `--model` and `REVIEW_CHANGE_MODEL` win. |
| Agent layer | empty | `committed` adds the local-tool prompt block. `local` omits it. Read when present; the project template owns the key. |
| Keep local | empty | Comma-separated paths this repo commits itself. They are not tool symlinks. |
| Skip branches | empty | Extra branch names that are not reviewed, besides `main`, `master`, and the base branch's short name. |
| Git base suffixes | empty | `suffix=ref` pairs, for example `-sit=origin/sit, default=origin/main`. The longest matching branch suffix wins. |

When settings are still in `PROJECT.md`, a heading that starts with `## Default git base` supplies a base ref when **Base branch** is empty and the ref exists.

## Laptop opt-out

The reviewer runs when the project hook is installed. Turn it off on this laptop only by writing `<git-dir>/agent-tools.local` (usually `.git/agent-tools.local`):

```markdown
- **Merge reviewer:** off
```

That file is not committed. `install/link-project.sh --check` prints `opted_out: yes` or `opted_out: no`.

## Quality bar

```markdown
## Quality bar

- Shipping a payment total without a rounding step is blocking.
```

The body is copied until the next `## ` heading.

## Environment

| Variable | Effect |
| --- | --- |
| `MERGE_REVIEWER_HOME` | This repository. Also loaded from `~/.config/review-change/cursor.env`. |
| `CURSOR_TEMPLATE_HOME` | Used only as a fallback location: `$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer`. |
| `REVIEW_CHANGE_MODEL` | Overrides **Review model**. |
| `REVIEW_CHANGE_SCOPE` | `commit` (default) or `branch`. |
| `REVIEW_CHANGE_BASE` | Overrides **Base branch**. |
| `REVIEW_CHANGE_GIT_HOST` | Overrides **Git host**. |
| `REVIEW_CHANGE_PROJECT` | Overrides **Git project**. |
| `REVIEW_CHANGE` | `0` skips the run. |
| `REVIEW_CHANGE_SKIP` | `1` skips the run. |
| `CI`, `GITLAB_CI`, `GITHUB_ACTIONS` | Any one of these set skips the run. |
| `REVIEW_CHANGE_PHPUNIT` | `0` skips the pre-review command. |
| `REVIEW_CHANGE_RETRY` | `1` retries blobs whose previous pass failed. |
| `REVIEW_CHANGE_MAX_DIFF_KB` | Positive kilobyte cap. Unset means no cap and no size measurement. |
| `REVIEW_CHANGE_INLINE_DIFF_BYTES` | Inline diff budget (default 96000). Larger diffs tell the model to pull hunks. |
| `REVIEW_CHANGE_CANVAS` | Override the canvas file path. |
| `CURSOR_API_KEY` | Dashboard key (`crsr_…`) for `cursor-sdk`. Never commit it. |

## Commands

`bin/post-commit-review` (cwd = project git root):

| Flag | Effect |
| --- | --- |
| `--model NAME` | Model for this run. |
| `--create-mr` | Push `HEAD` and open the new-merge-request URL. |
| `--prime-triggers` | Rewrite the three `.cursor/review-change.*.request` files. |
| `--demo` | Render the canvas from `examples/demo/analysis.json` (or `./analysis.json`). No model call. |
| `--dry-run` | Parse `PROJECT.md`, build scope, context, and the full prompt. Print a summary and the prompt size. No model call and no canvas write. |
| `--version` | Print `VERSION`. |

`bin/apply-review-fixes` applies `var/review-change.fixes.json`. `--skip` records the anchors as dismissed and does not edit source files.

Runtime files stay in the project under `var/review-change.*`. `install/link-project.sh` adds them to `.git/info/exclude`.
