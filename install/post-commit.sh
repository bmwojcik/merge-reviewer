#!/bin/sh
# Git hook stub. The project template copies this into the project's hook.
# It never fails the commit. Search order:
#   1. $MERGE_REVIEWER_HOME/bin/post-commit-review
#   2. $(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer
#   3. <project>/tools/merge-reviewer
# One line and exit 0 when nothing is found.

repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

key_file="${XDG_CONFIG_HOME:-$HOME/.config}/review-change/cursor.env"
if [ -f "$key_file" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$key_file"
    set +a
fi

if [ -f "$repo/.githooks/is-local-dev.sh" ]; then
    if ! sh "$repo/.githooks/is-local-dev.sh"; then
        exit 0
    fi
fi

engine=""
if [ -n "${MERGE_REVIEWER_HOME:-}" ] && [ -f "$MERGE_REVIEWER_HOME/bin/post-commit-review" ]; then
    engine="$MERGE_REVIEWER_HOME/bin/post-commit-review"
elif [ -n "${CURSOR_TEMPLATE_HOME:-}" ] && [ -f "$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer/bin/post-commit-review" ]; then
    engine="$(dirname "$CURSOR_TEMPLATE_HOME")/merge-reviewer/bin/post-commit-review"
elif [ -f "$repo/tools/merge-reviewer/bin/post-commit-review" ]; then
    engine="$repo/tools/merge-reviewer/bin/post-commit-review"
fi

if [ -z "$engine" ]; then
    printf '%s\n' "merge-reviewer: engine not found (set MERGE_REVIEWER_HOME)"
    exit 0
fi

cd "$repo" || exit 0
python3 "$engine" || true
exit 0
