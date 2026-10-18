#!/bin/sh
# Print inlined project knowledge and the diff preview. cwd = git root.
# Usage: engine/review-context.sh [base] [scope]
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
exec python3 - "$root" "$@" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from engine.context import build_context
from engine.ledger import detect_base, scope_ref
from engine.paths import repo_root

repo = repo_root()
base = sys.argv[2] if len(sys.argv) > 2 else detect_base(repo)
scope = sys.argv[3] if len(sys.argv) > 3 else scope_ref(repo, base)
ctx = build_context(repo, base, scope)
sys.stdout.write(ctx.knowledge)
if ctx.knowledge and not ctx.knowledge.endswith("\n"):
    sys.stdout.write("\n")
sys.stdout.write(ctx.preview)
PY
