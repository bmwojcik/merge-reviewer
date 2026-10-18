"""Paths, git helpers, and log lines shared by the engine."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

VAR = "var"
LEDGER_NAME = "review-change.ledger.json"
LAST_NAME = "review-change.last.json"
FIXES_NAME = "review-change.fixes.json"
ANALYSIS_NAME = "review-change.analysis.json"
BRIEF_NAME = "review-change.brief.md"
CANVAS_NAME = "review-change-preview.canvas.tsx"
APPLY_TRIGGER_NAME = "review-change.apply.request"
SKIP_TRIGGER_NAME = "review-change.skip.request"
MR_TRIGGER_NAME = "review-change.mr.request"
ANCHOR_CHARS = 160
DEFAULT_MODEL = "grok-4.7"
DOC_BUDGET = 24_000
NOTE_BUDGET = 12_000
_SECRET = re.compile(r"crsr_[A-Za-z0-9_\-]+")
_LOG_LOCK = threading.Lock()


def reviewer_home() -> Path:
    return Path(__file__).resolve().parents[1]


def package_version() -> str:
    path = reviewer_home() / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


def redact(message: str) -> str:
    return _SECRET.sub("crsr_[redacted]", message)


def log_line(message: str) -> None:
    with _LOG_LOCK:
        sys.stdout.write(redact(message) + "\n")
        sys.stdout.flush()


def tell(message: str) -> None:
    sys.stderr.write(redact(message) + "\n")
    sys.stderr.flush()


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def env_falsey(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no"}


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), text=True
    ).strip()


def git_ok(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=str(repo), text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return ""


def repo_root() -> Path:
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return Path(out)


def var_path(repo: Path, name: str) -> Path:
    return repo / VAR / name
