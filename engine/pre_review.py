"""Run the project's pre-review command. Never raises. Never fails git."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from engine.paths import env_falsey, reviewer_home

PROBE_SECONDS = 30
TIMEOUT_SECONDS = 600
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def unit_tests_script() -> Path:
    return reviewer_home() / "engine" / "review-unit-tests.sh"


def _summary(output: str) -> str:
    for line in reversed(_ANSI.sub("", output).splitlines()):
        stripped = line.strip()
        if stripped.startswith("OK (") or stripped.startswith("FAILURES"):
            return stripped
        if stripped.startswith("Tests:") or stripped.startswith("FAILED"):
            return stripped
    return ""


def command_argv(repo: Path, command: str) -> tuple[Path, list[str]]:
    shellish = any(token in command for token in ("|", "&&", ";", ">", "<", "`"))
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = []
    if shellish or not parts or parts[0] in {"bash", "sh", "env"}:
        return repo, ["bash", "-c", command]
    first = parts[0]
    if Path(first).name == "review-unit-tests.sh":
        script = unit_tests_script()
        if script.is_file():
            return repo, [str(script), *parts[1:]]
    candidate = Path(first)
    resolved = candidate if candidate.is_absolute() else (repo / candidate).resolve()
    if resolved.is_file():
        for parent in resolved.parents:
            bin_dir = parent / "bin"
            try:
                inside = resolved.is_relative_to(bin_dir)
            except ValueError:
                inside = False
            if inside and (bin_dir / "cli").is_file():
                return parent, [str(resolved.relative_to(parent)), *parts[1:]]
        return repo, [str(resolved), *parts[1:]]
    return repo, ["bash", "-c", command]


def run_pre_review(repo: Path, command: str) -> str:
    command = command.strip()
    if not command:
        return ""
    if env_falsey("REVIEW_CHANGE_PHPUNIT"):
        return "skip (REVIEW_CHANGE_PHPUNIT=0)"
    cwd, args = command_argv(repo, command)
    cli = cwd / "bin" / "cli"
    if cli.is_file():
        try:
            probe = subprocess.run(
                [str(cli), "true"],
                cwd=str(cwd),
                check=False,
                capture_output=True,
                text=True,
                timeout=PROBE_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return "skip (docker not ready)"
        except OSError as exc:
            return f"skip ({exc})"
        if probe.returncode != 0:
            return "skip (docker not ready)"
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"skip (timeout {TIMEOUT_SECONDS}s)"
    except OSError as exc:
        return f"skip ({exc})"
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    for line in reversed(_ANSI.sub("", output).splitlines()):
        stripped = line.strip()
        if stripped.lower().startswith("skip ("):
            return stripped
    summary = _summary(output)
    if completed.returncode == 0:
        return summary or "OK"
    if summary:
        if summary.startswith("FAILURES") or summary.startswith("FAILED"):
            return summary
        return f"FAILED {summary}"
    return f"FAILED (exit {completed.returncode})"
