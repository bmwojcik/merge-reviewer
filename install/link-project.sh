#!/bin/sh
# Link this engine into a project. Links are absolute and must not be committed.
# Usage: install/link-project.sh <project-root> [--check|--unlink|--force]
set -eu

usage() {
    printf '%s\n' "usage: install/link-project.sh <project-root> [--check|--unlink|--force]" >&2
    exit 2
}

REVIEWER=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
MODE=install
FORCE=0
ROOT=""

for arg in "$@"; do
    case "$arg" in
        --check) MODE=check ;;
        --unlink) MODE=unlink ;;
        --force) FORCE=1 ;;
        --help|-h) usage ;;
        *)
            if [ -n "$ROOT" ]; then
                usage
            fi
            ROOT=$arg
            ;;
    esac
done

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

STACK=$(project_stack)
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
        if [ -L "$dest" ] && same_link "$dest" "$src_abs"; then
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

claude_for() {
    dest_rel=$1
    case "$dest_rel" in
        .cursor/skills/*)
            printf '%s\n' ".claude/skills/${dest_rel#.cursor/skills/}"
            ;;
    esac
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
    if [ -n "$filter" ] && [ "$filter" != "$STACK" ]; then
        continue
    fi
    src_rel=${rest%% *}
    dest_rel=${rest#* }
    link_one "$src_rel" "$dest_rel"
    extra=$(claude_for "$dest_rel" || true)
    if [ -n "$extra" ]; then
        link_one "$src_rel" "$extra"
    fi
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

write_trigger ".cursor/review-change.apply.request" "apply"
write_trigger ".cursor/review-change.skip.request" "skip"
write_trigger ".cursor/review-change.mr.request" "mr"

refresh_exclude() {
    exclude=$ROOT/.git/info/exclude
    if [ ! -d "$ROOT/.git" ]; then
        return 0
    fi
    mkdir -p "$ROOT/.git/info"
    [ -f "$exclude" ] || : > "$exclude"
    tmp=$(mktemp)
    awk '
        $0 == "# >>> merge-reviewer" { skip = 1; next }
        $0 == "# <<< merge-reviewer" { skip = 0; next }
        skip == 0 { print }
    ' "$exclude" > "$tmp"
    if [ "$MODE" != "unlink" ]; then
        {
            printf '%s\n' "# >>> merge-reviewer"
            for rel in $DESTS \
                .cursor/review-change.apply.request \
                .cursor/review-change.skip.request \
                .cursor/review-change.mr.request
            do
                printf '%s\n' "$rel"
            done
            printf '%s\n' "var/review-change.*"
            printf '%s\n' "# <<< merge-reviewer"
        } >> "$tmp"
    fi
    mv "$tmp" "$exclude"
}

if [ "$MODE" = "check" ]; then
    refresh_exclude() { :; }
fi

refresh_exclude

prune_empty() {
    for dir in \
        "$ROOT/.cursor/skills" \
        "$ROOT/.cursor/agents" \
        "$ROOT/.cursor/commands" \
        "$ROOT/.cursor/extensions" \
        "$ROOT/.cursor/hooks" \
        "$ROOT/.cursor" \
        "$ROOT/.claude/skills" \
        "$ROOT/.claude"
    do
        if [ -d "$dir" ]; then
            rmdir "$dir" 2>/dev/null || true
        fi
    done
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
    exclude=$git_root/.git/info/exclude
    if [ ! -d "$git_root/.git" ]; then
        return 0
    fi
    mkdir -p "$git_root/.git/info"
    [ -f "$exclude" ] || : > "$exclude"
    tmp=$(mktemp)
    awk '
        $0 == "# >>> merge-reviewer" { skip = 1; next }
        $0 == "# <<< merge-reviewer" { skip = 0; next }
        skip == 0 { print }
    ' "$exclude" > "$tmp"
    if [ "$MODE" != "unlink" ]; then
        {
            printf '%s\n' "# >>> merge-reviewer"
            for rel in "$@"; do
                printf '%s\n' "$rel"
            done
            printf '%s\n' "# <<< merge-reviewer"
        } >> "$tmp"
    fi
    mv "$tmp" "$exclude"
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
        if [ "$MODE" = "check" ]; then
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

sync_workspace_triggers
if [ "$MODE" != "check" ]; then
    refresh_exclude
fi

if [ "$MODE" = "check" ]; then
    python3 - "$REVIEWER" "$ROOT" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from engine.project import is_opted_out

print("opted_out:", "yes" if is_opted_out(Path(sys.argv[2])) else "no")
PY
fi

if [ "$PROBLEMS" -ne 0 ]; then
    exit 1
fi
exit 0
