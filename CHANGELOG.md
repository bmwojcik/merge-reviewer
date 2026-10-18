# Changelog

## 0.3.0

- When Agent layer is committed, the analysis prompt includes the local tool symlinks, Keep local paths, and vendored paths. A tracked file replaced by one of those symlinks is not treated as lost, and the script marks that deletion reviewed with no notes.
- A committed reference to a path that exists only as a local symlink is a `team-ref-to-local` finding. In local mode that block is omitted.

## 0.2.0

- Tool settings move to `.agent-tools/config.md`. If that file is missing, the old `PROJECT.md` keys are used and one migration line is logged.
- Blocking rules come from `PROJECT.md` `## Quality bar`, with `## Review rules` as the fallback. `## Agent rules` is bounded extra context.
- On/off is per laptop in `<git-dir>/agent-tools.local`. A `Merge reviewer` bullet in a committed file is ignored.

## 0.1.1

- Normalise SSH host aliases (`github.com-…`, `gitlab.com-…`, `bitbucket.org-…`) and a readable `~/.ssh/config` `HostName` when building the merge-request URL. A set **Git host** still wins over `origin`.
- Treat em-dash and en-dash comments, `none`, `leave empty…`, `(empty)`, and `REPLACE_ME` as empty `PROJECT.md` values. Keep an em dash that is part of a short name.
- Mirror Apply, Skip, and Create-merge-request triggers into `.cursor/` beside the nearest parent `*.code-workspace` (up to four parents) and watch that directory as well as the repository `.cursor`.
- Optional **Test file command** (`{path}`) verifies one proposed test-file edit. The pre-review command remains the fallback.
- `--dry-run` prints `git_host`, `new_mr_url_sample`, and `trigger_dirs`.

## 0.1.0

First release. One analysis per commit, project settings from `PROJECT.md`, a script-rendered canvas, and symlink install so projects do not copy the engine.
