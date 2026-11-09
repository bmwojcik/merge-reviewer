"""Cursor runtime: env file, SDK, agent CLI, canvas buttons, merge-request push."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import select
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from engine.ledger import resolve_git_host, usage_str
from engine.paths import (
    APPLY_TRIGGER_NAME,
    MR_TRIGGER_NAME,
    SKIP_TRIGGER_NAME,
    git,
    log_line,
    reviewer_home,
    tell,
)
from engine.project import load_review_project

IN_OPEN = 0x00000020
IN_NONBLOCK = 0x00000800
INOTIFY_HEADER = struct.calcsize("iIII")
URL_RE_TEXT = r"https://[^\s]+"
WATCH_SECONDS = 4 * 3600
SETTLE_SECONDS = 5.0
_ENV_KEYS = {
    "CURSOR_API_KEY",
    "REVIEW_CHANGE_MODEL",
    "REVIEW_CHANGE_CANVAS",
    "REVIEW_CHANGE_BASE",
    "REVIEW_CHANGE_SCOPE",
    "REVIEW_CHANGE_GIT_HOST",
    "REVIEW_CHANGE_PROJECT",
    "REVIEW_CHANGE_MAX_DIFF_KB",
    "REVIEW_CHANGE_INLINE_DIFF_BYTES",
    "REVIEW_CHANGE_PHPUNIT",
    "REVIEW_CHANGE_RETRY",
    "REVIEW_CHANGE_UNIT_CMD",
    "REVIEW_CHANGE_HOST",
    "REVIEW_CHANGE_OPEN",
    "REVIEW_CHANGE_CLAUDE_TIMEOUT",
    "MERGE_REVIEWER_HOME",
    "CURSOR_TEMPLATE_HOME",
}


def env_file() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "review-change" / "cursor.env"


def load_cursor_env() -> None:
    path = env_file()
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in _ENV_KEYS:
            continue
        value = value.strip().strip("'\"")
        if not value:
            continue
        if name == "CURSOR_API_KEY":
            current = os.environ.get(name, "").strip()
            if current.startswith("crsr_"):
                continue
            if value.startswith("crsr_"):
                os.environ[name] = value
            continue
        if not os.environ.get(name, "").strip():
            os.environ[name] = value


def pid_path(repo: Path) -> Path:
    return repo / "var" / "review-change.pid"


def write_pid(repo: Path, pid: int) -> None:
    path = pid_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid) + "\n", encoding="utf-8")


def stop_previous(repo: Path) -> None:
    path = pid_path(repo)
    if not path.is_file():
        return
    try:
        old = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return
    if old <= 1:
        return
    try:
        os.kill(old, 0)
    except OSError:
        return
    try:
        os.killpg(old, signal.SIGTERM)
        tell(f"merge-reviewer: stopped previous pid {old}")
    except OSError:
        try:
            os.kill(old, signal.SIGTERM)
            tell(f"merge-reviewer: stopped previous pid {old}")
        except OSError:
            return


def apply_bin() -> Path:
    return reviewer_home() / "bin" / "apply-review-fixes"


def review_bin() -> Path:
    return reviewer_home() / "bin" / "post-commit-review"


def apply_trigger_path(repo: Path) -> Path:
    return repo / ".cursor" / APPLY_TRIGGER_NAME


def skip_trigger_path(repo: Path) -> Path:
    return repo / ".cursor" / SKIP_TRIGGER_NAME


def mr_trigger_path(repo: Path) -> Path:
    return repo / ".cursor" / MR_TRIGGER_NAME


def workspace_trigger_dirs(repo: Path) -> list[Path]:
    """`.cursor` beside the nearest ancestor `*.code-workspace`, at most four parents up."""
    folder = repo.resolve()
    for _ in range(4):
        parent = folder.parent
        if parent == folder:
            break
        folder = parent
        if any(folder.glob("*.code-workspace")):
            return [(folder / ".cursor").resolve()]
    return []


def extra_trigger_dirs(repo: Path) -> list[Path]:
    dirs = [(repo.resolve() / ".cursor")]
    for extra in workspace_trigger_dirs(repo):
        if extra not in dirs:
            dirs.append(extra)
    return dirs


def write_trigger(path: Path, state: str) -> None:
    del state
    kind = "apply"
    if path.name == SKIP_TRIGGER_NAME:
        kind = "skip"
    elif path.name == MR_TRIGGER_NAME:
        kind = "mr"
    wanted = f"canvas-trigger\n\n{kind}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wanted, encoding="utf-8")
    repo = path.parent.parent
    for folder in extra_trigger_dirs(repo):
        mirror = folder / path.name
        if mirror == path:
            continue
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(wanted, encoding="utf-8")


def prime_triggers(repo: Path) -> None:
    write_trigger(apply_trigger_path(repo), "idle")
    write_trigger(skip_trigger_path(repo), "idle")
    write_trigger(mr_trigger_path(repo), "idle")


def trigger_age(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return 0.0


def state_db_candidates() -> list[Path]:
    roots: list[Path] = [
        Path.home() / ".cursor-server" / "data" / "User" / "workspaceStorage",
    ]
    users = Path("/mnt/c/Users")
    if users.is_dir():
        roots.extend(users.glob("*/AppData/Roaming/Cursor/User/workspaceStorage"))
        roots.extend(users.glob("*/Desktop/Cursor/Priv/User/workspaceStorage"))
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(root.glob("*/state.vscdb"))
    return sorted(found, key=lambda item: item.stat().st_mtime, reverse=True)


def editor_tabs_mention(filename: str) -> bool:
    for db in state_db_candidates()[:4]:
        tmp = Path("/tmp") / f"review-change-{db.parent.name}.vscdb"
        try:
            shutil.copy2(db, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            row = con.execute(
                "select value from ItemTable where key = ?",
                ("memento/workbench.parts.editor",),
            ).fetchone()
            con.close()
        except (OSError, sqlite3.Error):
            continue
        if not row:
            continue
        value = row[0]
        blob = value if isinstance(value, str) else value.decode("utf-8", "replace")
        needles = {filename, filename.replace("/", "\\")}
        if any(needle in blob for needle in needles):
            return True
    return False


def open_inotify_dirs(paths: list[Path]) -> int | None:
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        return None
    libc = ctypes.CDLL(libc_name, use_errno=True)
    if not getattr(libc, "inotify_init1", None) or not getattr(libc, "inotify_add_watch", None):
        return None
    fd = libc.inotify_init1(IN_NONBLOCK)
    if fd < 0:
        return None
    watched = 0
    for path in paths:
        if not path.is_dir():
            continue
        watch = libc.inotify_add_watch(fd, os.fsencode(str(path)), IN_OPEN)
        if watch >= 0:
            watched += 1
    if watched == 0:
        os.close(fd)
        return None
    return fd


def inotify_event_names(fd: int) -> list[str]:
    try:
        data = os.read(fd, 4096)
    except BlockingIOError:
        return []
    names: list[str] = []
    offset = 0
    while offset + INOTIFY_HEADER <= len(data):
        _wd, _mask, _cookie, length = struct.unpack_from("iIII", data, offset)
        offset += INOTIFY_HEADER
        raw = data[offset : offset + length]
        offset += length
        name = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
        if name:
            names.append(name)
    return names


def is_single_button_open(names: list[str], filename: str) -> bool:
    if filename not in names:
        return False
    return not (APPLY_TRIGGER_NAME in names and MR_TRIGGER_NAME in names)


def default_branch_name(repo: Path) -> str:
    from engine.ledger import detect_base

    base = detect_base(repo)
    if base.startswith("origin/"):
        return base.split("/", 1)[1]
    if base in {"main", "master", "HEAD"}:
        return "main" if base == "HEAD" else base
    return base


def new_mr_url(repo: Path) -> str:
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    host, project = resolve_git_host(repo)
    if not host or not project:
        return ""
    default = default_branch_name(repo)
    if "github" in host:
        return f"https://{host}/{project}/compare/{default}...{quote(branch)}?expand=1"
    if "bitbucket" in host:
        return f"https://{host}/{project}/pull-requests/new?source={quote(branch)}"
    return (
        f"https://{host}/{project}/-/merge_requests/new"
        f"?merge_request[source_branch]={quote(branch)}"
    )


def url_from_push_output(blob: str, fallback: str) -> str:
    import re

    url_re = re.compile(URL_RE_TEXT)
    found = ""
    tokens = ("merge_requests/", "/pull/", "pull-requests/")
    for line in blob.splitlines():
        match = url_re.search(line.replace("remote:", " "))
        if not match:
            continue
        candidate = match.group(0).rstrip(").,]")
        if any(token in candidate for token in tokens):
            if "/new" not in candidate and "compare/" not in candidate:
                return candidate
            found = found or candidate
    return found or fallback


def open_url_in_cursor(url: str) -> None:
    if not url:
        log_line("create MR: no URL")
        return
    cursor = shutil.which("cursor")
    attempts: list[list[str]] = []
    if cursor:
        attempts.append([cursor, "--open-url", url])
        attempts.append([cursor, url])
    opener = shutil.which("xdg-open")
    if opener:
        attempts.append([opener, url])
    for args in attempts:
        try:
            completed = subprocess.run(
                args, check=False, capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0:
            return
    log_line("could not open merge-request URL")


def skipped_branch_names(repo: Path) -> set[str]:
    project = load_review_project(repo)
    names = {"main", "master", default_branch_name(repo)}
    names.update(project.skip_branches)
    return names


def push_and_open_mr(repo: Path) -> None:
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in skipped_branch_names(repo):
        log_line(f"create MR skipped — branch {branch}")
        return
    log_line(f"push origin {branch}")
    completed = subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    blob = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if completed.returncode != 0:
        log_line(blob.strip() or f"git push exit={completed.returncode}")
    url = url_from_push_output(blob, new_mr_url(repo))
    if url:
        log_line("merge-request URL ready")
    open_url_in_cursor(url)


def fix_paths(repo: Path) -> list[str]:
    raw = repo / "var" / "review-change.fixes.json"
    if not raw.is_file():
        return []
    try:
        data = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    paths: list[str] = []
    for item in data.get("files") or []:
        rel = str((item or {}).get("path") or "").strip()
        if rel:
            paths.append(rel)
    return paths


def open_fixed_files(repo: Path) -> None:
    cursor = shutil.which("cursor")
    paths = [str(repo / rel) for rel in fix_paths(repo) if (repo / rel).is_file()]
    if not cursor or not paths:
        return
    try:
        subprocess.Popen(
            [cursor, "-r", *paths],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        return


def _run_apply(repo: Path, skip: bool) -> bool:
    script = apply_bin()
    if not script.is_file():
        log_line("missing bin/apply-review-fixes")
        return False
    args = [sys.executable, str(script)]
    if skip:
        args.append("--skip")
    else:
        args.append(str(repo / "var" / "review-change.fixes.json"))
    completed = subprocess.run(
        args, cwd=repo, check=False, capture_output=True, text=True
    )
    if completed.stdout:
        log_line(completed.stdout.rstrip())
    if completed.returncode != 0:
        log_line(f"apply-review-fixes exit={completed.returncode}")
        if completed.stderr:
            log_line(completed.stderr.rstrip())
        return False
    return True


def finish_review_wait_for_click(repo: Path) -> None:
    from engine.canvas import publish_canvas

    published = publish_canvas(repo)
    if published is not None:
        log_line(f"published canvas {published.name}")
    apply_path = apply_trigger_path(repo)
    skip_path = skip_trigger_path(repo)
    mr_path = mr_trigger_path(repo)
    has_fixes = (repo / "var" / "review-change.fixes.json").is_file()
    write_trigger(apply_path, "pending" if has_fixes else "idle")
    write_trigger(skip_path, "pending" if has_fixes else "idle")
    write_trigger(mr_path, "pending")
    log_line("waiting for Apply, Skip, or Create merge request")
    started = time.monotonic()
    deadline = started + WATCH_SECONDS
    while time.monotonic() < started + SETTLE_SECONDS:
        time.sleep(0.2)
    apply_was_open = editor_tabs_mention(".cursor/" + APPLY_TRIGGER_NAME)
    skip_was_open = editor_tabs_mention(".cursor/" + SKIP_TRIGGER_NAME)
    mr_was_open = editor_tabs_mention(".cursor/" + MR_TRIGGER_NAME)
    notify_fd = open_inotify_dirs(extra_trigger_dirs(repo))
    applied = False
    mr_done = False
    try:
        while time.monotonic() < deadline:
            if (applied or not has_fixes) and mr_done:
                return
            names: list[str] = []
            if notify_fd is not None:
                ready, _, _ = select.select([notify_fd], [], [], 1.0)
                if ready:
                    names = inotify_event_names(notify_fd)
            else:
                time.sleep(1.0)
            apply_open = editor_tabs_mention(".cursor/" + APPLY_TRIGGER_NAME)
            skip_open = editor_tabs_mention(".cursor/" + SKIP_TRIGGER_NAME)
            mr_open = editor_tabs_mention(".cursor/" + MR_TRIGGER_NAME)
            apply_clicked = (apply_open and not apply_was_open) or is_single_button_open(
                names, APPLY_TRIGGER_NAME
            )
            skip_clicked = (skip_open and not skip_was_open) or is_single_button_open(
                names, SKIP_TRIGGER_NAME
            )
            mr_clicked = (mr_open and not mr_was_open) or is_single_button_open(
                names, MR_TRIGGER_NAME
            )
            apply_was_open = apply_open
            skip_was_open = skip_open
            mr_was_open = mr_open
            if (
                has_fixes
                and not applied
                and trigger_age(skip_path) >= SETTLE_SECONDS
                and skip_clicked
            ):
                if _run_apply(repo, skip=True):
                    write_trigger(skip_path, "skipped")
                    applied = True
                    log_line("skip recorded after click")
            if (
                has_fixes
                and not applied
                and trigger_age(apply_path) >= SETTLE_SECONDS
                and apply_clicked
            ):
                if _run_apply(repo, skip=False):
                    write_trigger(apply_path, "applied")
                    applied = True
                    open_fixed_files(repo)
                    log_line("fixes applied after click")
            if not mr_done and trigger_age(mr_path) >= SETTLE_SECONDS and mr_clicked:
                push_and_open_mr(repo)
                write_trigger(mr_path, "done")
                mr_done = True
        if has_fixes and not applied:
            log_line("timeout — fixes not applied (no click)")
    finally:
        if notify_fd is not None:
            os.close(notify_fd)


def tool_basename(args: object) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("path", "target_file", "file", "file_path"):
        value = args.get(key)
        if value:
            return Path(str(value)).name
    return ""


class ReviewProgress:
    def on_tool(self, name: str, status: str, args: object) -> None:
        name = (name or "tool").strip().lower()
        if status == "running" and name in {"write", "edit"}:
            path = tool_basename(args)
            if path:
                log_line(f"write {path}")
        elif status == "error":
            path = tool_basename(args)
            log_line(f"error {name}" + (f" {path}" if path else ""))


def _run_sdk(repo: Path, prompt: str, model: str) -> bool:
    if not os.environ.get("CURSOR_API_KEY"):
        return False
    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError:
        return False
    log_line(f"running cursor-sdk model={model}")
    try:
        options: dict[str, object] = {
            "api_key": os.environ["CURSOR_API_KEY"],
            "local": LocalAgentOptions(cwd=str(repo), setting_sources=["project", "user"]),
        }
        if model:
            options["model"] = model
        with Agent.create(AgentOptions(**options)) as agent:
            run = agent.send(prompt)
            progress = ReviewProgress()
            for message in run.messages():
                if getattr(message, "type", "") != "tool_call":
                    continue
                progress.on_tool(
                    getattr(message, "name", "") or "tool",
                    getattr(message, "status", "") or "running",
                    getattr(message, "args", None),
                )
            result = run.wait()
    except Exception as exc:  # noqa: BLE001 — record failure, do not abort git
        log_line(f"failed sdk: {exc}")
        return False
    usage = usage_str(result)
    log_line(f"done {result.status}" + (f" | {usage}" if usage else ""))
    return True


def _run_agent_cli(repo: Path, prompt: str, model: str) -> bool:
    binary = shutil.which("agent")
    if not binary:
        return False
    chat_id = ""
    try:
        created = subprocess.run(
            [binary, "create-chat"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        chat_id = (created.stdout or "").strip().split()[-1] if created.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        chat_id = ""
    cmd = [binary, "--print", "--trust", "--workspace", str(repo), "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])
    if chat_id:
        cmd.extend(["--resume", chat_id])
        log_line(f"running agent CLI model={model}")
    else:
        log_line(f"running agent CLI model={model}")
    cmd.append(prompt)
    completed = subprocess.run(cmd, cwd=repo, check=False)
    log_line(f"done agent CLI exit={completed.returncode}")
    return True


def review_once(repo: Path, prompt: str, model: str, output: Path | None = None) -> bool:
    if _run_sdk(repo, prompt, model) and (output is None or output.is_file()):
        return True
    if _run_agent_cli(repo, prompt, model) and (output is None or output.is_file()):
        return True
    return False


def spawn_background(repo: Path, model: str, host: str = "cursor") -> int:
    from datetime import datetime

    from engine.canvas import canvas_path, write_pending_canvas

    sha = git(repo, "rev-parse", "--short", "HEAD")
    subject = git(repo, "log", "-1", "--pretty=%s")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    started = datetime.now().strftime("%H:%M:%S")
    stop_previous(repo)
    branch_scope = os.environ.get("REVIEW_CHANGE_SCOPE", "commit").strip().lower() == "branch"
    if host == "claude":
        from engine import report

        report.write_pending_report(repo, sha, subject, branch, started, branch_scope)
        report.write_status(
            repo, "running", sha=sha, subject=subject, branch=branch, started_at=started, model=model
        )
    else:
        write_pending_canvas(repo, sha, subject, branch, started, branch_scope=branch_scope)
    log = repo / "var" / "review-change.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"merge-reviewer: {sha} {started} {subject}\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["REVIEW_CHANGE_HOST"] = host  # the child carries the decision, not CLAUDECODE
    if model:
        env["REVIEW_CHANGE_MODEL"] = model
    cmd = [sys.executable, "-u", str(review_bin()), "--run"]
    if model:
        cmd.extend(["--model", model])
    child = subprocess.Popen(
        cmd,
        cwd=repo,
        stdout=open(log, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    write_pid(repo, child.pid)
    tell(f"merge-reviewer: started pid {child.pid}")
    tell(f"merge-reviewer: commit  {sha}  {subject}")
    tell(f"merge-reviewer: model   {model}")
    if host == "claude":
        from engine import report

        tell("merge-reviewer: host    claude")
        tell(f"merge-reviewer: report  {report.html_path(repo)}")
    else:
        tell(f"merge-reviewer: canvas  {canvas_path(repo)}")
    tell(f"merge-reviewer: log     {log}")
    return 0
