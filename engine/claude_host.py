"""Claude Code runtime: one headless `claude -p` analysis. No Cursor key needed."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from engine.paths import log_line, reviewer_home

DEFAULT_TIMEOUT = 900
_READ_ONLY_GIT = ("diff", "show", "log", "status", "blame")
_DENIED_GIT = ("push", "commit", "checkout", "switch", "reset")
# Set by a user who wants add-dir CLAUDE.md files loaded. The child must never load the project's.
_MEMORY_ENV = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"
_STDERR_TAIL = 1500


def claude_binary() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "claude", Path("/usr/local/bin/claude")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def claude_timeout() -> int:
    raw = os.environ.get("REVIEW_CHANGE_CLAUDE_TIMEOUT", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_TIMEOUT


def reviewer_system_prompt() -> str:
    """Body of claude/agents/project-reviewer.md (frontmatter removed)."""
    path = reviewer_home() / "claude" / "agents" / "project-reviewer.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()


def build_command(binary: str, repo: Path, model: str, output: Path) -> list[str]:
    """The exact argv. The prompt goes to stdin, so variadic flags cannot swallow it.

    The child runs in an empty scratch directory (see run_claude), so the project's
    CLAUDE.md, CLAUDE.local.md, .claude/rules and .claude/skills are not loaded: the
    prompt already inlines the context. `--add-dir` gives tool access to the repo.
    Git is spelled `git -C <repo> ...` because the working directory is not the repo.
    """
    repo_s = str(repo)
    allowed = ["Read", "Grep", "Glob", f"Edit(/{output})"]
    allowed += [f"Bash(git -C {repo_s} {sub} *)" for sub in _READ_ONLY_GIT]
    denied = [f"Bash(git {sub} *)" for sub in _DENIED_GIT]
    denied += [f"Bash(git -C * {sub} *)" for sub in _DENIED_GIT]
    denied.append("Bash(git * --output*)")
    cmd = [
        binary,
        "-p",
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Read,Grep,Glob,Write,Bash",
        "--allowedTools",
        ",".join(allowed),
        "--disallowedTools",
        ",".join(denied),
        "--add-dir",
        repo_s,
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources",
        "user",
        "--settings",
        json.dumps({"disableAllHooks": True}),
        "--output-format",
        "json",
    ]
    system = reviewer_system_prompt()
    if system:
        cmd += ["--append-system-prompt", system]
    return cmd


def claude_preamble(repo: Path) -> str:
    """First lines of the prompt: where the repo is, since the child's cwd is a scratch dir."""
    return (
        f"REPOSITORY: {repo}\n"
        "Your working directory is an empty scratch directory, not the repository. "
        f"Open repository files by absolute path ({repo}/<path>). "
        f"Run git only as `git -C {repo} diff|show|log|status|blame ...`.\n\n"
    )


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["REVIEW_CHANGE"] = "0"  # the child must never start another review
    env["REVIEW_CHANGE_HOST"] = "claude"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("CURSOR_API_KEY", None)
    env.pop(_MEMORY_ENV, None)  # keep the repo's CLAUDE.md out of the child's context
    return env


def _summarise(stdout: str) -> tuple[bool, str]:
    """(is_error, one log line) from `--output-format json`."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return False, ""
    if not isinstance(data, dict):
        return False, ""
    parts = [f"subtype={data.get('subtype')}"]
    for key in ("num_turns", "duration_ms", "total_cost_usd"):
        if data.get(key) is not None:
            parts.append(f"{key}={data[key]}")
    usage = data.get("usage")
    if isinstance(usage, dict):
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        ):
            if usage.get(key) is not None:
                parts.append(f"{key}={usage[key]}")
    denied = data.get("permission_denials")
    if isinstance(denied, list) and denied:
        names = sorted({str(item.get("tool_name")) for item in denied if isinstance(item, dict)})
        parts.append("denied=" + ",".join(names))
    is_error = bool(data.get("is_error"))
    line = " ".join(parts)
    if is_error:
        text = re.sub(r"\s+", " ", str(data.get("result") or "")).strip()
        line += f" error={text[:300]}"
    return is_error, line


def run_claude(repo: Path, prompt: str, model: str, output: Path) -> bool:
    """Run one `claude -p`. True when the process ran to the end; the caller checks `output`."""
    binary = claude_binary()
    if not binary:
        log_line("claude CLI not found on PATH (install Claude Code and run `claude` once to log in)")
        return False
    cmd = build_command(binary, repo, model, output)
    timeout = claude_timeout()
    log_line(f"running claude -p model={model} timeout={timeout}s")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Outside the repo: Claude Code also loads CLAUDE.md files of every parent directory.
    scratch = tempfile.mkdtemp(prefix="review-change-claude-")
    try:
        return _run_in(cmd, scratch, claude_preamble(repo) + prompt, timeout)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run_in(cmd: list[str], cwd: str, prompt: str, timeout: int) -> bool:
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env(),
        )
    except OSError as exc:
        log_line(f"failed claude: {exc}")
        return False
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        log_line(f"failed claude: timeout after {timeout}s (REVIEW_CHANGE_CLAUDE_TIMEOUT)")
        return False
    except OSError as exc:
        log_line(f"failed claude: {exc}")
        return False
    is_error, summary = _summarise(stdout or "")
    log_line(f"done claude exit={proc.returncode}" + (f" | {summary}" if summary else ""))
    if proc.returncode != 0 or is_error:
        tail = (stderr or "").strip()[-_STDERR_TAIL:]
        if tail:
            log_line("claude stderr: " + tail.replace("\n", " | "))
        elif proc.returncode != 0 and not summary and (stdout or "").strip():
            log_line("claude stdout: " + (stdout or "").strip()[-_STDERR_TAIL:].replace("\n", " | "))
        return False
    return True
