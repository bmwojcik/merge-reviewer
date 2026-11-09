#!/usr/bin/env python3
"""Merge or remove the merge-reviewer hooks in <project>/.claude/settings.local.json.

usage: claude-settings.py <project> install|remove|check <reviewer-home>

Only entries whose command carries the marker below are ours. Every other key,
permission, and hook is preserved as parsed (key order and indent kept). The
committed .claude/settings.json is never read or written. Prints one word:
ok, missing, stale, removed, conflict (invalid JSON; the file is left alone).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import sys
from pathlib import Path

MARK = "# merge-reviewer-hook"
REL = ".claude/settings.local.json"
EVENTS = (("SessionStart", None), ("UserPromptSubmit", None), ("PostToolUse", "Bash"))


def command_for(home: str) -> str:
    script = shlex.quote(str(Path(home) / "claude" / "hooks" / "review-status.py"))
    return f'f={script}; [ -f "$f" ] && python3 "$f" || true  {MARK}'


def is_ours(handler: object) -> bool:
    return isinstance(handler, dict) and MARK in str(handler.get("command") or "")


def strip_ours(data: dict) -> None:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                group["hooks"] = [h for h in group["hooks"] if not is_ours(h)]
                if not group["hooks"]:
                    continue
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del data["hooks"]


def add_ours(data: dict, home: str) -> None:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks is not an object")
    handler = {"type": "command", "command": command_for(home), "timeout": 5}
    for event, matcher in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event} is not a list")
        group: dict = {"hooks": [copy.deepcopy(handler)]}
        if matcher:
            group = {"matcher": matcher, **group}
        groups.append(group)


def has_ours(data: dict, home: str) -> tuple[bool, bool]:
    """(any ours, all three events carry the current command)."""
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    wanted = command_for(home)
    present = 0
    anyone = False
    for event, _matcher in EVENTS:
        found = [
            h
            for g in hooks.get(event, []) if isinstance(g, dict)
            for h in g.get("hooks", []) if is_ours(h)
        ]
        anyone = anyone or bool(found)
        if any(h.get("command") == wanted for h in found):
            present += 1
    return anyone, present == len(EVENTS)


def indent_of(text: str) -> str | int:
    match = re.search(r"^([ \t]+)\S", text, re.M)
    if not match:
        return 2
    lead = match.group(1)
    return "\t" if lead.startswith("\t") else len(lead)


def write(path: Path, data: dict, original: str | None) -> None:
    indent = indent_of(original) if original else 2
    escaped = bool(original) and original.isascii() and re.search(r"\\u[0-9a-fA-F]{4}", original) is not None
    text = json.dumps(data, indent=indent, ensure_ascii=escaped)
    if original is None or original.endswith("\n"):
        text += "\n"
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".mr-tmp")
    tmp.write_text(text, encoding="utf-8")
    if target.exists():
        os.chmod(tmp, target.stat().st_mode & 0o777)
    os.replace(tmp, target)


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[2] not in {"install", "remove", "check"}:
        print("usage: claude-settings.py <project> install|remove|check <reviewer-home>", file=sys.stderr)
        return 2
    project, action, home = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    path = project / REL
    original: str | None = None
    data: dict = {}
    if path.exists():
        try:
            original = path.read_text(encoding="utf-8")
            loaded = json.loads(original) if original.strip() else {}
        except (OSError, ValueError):
            print(f"conflict {REL}")
            return 1
        if not isinstance(loaded, dict):
            print(f"conflict {REL}")
            return 1
        data = loaded
    if action == "check":
        anyone, full = has_ours(data, home)
        print(f"{'ok' if full else 'missing'} {REL}")
        return 0 if full else 1
    updated = copy.deepcopy(data)
    strip_ours(updated)
    if action == "remove":
        if not anyone_present(data):
            return 0
        if updated:
            write(path, updated, original)
            print(f"removed {REL} (hooks only)")
        else:
            path.unlink()
            print(f"removed {REL}")
        return 0
    try:
        add_ours(updated, home)
    except ValueError:
        print(f"conflict {REL}")
        return 1
    if updated != data or original is None:
        write(path, updated, original)
    print(f"ok {REL}")
    return 0


def anyone_present(data: dict) -> bool:
    return has_ours(data, "")[0]


if __name__ == "__main__":
    sys.exit(main())
