"""Blob-keyed review ledger.

A file is verified at `git rev-parse HEAD:<path>`. The next commit reviews only
blobs that changed. Dismissed notes stick to the branch by a code anchor, not a
line number. One commit writes one `var/review-change.analysis.json`.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from engine.paths import (
    ANALYSIS_NAME,
    ANCHOR_CHARS,
    BRIEF_NAME,
    FIXES_NAME,
    LAST_NAME,
    LEDGER_NAME,
    git_ok,
    var_path,
)
from engine.project import ReviewProject, load_review_project

FAILED_RETRY_LOG = (
    "previous pass failed on identical files — skipping; "
    "amend or set REVIEW_CHANGE_RETRY=1"
)


def _load_json(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _anchor(text: str) -> str:
    return " ".join(text.split())[:ANCHOR_CHARS]


_KNOWN_GIT_HOSTS = ("bitbucket.org", "github.com", "gitlab.com")


def _prefix_git_host(host: str) -> str:
    lowered = host.strip().lower().rstrip(".")
    for known in _KNOWN_GIT_HOSTS:
        if lowered == known or lowered.startswith(known + "-") or lowered.startswith(known + "."):
            return known
    return ""


def ssh_config_hostname(alias: str) -> str:
    """HostName for an exact `Host` token in ~/.ssh/config. Empty if unreadable."""
    alias = alias.strip()
    if not alias or any(char in alias for char in "*?["):
        return ""
    path = Path.home() / ".ssh" / "config"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    matched = False
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        key = parts[0].lower()
        value = parts[1].strip() if len(parts) > 1 else ""
        if key == "host":
            matched = alias in value.split()
            continue
        if matched and key == "hostname" and value:
            return value.split()[0]
    return ""


def normalize_git_host(host: str) -> str:
    """Map `github.com-alias` / `HostName` to the real git host. A set host still wins over origin."""
    host = host.strip()
    if not host:
        return ""
    mapped = ssh_config_hostname(host)
    candidate = (mapped or host).strip()
    return _prefix_git_host(candidate) or candidate


def parse_git_remote(url: str) -> tuple[str, str]:
    url = url.strip()
    if not url:
        return "", ""
    if url.startswith("git@"):
        rest = url.split("@", 1)[1]
        if ":" not in rest:
            return "", ""
        host_part, path = rest.split(":", 1)
        return normalize_git_host(host_part), path.removesuffix(".git")
    url = url.removeprefix("https://").removeprefix("http://")
    if "@" in url.split("/", 1)[0]:
        url = url.split("@", 1)[1]
    if "/" not in url:
        return "", ""
    host_part, path = url.split("/", 1)
    return normalize_git_host(host_part), path.removesuffix(".git")


def resolve_git_host(
    repo: Path, settings: ReviewProject | None = None
) -> tuple[str, str]:
    settings = settings or load_review_project(repo)
    host = os.environ.get("REVIEW_CHANGE_GIT_HOST", "").strip() or settings.git_host
    project = (
        os.environ.get("REVIEW_CHANGE_PROJECT", "").strip().strip("/")
        or settings.git_project
    )
    parsed_host, parsed_project = parse_git_remote(
        git_ok(repo, "remote", "get-url", "origin")
    )
    if host:
        host = normalize_git_host(host)
    else:
        host = parsed_host
    return host, project or parsed_project


def current_branch(repo: Path) -> str:
    return git_ok(repo, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"


def detect_base(repo: Path) -> str:
    override = os.environ.get("REVIEW_CHANGE_BASE", "").strip()
    if override and git_ok(repo, "rev-parse", "--verify", override):
        return override
    project = load_review_project(repo)
    if project.base_branch and git_ok(repo, "rev-parse", "--verify", project.base_branch):
        return project.base_branch
    branch = current_branch(repo)
    fallback = ""
    matched: tuple[int, str] | None = None
    for suffix, ref in project.base_suffixes:
        if suffix == "default":
            fallback = ref
            continue
        if branch.endswith(suffix) and (matched is None or len(suffix) > matched[0]):
            matched = (len(suffix), ref)
    if matched and git_ok(repo, "rev-parse", "--verify", matched[1]):
        return matched[1]
    if fallback and git_ok(repo, "rev-parse", "--verify", fallback):
        return fallback
    if project.legacy_base and git_ok(repo, "rev-parse", "--verify", project.legacy_base):
        return project.legacy_base
    for candidate in ("origin/main", "main", "origin/master", "master"):
        if git_ok(repo, "rev-parse", "--verify", candidate):
            return candidate
    return "HEAD"


def base_sha(repo: Path, base: str) -> str:
    return git_ok(repo, "rev-parse", base)


def scope_ref(repo: Path, base: str) -> str:
    """Diff ref for this run. Default is the triggering commit, not the branch."""
    mode = os.environ.get("REVIEW_CHANGE_SCOPE", "commit").strip().lower()
    if mode == "branch":
        return base
    parent = git_ok(repo, "rev-parse", "--verify", "HEAD~1")
    return "HEAD~1" if parent else base


def changed_files(repo: Path, base: str) -> list[str]:
    out = git_ok(repo, "diff", "--name-only", f"{base}...HEAD")
    return [line for line in out.splitlines() if line.strip()]


def blob_for(repo: Path, path: str) -> str:
    return git_ok(repo, "rev-parse", f"HEAD:{path}")


def load_ledger(repo: Path) -> dict:
    data = _load_json(var_path(repo, LEDGER_NAME))
    return data if isinstance(data, dict) else {}


def save_ledger(repo: Path, ledger: dict) -> None:
    _write_json(var_path(repo, LEDGER_NAME), ledger)


def clear_last(repo: Path) -> None:
    try:
        var_path(repo, LAST_NAME).unlink()
    except OSError:
        pass


def ledger_for(repo: Path, base: str) -> dict:
    ledger = load_ledger(repo)
    branch = current_branch(repo)
    tip = base_sha(repo, base)
    if ledger.get("branch") != branch or ledger.get("base_sha") != tip:
        return {"branch": branch, "base": base, "base_sha": tip, "entries": {}}
    ledger.setdefault("entries", {})
    return ledger


def work_set(repo: Path, base: str, scope: str | None = None) -> dict:
    ledger = ledger_for(repo, base)
    entries = ledger.get("entries", {})
    changed = changed_files(repo, scope or base)
    seen = set(changed)
    carry = [
        path
        for path, entry in entries.items()
        if isinstance(entry, dict)
        and entry.get("status") == "not_reviewed"
        and path not in seen
        and blob_for(repo, path)
    ]
    in_scope: list[str] = []
    verified: list[str] = []
    for path in changed + carry:
        blob = blob_for(repo, path)
        entry = entries.get(path) if isinstance(entries.get(path), dict) else None
        if (
            entry
            and blob
            and entry.get("status") == "verified"
            and entry.get("blob") == blob
        ):
            verified.append(path)
        else:
            in_scope.append(path)
    return {"ledger": ledger, "in_scope": in_scope, "verified": verified}


def previous_notes(repo: Path, base: str, paths: list[str]) -> dict:
    entries = ledger_for(repo, base).get("entries", {})
    notes: dict[str, dict] = {}
    for path in paths:
        entry = entries.get(path) if isinstance(entries.get(path), dict) else {}
        findings = entry.get("findings") or []
        dismissed = entry.get("dismissed") or []
        if findings or dismissed:
            notes[path] = {"findings": findings, "dismissed": dismissed}
    return notes


def scope_prompt(
    repo: Path,
    base: str,
    paths: list[str] | None = None,
    scope: str | None = None,
    write_last: bool = True,
) -> str:
    ws = work_set(repo, base, scope)
    diff_ref = scope or base
    in_scope = ws["in_scope"] if paths is None else list(paths)
    verified = ws["verified"]
    notes = previous_notes(repo, base, in_scope + verified)
    lines: list[str] = []
    lines.append("## Review ledger (blob-keyed — converge in ONE pass)")
    lines.append(
        "Review ONLY the files under 'In scope'. If their full diff is inline in "
        "the preview below, review from it and do not re-run `git diff` (except "
        "for the Retry list below). If the preview says the diff was too big to "
        f"inline, pull each file's hunks with `git diff {diff_ref}...HEAD -- <path>` "
        "— never skip a file or leave its findings empty just because it was not "
        "inlined."
    )
    lines.append(
        "Project knowledge that matches this diff is already inlined in the prompt. "
        "Do not open docs/features/INDEX.md, do not grep the repository, and do not "
        "open other docs. Open at most ONE existing source file, and only when a "
        "hunk uses a symbol whose contract you cannot judge from the diff."
    )
    lines.append("")
    lines.append("### In scope (changed since last verified — review every one)")
    if in_scope:
        lines.extend(f"- {path}" for path in in_scope)
    else:
        lines.append("- (none — nothing changed since the last verified pass)")
    carried = [path for path in in_scope if path not in set(changed_files(repo, diff_ref))]
    if carried:
        lines.append("")
        lines.append(
            "### Retry — carried over, NOT in this commit's diff "
            f"(`git diff {diff_ref}...HEAD` shows nothing for these)"
        )
        lines.append(
            f"Review each from its real change: `git diff {base}...HEAD -- <path>`. "
            "A path here is reviewed only after you have seen a non-empty diff for it."
        )
        lines.extend(f"- {path}" for path in carried)
    lines.append("")
    lines.append(
        "### Already verified (context only — do not re-review unless an in-scope "
        "file changes a public contract they depend on)"
    )
    if verified:
        lines.extend(f"- {path}" for path in verified)
    else:
        lines.append("- (none)")
    dismissed_lines: list[str] = []
    finding_lines: list[str] = []
    for path, note in notes.items():
        for anchor in note.get("dismissed") or []:
            dismissed_lines.append(f"- {path}: {anchor}")
        for finding in note.get("findings") or []:
            anchor = finding.get("anchor") if isinstance(finding, dict) else str(finding)
            if anchor:
                finding_lines.append(f"- {path}: {_anchor(str(anchor))}")
    if dismissed_lines:
        lines.append("")
        lines.append("### Dismissed by the human (never raise these again on this branch)")
        lines.extend(dismissed_lines)
    if finding_lines:
        lines.append("")
        lines.append(
            "### Already reported (repeat a note only if it is still unfixed — "
            "do not invent a new variant of it)"
        )
        lines.extend(finding_lines)
    if write_last:
        lines.append("")
        lines.append(
            "After the verdict also write `var/review-change.last.json`:\n"
            '{"base":"<ref>","head":"<short sha>",'
            '"reviewed":[{"path":"<in-scope path>","findings":['
            '{"anchor":"<unique snippet from the file>","severity":"blocking|nit",'
            '"category":"<short>"}]}],"not_reviewed":["<path + why>"]}\n'
            "List every in-scope path exactly once under `reviewed` or "
            "`not_reviewed`. The script stamps blob hashes."
        )
    return "\n".join(lines) + "\n"


def merge_last(repo: Path, base: str, scope: str | None = None) -> tuple[int, int]:
    ws = work_set(repo, base, scope)
    expected = len(ws["in_scope"])
    ledger = ws["ledger"]
    entries = ledger["entries"]
    last = _load_json(var_path(repo, LAST_NAME))
    head = git_ok(repo, "rev-parse", "--short", "HEAD")
    reviewed = 0
    fresh = (
        isinstance(last, dict)
        and bool(head)
        and str(last.get("head") or "").strip() == head
    )
    if fresh:
        allowed = set(ws["in_scope"])
        mentioned: set[str] = set()
        too_large = str(last.get("verdict") or "") == "too-large"
        global_failed = bool(last.get("pass_failed")) and not too_large
        failed_raw = last.get("pass_failed_paths")
        failed_paths = {
            str(item).strip()
            for item in (failed_raw if isinstance(failed_raw, list) else [])
            if str(item).strip()
        }

        def store(path: str, status: str, findings: object, mark_failed: bool) -> None:
            prev = entries.get(path) if isinstance(entries.get(path), dict) else {}
            kept = findings if isinstance(findings, list) else (prev.get("findings") or [])
            entry = {
                "blob": blob_for(repo, path),
                "status": status,
                "findings": kept,
                "dismissed": prev.get("dismissed") or [],
            }
            if mark_failed:
                entry["pass_failed"] = True
            entries[path] = entry

        def mark_failed(path: str) -> bool:
            if too_large:
                return False
            return global_failed or path in failed_paths

        for item in last.get("reviewed") or []:
            path = str((item or {}).get("path") or "").strip()
            if not path or path not in allowed:
                continue
            store(path, "verified", (item or {}).get("findings") or [], False)
            reviewed += 1
            mentioned.add(path)
        for raw in last.get("not_reviewed") or []:
            path = str(raw or "").split(" ", 1)[0].strip()
            if not path or path not in allowed:
                continue
            prev = entries.get(path) if isinstance(entries.get(path), dict) else {}
            status = "skipped-too-large" if too_large else "not_reviewed"
            store(path, status, prev.get("findings") or [], mark_failed(path))
            mentioned.add(path)
        for path in ws["in_scope"]:
            if path in mentioned:
                continue
            prev = entries.get(path) if isinstance(entries.get(path), dict) else {}
            blob = blob_for(repo, path)
            same = bool(blob) and prev.get("blob") == blob
            if same and (
                prev.get("status") == "skipped-too-large"
                or (prev.get("pass_failed") and not mark_failed(path))
            ):
                continue
            status = "skipped-too-large" if too_large else "not_reviewed"
            store(path, status, prev.get("findings") or [], mark_failed(path))
    save_ledger(repo, ledger)
    return expected, reviewed


def stamp_fixes_blobs(repo: Path) -> None:
    path = var_path(repo, FIXES_NAME)
    data = _load_json(path)
    if not isinstance(data, dict):
        return
    changed = False
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").strip()
        if not rel:
            continue
        blob = blob_for(repo, rel)
        if blob and item.get("blob") != blob:
            item["blob"] = blob
            changed = True
    if changed:
        _write_json(path, data)


def blob_matches(repo: Path, path: str, expected: str) -> bool:
    """True when the file is still the blob the reviewer saw, and the worktree is clean."""
    if not expected:
        return True
    if blob_for(repo, path) != expected:
        return False
    return git_ok(repo, "status", "--porcelain", "--", path) == ""


def record_dismissed(repo: Path, base: str | None = None) -> int:
    base = base or detect_base(repo)
    ledger = ledger_for(repo, base)
    entries = ledger["entries"]
    fixes = _load_json(var_path(repo, FIXES_NAME))
    added = 0
    if isinstance(fixes, dict):
        for item in fixes.get("files") or []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            entry = entries.setdefault(
                path,
                {
                    "blob": blob_for(repo, path),
                    "status": "not_reviewed",
                    "findings": [],
                    "dismissed": [],
                },
            )
            dismissed = entry.setdefault("dismissed", [])
            for repl in item.get("replacements") or []:
                anchor = _anchor(str((repl or {}).get("old") or ""))
                if anchor and anchor not in dismissed:
                    dismissed.append(anchor)
                    added += 1
            for finding in entry.get("findings") or []:
                text = finding.get("anchor") if isinstance(finding, dict) else str(finding)
                anchor = _anchor(str(text or ""))
                if anchor and anchor not in dismissed:
                    dismissed.append(anchor)
                    added += 1
            entry["findings"] = []
    save_ledger(repo, ledger)
    return added


def analysis_file(repo: Path) -> Path:
    return var_path(repo, ANALYSIS_NAME)


def brief_file(repo: Path) -> Path:
    return var_path(repo, BRIEF_NAME)


def max_diff_kb() -> int:
    raw = os.environ.get("REVIEW_CHANGE_MAX_DIFF_KB", "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def max_diff_bytes() -> int:
    return max_diff_kb() * 1024


def too_large_reason(size_bytes: int, limit_bytes: int) -> str:
    shown = (max(0, size_bytes) + 1023) // 1024
    limit_kb = max(0, limit_bytes) // 1024
    return (
        "commit too large for one review pass "
        f"({shown} kB > {limit_kb} kB) — split the commit; "
        "set REVIEW_CHANGE_MAX_DIFF_KB to override"
    )


def work_diff_bytes(repo: Path, scope: str, paths: list[str]) -> int:
    if not paths:
        return 0
    cmd = ["git", "diff", "--no-color", "-U20", f"{scope}...HEAD", "--", *paths]
    try:
        return len(
            subprocess.check_output(cmd, cwd=str(repo), stderr=subprocess.DEVNULL)
        )
    except (subprocess.CalledProcessError, OSError):
        return 0


def blobs_for(repo: Path, paths: list[str]) -> list[tuple[str, str]]:
    return [(path, blob_for(repo, path)) for path in paths]


def decide_review(
    files: list[tuple[str, str]],
    entries: dict,
    limit_bytes: int,
    retry: bool,
    measure: Callable[[list[str]], int],
) -> tuple[str, list[str], int]:
    """Return (decision, paths, diff_bytes).

    decision is `run`, `too-large`, `skip-too-large`, or `skip-failed`.
    A non-positive limit never measures the diff and never returns `too-large`.
    """
    if not files:
        return "run", [], 0
    fresh: list[str] = []
    large: list[str] = []
    failed: list[str] = []
    capped = limit_bytes > 0
    for path, blob in files:
        entry = entries.get(path) if isinstance(entries.get(path), dict) else {}
        same = bool(blob) and entry.get("blob") == blob
        if (
            same
            and entry.get("pass_failed")
            and entry.get("status") == "not_reviewed"
            and not retry
        ):
            failed.append(path)
        elif capped and same and entry.get("status") == "skipped-too-large":
            large.append(path)
        else:
            fresh.append(path)
    if not fresh and not large:
        return "skip-failed", failed, 0
    order = [path for path, _blob in files]
    fresh_set = set(fresh)
    large_set = set(large)
    pool = [path for path in order if path in fresh_set or path in large_set]
    if not capped:
        return "run", pool, 0
    pool_size = int(measure(pool))
    if pool_size > limit_bytes:
        if not fresh:
            return "skip-too-large", pool, pool_size
        fresh_paths = [path for path in order if path in fresh_set]
        fresh_size = int(measure(fresh_paths))
        if fresh_size > limit_bytes:
            return "too-large", fresh_paths, fresh_size
        return "run", fresh_paths, fresh_size
    return "run", pool, pool_size


def record_too_large(repo: Path, paths: list[str], reason: str) -> None:
    _write_json(
        analysis_file(repo),
        {
            "verdict": "too-large",
            "summary": reason,
            "reviewed": [],
            "not_reviewed": [f"{path} ({reason})" for path in paths],
            "files": [],
        },
    )


def record_failed_analysis(repo: Path, paths: list[str], reason: str) -> None:
    _write_json(
        analysis_file(repo),
        {
            "not_reviewed": [f"{path} ({reason})" for path in paths],
            "pass_failed": True,
        },
    )


def clear_analysis(repo: Path) -> None:
    var = repo / "var"
    if not var.is_dir():
        return
    stale = [var / ANALYSIS_NAME, var / BRIEF_NAME, *var.glob("review-change.shard-*.json")]
    for path in stale:
        try:
            path.unlink()
        except OSError:
            pass


_VERDICT_RANK = {
    "approve": 0,
    "approve-with-nits": 1,
    "request-changes": 2,
    "too-large": 3,
}


def merge_analysis(repo: Path) -> dict:
    data = _load_json(analysis_file(repo))
    if not isinstance(data, dict):
        data = {}
    reviewed = [item for item in (data.get("reviewed") or []) if isinstance(item, dict)]
    not_reviewed = [str(item) for item in (data.get("not_reviewed") or []) if str(item).strip()]
    files = [item for item in (data.get("files") or []) if isinstance(item, dict)]
    candidate = str(data.get("verdict") or "").strip()
    verdict = candidate if candidate in _VERDICT_RANK else ""
    summary = str(data.get("summary") or "").strip()
    mr = data.get("mr") if isinstance(data.get("mr"), dict) else {}
    head = git_ok(repo, "rev-parse", "--short", "HEAD")
    pass_failed = bool(data.get("pass_failed")) and verdict != "too-large"
    failed_raw = data.get("pass_failed_paths")
    failed_paths = [
        str(item).strip()
        for item in (failed_raw if isinstance(failed_raw, list) else [])
        if str(item).strip()
    ]
    last_body: dict = {"head": head, "reviewed": reviewed, "not_reviewed": not_reviewed}
    if verdict:
        last_body["verdict"] = verdict
    if pass_failed:
        last_body["pass_failed"] = True
    if failed_paths and verdict != "too-large":
        last_body["pass_failed_paths"] = failed_paths
    _write_json(var_path(repo, LAST_NAME), last_body)
    fixes: dict = {"commit": head, "files": files}
    if verdict:
        fixes["verdict"] = verdict
    if summary:
        fixes["summary"] = summary
    if mr:
        fixes["mr"] = mr
    _write_json(var_path(repo, FIXES_NAME), fixes)
    return {
        "reviewed": reviewed,
        "not_reviewed": not_reviewed,
        "files": files,
        "mr": mr,
        "verdict": verdict,
        "summary": summary,
        "pass_failed_paths": failed_paths,
    }


def usage_str(result: object) -> str:
    for attr in ("usage", "token_usage", "tokens", "usage_metadata"):
        value = getattr(result, attr, None)
        if value:
            return str(value)
    return ""
