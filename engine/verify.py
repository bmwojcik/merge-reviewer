"""Check proposed fixes before they reach the canvas.

Note files under docs/notes/ must stay append-only. A test file is executed
only when the pre-review command can take that one path. Other files are left
as proposed.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from engine.context import is_test_path
from engine.ledger import analysis_file
from engine.paths import git_ok
from engine.pre_review import command_argv
from engine.project import load_review_project

_RUNNERS = {
    "phpunit",
    "phpunit.phar",
    "pytest",
    "py.test",
    "jest",
    "mocha",
    "rspec",
    "review-unit-tests.sh",
}
UNIT_TIMEOUT = 120


def _read_text(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _is_notes(path: str) -> bool:
    rel = path.replace("\\", "/")
    return rel.startswith("docs/notes/") and rel.endswith(".md")


def _is_append(old: bytes, new: bytes) -> bool:
    base = old.rstrip(b"\r\n")
    if not base:
        return True
    return new == base or new.startswith(base + b"\n") or new.startswith(base + b"\r\n")


def _apply_replacements(text: str, replacements: list) -> str | None:
    for repl in replacements:
        if not isinstance(repl, dict):
            return None
        old = repl.get("old")
        new = repl.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old == new:
            continue
        count = text.count(old)
        if count == 0 and new in text:
            continue
        if count != 1:
            return None
        text = text.replace(old, new, 1)
    return text


def _add_finding(data: dict, path: str, note: str) -> None:
    reviewed = data.get("reviewed")
    if not isinstance(reviewed, list):
        reviewed = []
        data["reviewed"] = reviewed
    finding = {"anchor": note, "severity": "nit", "category": "fix-verify", "note": note}
    for entry in reviewed:
        if isinstance(entry, dict) and str(entry.get("path") or "") == path:
            findings = entry.get("findings")
            if not isinstance(findings, list):
                findings = []
                entry["findings"] = findings
            findings.append(finding)
            return
    reviewed.append({"path": path, "findings": [finding]})


def _drop(data: dict, item: dict, path: str, reason: str, emit: Callable[[str], None]) -> None:
    note = reason if reason.startswith("fix dropped:") else f"fix dropped: {reason}"
    item["replacements"] = []
    item["verify"] = "dropped"
    item["verify_note"] = note
    _add_finding(data, path, note)
    emit(f"fix verify {path}: dropped ({reason})")


def _unverified(item: dict, emit: Callable[[str], None], path: str, note: str) -> None:
    item["verify"] = "unverified"
    item["verify_note"] = note
    emit(f"fix verify {path}: unverified")


def _git_bytes(repo: Path, path: str) -> bytes:
    if not git_ok(repo, "cat-file", "-e", f"HEAD:{path}"):
        return b""
    try:
        return subprocess.check_output(
            ["git", "show", f"HEAD:{path}"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return b""


def _can_target(command: str) -> bool:
    if "{path}" in command:
        return True
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    return Path(parts[0]).name in _RUNNERS or Path(parts[-1]).name in _RUNNERS


def _verify_notes(repo: Path, data: dict, item: dict, path: str, replacements: list, emit: Callable[[str], None], drop_note: str) -> None:
    target = repo / path
    try:
        current = _read_text(target)
    except (OSError, UnicodeError):
        _drop(data, item, path, "missing file", emit)
        return
    proposed = _apply_replacements(current, replacements)
    if proposed is None:
        _unverified(item, emit, path, "replacement is not unique")
        return
    old = _git_bytes(repo, path)
    if not _is_append(old, proposed.encode("utf-8")):
        _drop(data, item, path, drop_note, emit)
        return
    emit(f"fix verify {path}: ok")


def _verify_test(
    repo: Path,
    data: dict,
    item: dict,
    path: str,
    replacements: list,
    command: str,
    emit: Callable[[str], None],
    unverified_note: str,
    forced: bool = False,
) -> None:
    target = repo / path
    try:
        original = target.read_bytes()
        current = _read_text(target)
    except (OSError, UnicodeError):
        _drop(data, item, path, "missing file", emit)
        return
    proposed = _apply_replacements(current, replacements)
    if proposed is None or not command or (not forced and not _can_target(command)):
        _unverified(item, emit, path, unverified_note)
        return
    concrete = command.replace("{path}", path)
    if "{path}" not in command:
        concrete = f"{command} {path}"
    cwd, args = command_argv(repo, concrete)
    try:
        target.write_text(proposed, encoding="utf-8")
    except OSError:
        _unverified(item, emit, path, unverified_note)
        return
    try:
        try:
            completed = subprocess.run(
                args,
                cwd=str(cwd),
                check=False,
                capture_output=True,
                text=True,
                timeout=UNIT_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            _unverified(item, emit, path, unverified_note)
            return
    finally:
        try:
            target.write_bytes(original)
        except OSError:
            pass
    if completed.returncode == 0:
        emit(f"fix verify {path}: ok")
        return
    _drop(data, item, path, f"breaks {path} (exit {completed.returncode})", emit)


def verify_proposed_fixes(repo: Path, log: Callable[[str], None] | None = None) -> dict:
    loaded = analysis_file(repo)
    raw = loaded.read_text(encoding="utf-8") if loaded.is_file() else ""
    import json

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    project = load_review_project(repo)
    files = data.get("files")
    if not isinstance(files, list):
        files = []
        data["files"] = files
    if project.language == "pl":
        unverified_note = "niezweryfikowane: brak polecenia testu dla pliku"
        drop_note = "edycja istniejącego wpisu w docs/notes — dopisz nowy wpis"
    else:
        unverified_note = "unverified: no per-file test command"
        drop_note = "edits an existing docs/notes entry — append a new entry"

    def emit(message: str) -> None:
        if log is not None:
            log(message)

    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        replacements = item.get("replacements") or []
        if not path or not isinstance(replacements, list) or not replacements:
            continue
        if _is_notes(path):
            _verify_notes(repo, data, item, path, replacements, emit, drop_note)
        elif is_test_path(path, project.test_paths):
            forced = bool(project.test_file_command.strip())
            _verify_test(
                repo,
                data,
                item,
                path,
                replacements,
                project.test_file_command.strip() or project.pre_review,
                emit,
                unverified_note,
                forced=forced,
            )
        else:
            emit(f"fix verify {path}: ok")
    from engine.ledger import _write_json

    _write_json(analysis_file(repo), data)
    return data
