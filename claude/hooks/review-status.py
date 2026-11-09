#!/usr/bin/env python3
"""Claude Code hook: announce a finished merge-reviewer run once per session.

Events: SessionStart, UserPromptSubmit (announce a finished or failed review that
this session has not heard about) and PostToolUse on Bash (after `git commit`,
say that the review started in the background). Reads
`var/review-change.status.json`, written by the engine for the Claude host.
Prints `hookSpecificOutput.additionalContext` JSON. Always exits 0 and prints
nothing on any problem. No network, no secrets.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

STATUS = "review-change.status.json"
ANNOUNCED = "review-change.announced.json"
FRESH_SECONDS = 12 * 3600
START_WINDOW = 180
MAX_SESSIONS = 20
COMMIT_RE = re.compile(r"(^|[;&|(\s])git\s+([^;&|\n]*\s)?commit(\s|$)")


def find_var(start: str) -> Path | None:
    folder = Path(start or ".").resolve()
    for _ in range(10):
        candidate = folder / "var"
        if (candidate / STATUS).is_file():
            return candidate
        if folder.parent == folder:
            return None
        folder = folder.parent
    return None


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mark(var: Path, session: str, key: str) -> None:
    path = var / ANNOUNCED
    data = load(path)
    seen = [str(item) for item in data.get(session, []) if isinstance(item, str)]
    if key not in seen:
        seen.append(key)
    data[session] = seen[-10:]
    if len(data) > MAX_SESSIONS:
        for stale in list(data)[: len(data) - MAX_SESSIONS]:
            data.pop(stale, None)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def emit(event: str, text: str) -> None:
    sys.stdout.write(
        json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text[:9000]}})
        + "\n"
    )


def finished_text(status: dict) -> str:
    sha = status.get("sha", "?")
    head = f"merge-reviewer finished the review of commit {sha}"
    if status.get("branch"):
        head += f" on {status['branch']}"
    if status.get("subject"):
        head += f" ({status['subject']})"
    lines = [head + "."]
    if status.get("state") == "failed":
        lines.append(
            f"The run did not complete: {status.get('label') or 'analysis failed'}. "
            f"Details are in {status.get('log', 'var/review-change.log')}."
        )
    else:
        lines.append(
            f"Verdict: {status.get('label') or status.get('verdict') or 'unknown'} "
            f"({status.get('blocking', 0)} blocking, {status.get('nits', 0)} notes, "
            f"{status.get('fix_files', 0)} file(s) with proposed fixes)."
        )
        if status.get("summary"):
            lines.append(f"Summary: {status['summary']}")
        for item in (status.get("top_blocking") or [])[:5]:
            lines.append(f"- blocking: {item}")
    lines.append(f"Report: {status.get('html', '')} and {status.get('md', '')}.")
    lines.append(
        "Tell the user this in one or two sentences, in their language, and point to the report. "
        "If there are proposed fixes, mention the skills /apply-review-fixes (apply) and "
        "/skip-review-fixes (dismiss). Do not apply, commit, or push unless the user asks."
    )
    return "\n".join(lines)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        return
    event = str(data.get("hook_event_name") or "")
    if event not in {"SessionStart", "UserPromptSubmit", "PostToolUse"}:
        return
    var = find_var(str(data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."))
    if var is None:
        return
    status = load(var / STATUS)
    if status.get("host") != "claude":
        return
    session = str(data.get("session_id") or "nosession")
    seen = set(load(var / ANNOUNCED).get(session, []))
    now = time.time()
    state = status.get("state")
    run_id = str(status.get("id") or "")
    if not run_id:
        return
    if event == "PostToolUse":
        if data.get("tool_name") != "Bash":
            return
        command = str((data.get("tool_input") or {}).get("command") or "")
        if not COMMIT_RE.search(command):
            return
        key = "start:" + run_id
        age = now - float(status.get("updated_epoch") or 0)
        if state != "running" or age > START_WINDOW or key in seen:
            return
        mark(var, session, key)
        emit(
            event,
            f"merge-reviewer started a background review of commit {status.get('sha', '?')} "
            f"(model {status.get('model', 'sonnet')}). It takes a few minutes; do not wait for it "
            "and do not review the diff yourself. The result will be announced in this session; "
            f"progress page: {status.get('html', 'var/review-change.html')}.",
        )
        return
    if state not in {"done", "failed"} or run_id in seen:
        return
    if now - float(status.get("finished_epoch") or 0) > FRESH_SECONDS:
        return
    mark(var, session, run_id)
    emit(event, finished_text(status))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass
    sys.exit(0)
