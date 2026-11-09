#!/bin/sh
# Link this engine into a project. Links are absolute and must not be committed.
# Usage: install/link-project.sh <project-root> [--host cursor|claude|both] [--check|--unlink|--force]
# Host: --host, else "Agent host" in <git-dir>/agent-tools.local, else in
# .agent-tools/config.md, else both. Narrowing the host removes the links this
# repo owns for the dropped host.
set -eu

usage() {
    printf '%s\n' "usage: install/link-project.sh <project-root> [--host cursor|claude|both] [--check|--unlink|--force]" >&2
    exit 2
}

REVIEWER=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
MODE=install
FORCE=0
ROOT=""
HOST=""

while [ "$#" -gt 0 ]; do
    arg=$1
    shift
    case "$arg" in
        --check) MODE=check ;;
        --unlink) MODE=unlink ;;
        --force) FORCE=1 ;;
        --host=*) HOST=${arg#--host=} ;;
        --host)
            [ "$#" -gt 0 ] || usage
            HOST=$1
            shift
            ;;
        --help|-h) usage ;;
        *)
            if [ -n "$ROOT" ]; then
                usage
            fi
            ROOT=$arg
            ;;
    esac
done
case "$HOST" in
    ""|cursor|claude|both) ;;
    *) usage ;;
esac

[ -n "$ROOT" ] || usage
[ -d "$ROOT" ] || {
    printf '%s\n' "missing project: $ROOT" >&2
    exit 1
}
ROOT=$(CDPATH= cd -- "$ROOT" && pwd -P)
LINKS="$REVIEWER/install/links.txt"
[ -f "$LINKS" ] || {
    printf '%s\n' "missing links.txt" >&2
    exit 1
}

abs_path() {
    target=$1
    if [ -d "$target" ]; then
        (CDPATH= cd -- "$target" && pwd -P)
        return 0
    fi
    dir=$(dirname "$target")
    base=$(basename "$target")
    printf '%s/%s\n' "$(CDPATH= cd -- "$dir" && pwd -P)" "$base"
}

project_stack() {
    file=$ROOT/PROJECT.md
    if [ ! -f "$file" ]; then
        printf '%s\n' generic
        return 0
    fi
    python3 - "$file" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
stack = ""
kind = ""
for line in text.splitlines():
    match = re.match(r"^\s*-\s+\*\*([^*]+)\*\*:?\s*(.*)$", line)
    if not match:
        continue
    key = re.sub(r"\s+", " ", match.group(1).strip().lower()).rstrip(":")
    value = match.group(2).strip().strip("`").split(" — ")[0].strip().lower()
    if key == "stack":
        stack = value
    elif key == "kind" and not kind:
        kind = value
raw = stack or kind or "generic"
if raw in {"magento", "magento2"}:
    print("magento2")
elif raw == "shopify":
    print("shopify")
elif raw == "generic":
    print("generic")
else:
    print("generic")
PY
}

resolve_host() {
    python3 - "$REVIEWER" "$ROOT" 2>/dev/null <<'PY' || printf '%s\n' both
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from engine.project import load_review_project

print(load_review_project(Path(sys.argv[2])).agent_host)
PY
}

STACK=$(project_stack)
if [ -z "$HOST" ]; then
    HOST=$(resolve_host)
fi
case "$HOST" in
    cursor|claude|both) ;;
    *) HOST=both ;;
esac

host_has() {
    [ "$HOST" = both ] || [ "$HOST" = "$1" ]
}

