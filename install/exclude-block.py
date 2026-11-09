#!/usr/bin/env python3
"""Maintain the managed `# >>> merge-reviewer` block in a repository's info/exclude.

usage: exclude-block.py <git-root> install|unlink [line ...]

The exclude file is the shared one (`git rev-parse --git-path info/exclude`), so
linked worktrees of one repository write to the same block. Install keeps every
existing line whose path still exists in some worktree and adds the wanted lines.
Unlink keeps only lines a sibling worktree still needs. Idempotent. Not a git
repository: does nothing.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

START = "# >>> merge-reviewer"
END = "# <<< merge-reviewer"


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def worktree_roots(root: Path) -> list[Path]:
    roots = [root.resolve()]
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[9:]).resolve()
            if path not in roots:
                roots.append(path)
    return roots


def exists_in(rel: str, roots: list[Path]) -> bool:
    for base in roots:
        target = str(base / rel)
        if "*" in rel:
            if glob.glob(target):
                return True
        elif os.path.lexists(target):
            return True
    return False


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[2] not in {"install", "unlink"}:
        print("usage: exclude-block.py <git-root> install|unlink [line ...]", file=sys.stderr)
        return 2
    root, mode, wanted = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3:]
    raw = git(root, "rev-parse", "--git-path", "info/exclude")
    if not raw:
        return 0
    exclude = Path(raw)
    if not exclude.is_absolute():
        exclude = root / exclude
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    rest: list[str] = []
    old: list[str] = []
    inside = False
    for line in text.splitlines():
        if line == START:
            inside = True
        elif line == END:
            inside = False
        elif inside:
            old.append(line)
        else:
            rest.append(line)
    if not old and mode == "unlink":
        return 0
    roots = worktree_roots(root)
    check_roots = roots if mode == "install" else [r for r in roots if r != root]
    if mode == "unlink":
        wanted = []
    final = list(dict.fromkeys(wanted))
    for line in old:
        if line and line not in final and exists_in(line, check_roots):
            final.append(line)
    out = list(rest)
    while out and not out[-1].strip():
        out.pop()
    if final:
        out += [START, *final, END]
    new_text = ("\n".join(out) + "\n") if out else ""
    if new_text != text:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(new_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