# True when dest is a symlink this repo created (absolute target inside this repo).
owned_link() {
    [ -L "$1" ] || return 1
    target=$(readlink "$1" 2>/dev/null || true)
    case "$target" in
        "$REVIEWER"/*) return 0 ;;
    esac
    return 1
}

PROBLEMS=0
DESTS=""

note_dest() {
    case " $DESTS " in
        *" $1 "*) ;;
        *) DESTS="$DESTS $1" ;;
    esac
}

same_link() {
    dest=$1
    src=$2
    if [ ! -L "$dest" ]; then
        return 1
    fi
    current=$(readlink -f "$dest" 2>/dev/null || readlink "$dest")
    wanted=$(readlink -f "$src" 2>/dev/null || printf '%s\n' "$src")
    [ "$current" = "$wanted" ]
}

link_one() {
    src_rel=$1
    dest_rel=$2
    src=$REVIEWER/$src_rel
    dest=$ROOT/$dest_rel
    if [ ! -e "$src" ]; then
        printf '%s %s\n' "missing" "$dest_rel"
        PROBLEMS=$((PROBLEMS + 1))
        return 0
    fi
    src_abs=$(abs_path "$src")
    if [ "$MODE" = "unlink" ]; then
        if owned_link "$dest"; then
            rm -f "$dest"
            printf '%s %s\n' "removed" "$dest_rel"
        fi
        return 0
    fi
    if [ -L "$dest" ] && same_link "$dest" "$src_abs"; then
        printf '%s %s\n' "ok" "$dest_rel"
        note_dest "$dest_rel"
        return 0
    fi
    if owned_link "$dest"; then
        # Ours but pointing elsewhere in this repo (an older layout): repoint it.
        if [ "$MODE" = "check" ]; then
            printf '%s %s\n' "stale" "$dest_rel"
            PROBLEMS=$((PROBLEMS + 1))
            return 0
        fi
        rm -f "$dest"
    fi
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        if [ "$dest_rel" = ".cursor/hooks.json" ] && [ ! -L "$dest" ] && [ "$FORCE" -eq 0 ]; then
            printf '%s %s\n' "ok" "$dest_rel"
            if [ "$MODE" = "install" ]; then
                printf '%s\n' "merge-reviewer: kept existing .cursor/hooks.json (merge hooks by hand)" >&2
            fi
            return 0
        fi
        if [ "$FORCE" -eq 0 ]; then
            printf '%s %s\n' "conflict" "$dest_rel"
            PROBLEMS=$((PROBLEMS + 1))
            return 0
        fi
        rm -rf "$dest"
    fi
    if [ "$MODE" = "check" ]; then
        printf '%s %s\n' "missing" "$dest_rel"
        PROBLEMS=$((PROBLEMS + 1))
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    ln -s "$src_abs" "$dest"
    printf '%s %s\n' "ok" "$dest_rel"
    note_dest "$dest_rel"
}

# A link of a host that is no longer wanted: remove it when this repo owns it.
drop_stale() {
    dest_rel=$1
    dest=$ROOT/$dest_rel
    owned_link "$dest" || return 0
    if [ "$MODE" = "check" ]; then
        printf '%s %s\n' "stale" "$dest_rel"
        PROBLEMS=$((PROBLEMS + 1))
        return 0
    fi
    rm -f "$dest"
    printf '%s %s\n' "removed" "$dest_rel"
}

while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ""|\#*) continue ;;
    esac
    filter=""
    rest=$line
    case "$line" in
        \[*\ *)
            filter=${line%%]*}
            filter=${filter#\[}
            rest=${line#*] }
            ;;
    esac
    src_rel=${rest%% *}
    dest_rel=${rest#* }
    case "$filter" in
        host:*)
            if [ "$MODE" != "unlink" ] && ! host_has "${filter#host:}"; then
                drop_stale "$dest_rel"
                continue
            fi
            ;;
        "") ;;
        *)
            if [ "$filter" != "$STACK" ]; then
                continue
            fi
            ;;
    esac
    link_one "$src_rel" "$dest_rel"
done < "$LINKS"

write_trigger() {
    rel=$1
    kind=$2
    path=$ROOT/$rel
    if [ "$MODE" = "check" ]; then
        if [ -f "$path" ] && [ ! -L "$path" ]; then
            printf '%s %s\n' "ok" "$rel"
        else
            printf '%s %s\n' "missing" "$rel"
            PROBLEMS=$((PROBLEMS + 1))
        fi
        return 0
    fi
    if [ "$MODE" = "unlink" ]; then
        if [ -f "$path" ] && [ ! -L "$path" ]; then
            first=$(head -n 1 "$path" 2>/dev/null || true)
            if [ "$first" = "canvas-trigger" ]; then
                rm -f "$path"
                printf '%s %s\n' "removed" "$rel"
            fi
        fi
        return 0
    fi
    if [ -e "$path" ] && [ ! -L "$path" ]; then
        first=$(head -n 1 "$path" 2>/dev/null || true)
        if [ "$first" != "canvas-trigger" ] && [ "$FORCE" -eq 0 ]; then
            printf '%s %s\n' "conflict" "$rel"
            PROBLEMS=$((PROBLEMS + 1))
            return 0
        fi
    fi
    if [ -L "$path" ]; then
        rm -f "$path"
    fi
    mkdir -p "$(dirname "$path")"
    printf 'canvas-trigger\n\n%s\n' "$kind" > "$path"
    printf '%s %s\n' "ok" "$rel"
}

# Cursor-only trigger files. Without the cursor host they are removed (never created).
cursor_triggers() {
    write_trigger ".cursor/review-change.apply.request" "apply"
    write_trigger ".cursor/review-change.skip.request" "skip"
    write_trigger ".cursor/review-change.mr.request" "mr"
}

if host_has cursor; then
    cursor_triggers
elif [ "$MODE" = "install" ]; then
    MODE=unlink
    cursor_triggers
    MODE=install
fi

refresh_exclude() {
    if [ "$MODE" = "unlink" ]; then
        python3 "$REVIEWER/install/exclude-block.py" "$ROOT" unlink
        return 0
    fi
    # Union with lines other worktrees of this repo still need (shared info/exclude).
    set -- $DESTS
    if host_has cursor; then
        set -- "$@" \
            .cursor/review-change.apply.request \
            .cursor/review-change.skip.request \
            .cursor/review-change.mr.request
    fi
    if host_has claude; then
        set -- "$@" .claude/settings.local.json
    fi
    python3 "$REVIEWER/install/exclude-block.py" "$ROOT" install "$@" "var/review-change.*"
}

prune_dirs() {
    for dir in "$@"; do
        if [ -d "$dir" ]; then
            rmdir "$dir" 2>/dev/null || true
        fi
    done
}

prune_cursor() {
    prune_dirs "$ROOT/.cursor/skills" "$ROOT/.cursor/agents" "$ROOT/.cursor/commands" \
        "$ROOT/.cursor/extensions" "$ROOT/.cursor/hooks" "$ROOT/.cursor"
}

prune_claude() {
    prune_dirs "$ROOT/.claude/skills" "$ROOT/.claude/agents" "$ROOT/.claude"
}

prune_empty() {
    prune_cursor
    prune_claude
}

if [ "$MODE" = "unlink" ]; then
    prune_empty
fi

workspace_cursor_dirs() {
    python3 - "$REVIEWER" "$ROOT" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from engine.host import workspace_trigger_dirs

for folder in workspace_trigger_dirs(Path(sys.argv[2])):
    print(folder)
PY
}

refresh_exclude_root() {
    git_root=$1
    shift
    if [ "$MODE" = "unlink" ]; then
        python3 "$REVIEWER/install/exclude-block.py" "$git_root" unlink
    else
        python3 "$REVIEWER/install/exclude-block.py" "$git_root" install "$@"
    fi
}

rel_to() {
    python3 -c 'import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$1" "$2"
}

sync_workspace_triggers() {
    list=$(workspace_cursor_dirs) || list=""
    [ -n "$list" ] || return 0
    while IFS= read -r dir; do
        [ -n "$dir" ] || continue
        for spec in "review-change.apply.request apply" "review-change.skip.request skip" "review-change.mr.request mr"; do
            name=${spec%% *}
            kind=${spec#* }
            write_trigger_file "$dir/$name" "$kind" "$dir/$name"
        done
        if [ "$MODE" = "check" ] || [ "$SYNC_EXCLUDE" -eq 0 ]; then
            continue
        fi
        top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)
        [ -n "$top" ] || continue
        apply_rel=$(rel_to "$dir/review-change.apply.request" "$top")
        skip_rel=$(rel_to "$dir/review-change.skip.request" "$top")
        mr_rel=$(rel_to "$dir/review-change.mr.request" "$top")
        if [ "$top" = "$ROOT" ]; then
            note_dest "$apply_rel"
            note_dest "$skip_rel"
            note_dest "$mr_rel"
        else
            refresh_exclude_root "$top" "$apply_rel" "$skip_rel" "$mr_rel"
        fi
    done <<EOF
$list
EOF
}

write_trigger_file() {
    path=$1
    kind=$2
    label=$3
    if [ "$MODE" = "check" ]; then
        if [ -f "$path" ] && [ ! -L "$path" ]; then
            printf '%s %s\n' "ok" "$label"
        else
            printf '%s %s\n' "missing" "$label"
            PROBLEMS=$((PROBLEMS + 1))
        fi
        return 0
    fi
    if [ "$MODE" = "unlink" ]; then
        if [ -f "$path" ] && [ ! -L "$path" ]; then
            first=$(head -n 1 "$path" 2>/dev/null || true)
            if [ "$first" = "canvas-trigger" ]; then
                rm -f "$path"
                printf '%s %s\n' "removed" "$label"
            fi
        fi
        return 0
    fi
    if [ -e "$path" ] && [ ! -L "$path" ]; then
        first=$(head -n 1 "$path" 2>/dev/null || true)
        if [ "$first" != "canvas-trigger" ] && [ "$FORCE" -eq 0 ]; then
            printf '%s %s\n' "conflict" "$label"
            PROBLEMS=$((PROBLEMS + 1))
            return 0
        fi
    fi
    if [ -L "$path" ]; then
        rm -f "$path"
    fi
    mkdir -p "$(dirname "$path")"
    printf 'canvas-trigger\n\n%s\n' "$kind" > "$path"
    printf '%s %s\n' "ok" "$label"
}

SYNC_EXCLUDE=1
if host_has cursor || [ "$MODE" = "unlink" ]; then
    sync_workspace_triggers
elif [ "$MODE" = "install" ]; then
    # Host without Cursor: drop trigger files mirrored beside a workspace file.
    MODE=unlink
    SYNC_EXCLUDE=0
    sync_workspace_triggers
    MODE=install
    SYNC_EXCLUDE=1
fi

# Claude Code hooks live in .claude/settings.local.json (laptop-only). The tagged
# entries are merged in or removed; settings.json is never touched.
claude_hooks() {
    helper=$REVIEWER/install/claude-settings.py
    case "$MODE" in
        unlink)
            python3 "$helper" "$ROOT" remove "$REVIEWER" || true
            ;;
        check)
            if host_has claude; then
                python3 "$helper" "$ROOT" check "$REVIEWER" || PROBLEMS=$((PROBLEMS + 1))
            elif python3 "$helper" "$ROOT" check "$REVIEWER" >/dev/null 2>&1; then
                printf '%s %s\n' "stale" ".claude/settings.local.json"
                PROBLEMS=$((PROBLEMS + 1))
            fi
            ;;
        *)
            if host_has claude; then
                python3 "$helper" "$ROOT" install "$REVIEWER" || PROBLEMS=$((PROBLEMS + 1))
            else
                python3 "$helper" "$ROOT" remove "$REVIEWER" || true
            fi
            ;;
    esac
}

claude_hooks

if [ "$MODE" = "install" ]; then
    # Only the dropped host's directories, and only when empty.
    host_has cursor || prune_cursor
    host_has claude || prune_claude
elif [ "$MODE" = "unlink" ]; then
    prune_empty
fi
if [ "$MODE" != "check" ]; then
    refresh_exclude
fi

if [ "$MODE" = "check" ]; then
    printf '%s %s\n' "host:" "$HOST"
    python3 - "$REVIEWER" "$ROOT" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from engine.project import configured_review_host, is_opted_out, load_review_project

root = Path(sys.argv[2])
print("review_host:", configured_review_host(load_review_project(root)))
print("opted_out:", "yes" if is_opted_out(root) else "no")
PY
fi

if [ "$PROBLEMS" -ne 0 ]; then
    exit 1
fi
exit 0
