"""One analysis per commit, then a script-rendered canvas."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from engine.canvas import (
    canvas_path,
    render_canvas_tsx,
    review_ui,
    write_canvas,
    write_idle_canvas,
    write_pending_canvas,
)
from engine.context import ReviewContext, build_context, write_brief
from engine.ledger import (
    FAILED_RETRY_LOG,
    analysis_file,
    blobs_for,
    clear_analysis,
    clear_last,
    decide_review,
    detect_base,
    max_diff_bytes,
    merge_analysis,
    merge_last,
    record_failed_analysis,
    record_too_large,
    scope_prompt,
    scope_ref,
    stamp_fixes_blobs,
    too_large_reason,
    work_diff_bytes,
    work_set,
)
from engine.paths import (
    CANVAS_NAME,
    env_falsey,
    env_truthy,
    git,
    log_line,
    package_version,
    repo_root,
    tell,
)
from engine.pre_review import run_pre_review
from engine.project import load_review_project, version_older
from engine.verify import verify_proposed_fixes

SKIP_SUBJECT_MARKERS = ("[skip review]", "[skip review-change]")


def argv_value(flag: str) -> str:
    args = sys.argv[1:]
    prefix = flag + "="
    for index, arg in enumerate(args):
        if arg == flag and index + 1 < len(args) and not args[index + 1].startswith("-"):
            return args[index + 1].strip()
        if arg.startswith(prefix):
            return arg.split("=", 1)[1].strip()
    return ""


def resolve_model(repo: Path | None = None) -> str:
    cli = argv_value("--model")
    if cli:
        return cli
    env = os.environ.get("REVIEW_CHANGE_MODEL", "").strip()
    if env:
        return env
    if repo is not None:
        project = load_review_project(repo)
        if project.model:
            return project.model
    return "grok-4.7"


def should_skip(repo: Path) -> str | None:
    if env_falsey("REVIEW_CHANGE") or env_truthy("REVIEW_CHANGE_SKIP"):
        return "REVIEW_CHANGE disabled"
    if os.environ.get("CI") or os.environ.get("GITLAB_CI") or os.environ.get("GITHUB_ACTIONS"):
        return "CI"
    probe = repo / ".githooks" / "is-local-dev.sh"
    if probe.is_file():
        if subprocess.call(["sh", str(probe)], cwd=repo) != 0:
            return "not a local laptop"
    from engine.host import skipped_branch_names

    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in skipped_branch_names(repo):
        return f"branch {branch}"
    parents = git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) > 2:
        return "merge commit"
    git_dir = Path(git(repo, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (git_dir / marker).exists():
            return f"{marker} in progress"
    subject = git(repo, "log", "-1", "--pretty=%s")
    lower = subject.lower()
    for marker in SKIP_SUBJECT_MARKERS:
        if marker in lower:
            return marker
    return None


def warn_version(repo: Path) -> str:
    project = load_review_project(repo)
    if not project.min_version:
        return ""
    installed = package_version()
    if version_older(installed, project.min_version):
        return (
            f"merge-reviewer: this checkout is {installed}; "
            f"the project asks for {project.min_version}"
        )
    return ""


def build_analysis_prompt(repo: Path, base: str, paths: list[str], scope: str, context: ReviewContext) -> str:
    ui = review_ui(repo)
    project = load_review_project(repo)
    lang = ui["note_language"]
    scope_text = scope_prompt(repo, base, paths, scope, write_last=False)
    out = analysis_file(repo)
    sha = git(repo, "rev-parse", "--short", "HEAD")
    guidance = ""
    if project.rules.strip():
        guidance += "## Quality bar (project)\n" + project.rules.strip() + "\n\n"
    if project.agent_rules.strip():
        guidance += "## Agent rules (project)\n" + project.agent_rules.strip() + "\n\n"
    knowledge = context.knowledge or (
        "No docs/features/INDEX.md and no docs/notes matched. Review the diff alone.\n"
    )
    # Docs, rules, and the diff are concatenated so braces in source are literal.
    head = f"""MODE: preview (one analysis for this commit)
INTENT: analyse {sha} and emit JSON only. The script renders the canvas. Do not render it.
You review the whole in-scope set in one pass. There is no second model and no split.
You are a fresh project-reviewer. Apply `.cursor/agents/project-reviewer.md`.
The reading budget below overrides any instruction to discover docs.
Do not resume any previous session.

"""
    tail = f"""READING BUDGET:
- Matching INDEX rows and notes are already inlined above. Do not open them again.
- The in-scope diff is inline below when it fits. Review from it.
- Do not grep the repository. Do not open architecture or feature docs.
- Re-run git diff only when the preview says the diff was too big to inline.
- Open at most one existing source file, and only if a hunk's contract is not in the diff.
- Finish in a few turns.

Do not write the canvas. Do not edit var/review-change.fixes.json or last.json.
Create or edit no other file.
Write summary, each finding note, and mr.title / mr.body in {lang}.
Write ONLY {out} as JSON (no prose, no code fence):
{{"verdict":"approve|approve-with-nits|request-changes","summary":"<one line, {lang}>",
  "reviewed":[{{"path":"<in-scope path>","findings":[{{"anchor":"<unique snippet>","severity":"blocking|nit","category":"<short>","note":"<{lang}, 1-2 sentences>"}}]}}],
  "not_reviewed":["<path + why>"],
  "files":[{{"path":"<workspace path>","replacements":[{{"old":"<unique>","new":"<fix>"}}]}}],
  "mr":{{"title":"...","body":"..."}}}}
List every in-scope path exactly once under reviewed (empty findings only after you
reviewed that path's diff) or under not_reviewed. A path you never saw is not_reviewed.
Keep old/new to the smallest unique snippet. One sentence per note.
Before a replacement, review its new text yourself.
Do not set pass_failed.

"""
    from engine.tool_layer import load_tool_layer, render_local_layer

    layer_block = render_local_layer(load_tool_layer(repo, scope, project))
    if layer_block and not layer_block.endswith("\n"):
        layer_block += "\n"
    return head + knowledge + "\n" + guidance + layer_block + tail + scope_text + "\n" + context.preview


def build_canvas_prompt(repo: Path, base: str, scope: str, merged: dict) -> str:
    context = build_context(repo, base, scope)
    sha = git(repo, "rev-parse", "--short", "HEAD")
    subject = git(repo, "log", "-1", "--pretty=%s")
    fixes = repo / "var" / "review-change.fixes.json"
    last = repo / "var" / "review-change.last.json"
    file_list = ", ".join(str(item.get("path", "")) for item in merged.get("files") or [])
    reviewed_list = ", ".join(str(item.get("path", "")) for item in merged.get("reviewed") or [])
    verdict = str(merged.get("verdict") or "approve-with-nits").replace("{", "{{").replace("}", "}}")
    summary = str(merged.get("summary") or "").replace("{", "{{").replace("}", "}}")
    subject = subject.replace("{", "{{").replace("}", "}}")
    file_list = file_list.replace("{", "{{").replace("}", "}}")
    reviewed_list = reviewed_list.replace("{", "{{").replace("}", "}}")
    ui = review_ui(repo)
    target = canvas_path(repo)
    head = f"""MODE: preview (canvas render only — analysis of {sha} is done)
INTENT: render ONE review canvas for {sha} — {subject}. You do not review code.

Do not resume any previous session.
Do not re-review the diff. Do not read the INDEX. Do not edit application files.
Do not edit {fixes} or {last}.

Verdict: {verdict}
Summary: {summary}
Proposals in {fixes}: {file_list or "(none)"}
Findings in {last}: {reviewed_list or "(none)"}

Overwrite exactly {target} with the finished review.
Render a FileDiff + DiffView for every proposal, using lines of
{{type, content, lineNumber}} where type is added, removed, or unchanged.
Use a ghost Button with the workspace path that dispatches openFile.
Canvas verdict labels ({ui["note_language"]}):
- approve → {ui["approve"]}
- approve-with-nits → {ui["approve_nits"]}
- request-changes → {ui["request_changes"]}
Buttons via useCanvasAction openFile (no newComposerChat):
- {ui["apply"]}: .cursor/review-change.apply.request with startLineNumber 2 and a changing startColumn
- {ui["skip"]}: .cursor/review-change.skip.request with startLineNumber 1
- {ui["create_mr"]}: .cursor/review-change.mr.request
Never git push. Never create a branch. Never create a merge request.
Put the MR title and body from {fixes} on the canvas as text.

"""
    return head + context.preview


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def run_analysis(repo: Path, prompt: str, model: str, paths: list[str]) -> bool:
    from engine.host import review_once

    out = analysis_file(repo)
    review_once(repo, prompt, model, out)
    if not out.is_file():
        record_failed_analysis(repo, paths, "no analysis output")
        log_line("analysis: no output — files stay not_reviewed")
        return False
    return True


def _publish_too_large(repo: Path, base: str, scope: str, paths: list[str], reason: str, started: float, sha: str, subject: str) -> None:
    clear_last(repo)
    clear_analysis(repo)
    record_too_large(repo, paths, reason)
    merged = merge_analysis(repo)
    stamp_fixes_blobs(repo)
    merge_last(repo, base, scope)
    finished = datetime.now().strftime("%H:%M:%S")
    elapsed = _format_elapsed(time.monotonic() - started)
    ui = review_ui(repo)
    try:
        write_canvas(repo, merged, {"finished": finished, "elapsed": elapsed})
        log_line(f"wrote {CANVAS_NAME} (script)")
    except Exception as exc:  # noqa: BLE001
        log_line(f"canvas render failed: {exc}")
        write_idle_canvas(repo, sha, subject, ui["too_large"], reason)


def run() -> int:
    from engine.host import finish_review_wait_for_click, prime_triggers, review_once, write_pid

    repo = repo_root()
    write_pid(repo, os.getpid())
    project = load_review_project(repo)
    if not project.enabled:
        return 0
    warning = warn_version(repo)
    if warning:
        log_line(warning)
    reason = should_skip(repo)
    if reason:
        log_line(f"skip ({reason})")
        return 0
    sha = git(repo, "rev-parse", "--short", "HEAD")
    subject = git(repo, "log", "-1", "--pretty=%s")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    started_at = datetime.now().strftime("%H:%M:%S")
    started = time.monotonic()
    ui = review_ui(repo)
    model = resolve_model(repo)
    prime_triggers(repo)
    base = detect_base(repo)
    scope = scope_ref(repo, base)
    work = work_set(repo, base, scope)
    in_scope = work["in_scope"]
    log_line(
        f"ledger scope vs {scope}: {len(in_scope)} in scope, "
        f"{len(work['verified'])} verified (context only)"
    )
    if not in_scope:
        log_line("nothing changed since the last verified pass — no review")
        write_idle_canvas(repo, sha, subject, ui["no_changes"], ui["no_changes_note"])
        return 0
    entries = work["ledger"].get("entries") or {}
    if not isinstance(entries, dict):
        entries = {}
    limit = max_diff_bytes()
    decision, paths, size = decide_review(
        blobs_for(repo, in_scope),
        entries,
        limit,
        env_truthy("REVIEW_CHANGE_RETRY"),
        lambda subset: work_diff_bytes(repo, scope, subset),
    )
    log_line(f"analysis: one (decision {decision}, files {len(paths)})")
    if decision in {"too-large", "skip-too-large"}:
        reason = too_large_reason(size, limit)
        log_line(reason)
        _publish_too_large(repo, base, scope, paths, reason, started, sha, subject)
        return 0
    if decision == "skip-failed":
        log_line(FAILED_RETRY_LOG)
        write_idle_canvas(repo, sha, subject, ui["retry_hold"], ui["retry_note"])
        return 0
    pre_status = ""
    if project.pre_review:
        log_line("pre-review: running")
        pre_status = run_pre_review(repo, project.pre_review)
        log_line(f"pre-review: {pre_status}")
    else:
        log_line("pre-review: skip (empty command)")
    branch_scope = os.environ.get("REVIEW_CHANGE_SCOPE", "commit").strip().lower() == "branch"
    write_pending_canvas(repo, sha, subject, branch, started_at, pre_status, branch_scope)
    clear_last(repo)
    clear_analysis(repo)
    context = build_context(repo, base, scope, project)
    brief = write_brief(repo, context)
    log_line(f"brief {brief.name}")
    prompt = build_analysis_prompt(repo, base, paths, scope, context)
    log_line(f"analysing commit {sha} model={model} prompt_bytes={len(prompt.encode('utf-8'))}")
    ok = run_analysis(repo, prompt, model, paths)
    if analysis_file(repo).is_file():
        try:
            verify_proposed_fixes(repo, log=log_line)
        except Exception as exc:  # noqa: BLE001
            log_line(f"fix verify failed: {exc}")
        try:
            from engine.tool_layer import apply_tool_owned_reviews, load_tool_layer

            stamped = apply_tool_owned_reviews(repo, load_tool_layer(repo, scope, project))
            if stamped:
                log_line("tool links reviewed: " + ", ".join(stamped))
        except Exception as exc:  # noqa: BLE001
            log_line(f"tool layer stamp failed: {exc}")
    merged = merge_analysis(repo)
    stamp_fixes_blobs(repo)
    expected, reviewed = merge_last(repo, base, scope)
    log_line(
        f"ledger merged: expected={expected} reviewed={reviewed} "
        f"fix files={len(merged.get('files') or [])} verdict={merged.get('verdict') or '?'}"
    )
    if not ok:
        log_line("analysis failed — same blobs are skipped unless REVIEW_CHANGE_RETRY=1")
        write_idle_canvas(repo, sha, subject, ui["incomplete_pill"], ui["incomplete_note"])
        return 0
    finished = datetime.now().strftime("%H:%M:%S")
    elapsed = _format_elapsed(time.monotonic() - started)
    log_line(f"finished {finished} — took {elapsed}")
    try:
        write_canvas(
            repo,
            merged,
            {"finished": finished, "elapsed": elapsed, "pre_review": pre_status},
        )
        log_line(f"wrote {CANVAS_NAME} (script)")
    except Exception as exc:  # noqa: BLE001 — model fallback renders the canvas
        log_line(f"canvas render failed: {exc} — fallback {model}")
        review_once(repo, build_canvas_prompt(repo, base, scope, merged), model)
    finish_review_wait_for_click(repo)
    return 0


def _prompt_has(prompt: str, needle: str) -> bool:
    return bool(needle) and needle in prompt


def dry_run(repo: Path) -> int:
    project = load_review_project(repo)
    warning = warn_version(repo)
    base = detect_base(repo)
    scope = scope_ref(repo, base)
    work = work_set(repo, base, scope)
    paths = list(work["in_scope"])
    context = build_context(repo, base, scope, project)
    prompt = build_analysis_prompt(repo, base, paths, scope, context)
    ui = review_ui(repo)
    source = (
        ".agent-tools/config.md"
        if project.config_source == "config"
        else "PROJECT.md (legacy)"
    )
    print(f"version: {package_version()}")
    print(f"stack: {project.stack}")
    print(f"config_source: {source}")
    print(f"opted_out: {'yes' if project.opted_out else 'no'}")
    print(f"quality_bar_bytes: {len(project.rules.encode('utf-8'))}")
    print(f"language: {project.language}")
    print(f"canvas_language: {ui['note_language']}")
    if project.labels:
        print("labels: " + " / ".join(project.labels))
    else:
        print(f"labels: {ui['approve']} / {ui['approve_nits']} / {ui['request_changes']}")
    print(f"model: {resolve_model(repo)}")
    print(f"base: {base}")
    print(f"scope: {scope}")
    print(f"in_scope: {len(paths)}")
    print(f"verified: {len(work['verified'])}")
    print(f"pre_review: {project.pre_review or '(empty)'}")
    print(f"test_file_command: {project.test_file_command or '(empty)'}")
    from engine.host import extra_trigger_dirs, new_mr_url
    from engine.ledger import resolve_git_host

    host, git_project = resolve_git_host(repo, project)
    print(f"git_host: {host or '(empty)'}")
    print(f"git_project: {git_project or '(empty)'}")
    print(f"new_mr_url_sample: {new_mr_url(repo) or '(empty)'}")
    print("trigger_dirs: " + ", ".join(str(path) for path in extra_trigger_dirs(repo)))
    print("test_paths: " + ",".join(project.test_paths))
    if context.test_edits:
        print("test_edits: " + "; ".join(context.test_edits))
    else:
        print("test_edits: (none)")
    if context.index_slugs:
        print("index_slugs: " + ", ".join(context.index_slugs))
    else:
        print("index_slugs: (none)")
    for line in context.index_lines:
        print(f"index_in_prompt: {'yes' if _prompt_has(prompt, line) else 'no'} {line}")
    if context.note_paths:
        print("notes: " + ", ".join(context.note_paths))
    else:
        print("notes: (none)")
    for rel, excerpt in context.note_excerpts:
        print(f"note_in_prompt: {rel} {'yes' if _prompt_has(prompt, excerpt) else 'no'}")
    from engine.tool_layer import load_tool_layer

    layer = load_tool_layer(repo, scope, project)
    print(f"agent_layer: {layer.mode or '(empty)'}")
    print(f"local_links: {len(layer.links)}")
    print(f"vendored: {len(layer.vendored)}")
    print(f"local_layer_in_prompt: {'yes' if '## Local tool layer' in prompt else 'no'}")
    print("shards: none")
    print(f"prompt_bytes: {len(prompt.encode('utf-8'))}")
    if warning:
        print(f"version_warning: {warning}")
    else:
        print("version_warning: (none)")
    if any(token in prompt for token in ("plan_shards", "MAX_SHARDS", "ThreadPoolExecutor")):
        print("shard_plan_in_prompt: yes")
    else:
        print("shard_plan_in_prompt: no")
    return 0


def run_demo() -> int:
    cwd = Path.cwd()
    demo = cwd if (cwd / "analysis.json").is_file() else None
    if demo is None:
        bundled = Path(__file__).resolve().parents[1] / "examples" / "demo"
        if (bundled / "analysis.json").is_file():
            demo = bundled
    if demo is None:
        tell("merge-reviewer: no analysis.json for --demo")
        return 1
    payload = json.loads((demo / "analysis.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        tell("merge-reviewer: analysis.json must be an object")
        return 1
    meta = dict(payload.get("demo") or {})
    meta["demo"] = True
    body = render_canvas_tsx(demo, payload, meta)
    from engine.canvas import canvas_paths_for_repo

    written: Path | None = None
    for path in canvas_paths_for_repo(demo):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        written = path
    if written is None:
        tell("merge-reviewer: could not write the demo canvas")
        return 1
    print(f"canvas: {written}")
    print(f"filediff: {'yes' if '<FileDiff' in body else 'no'}")
    return 0


def main() -> int:
    from engine.host import load_cursor_env, prime_triggers, push_and_open_mr, spawn_background

    try:
        if "--version" in sys.argv:
            print(package_version())
            return 0
        if "--demo" in sys.argv:
            return run_demo()
        if "--prime-triggers" in sys.argv:
            try:
                repo = repo_root()
            except (subprocess.CalledProcessError, OSError):
                tell("merge-reviewer: not a git repository")
                return 1
            prime_triggers(repo)
            tell(f"merge-reviewer: primed triggers under {repo / '.cursor'}")
            return 0
        if "--dry-run" in sys.argv:
            load_cursor_env()
            return dry_run(repo_root())
        if "--create-mr" in sys.argv:
            load_cursor_env()
            push_and_open_mr(repo_root())
            return 0
        if "--run" in sys.argv:
            load_cursor_env()
            return run()
        load_cursor_env()
        repo = repo_root()
        project = load_review_project(repo)
        if not project.enabled:
            return 0
        reason = should_skip(repo)
        if reason:
            tell(f"merge-reviewer: skip ({reason})")
            return 0
        return spawn_background(repo, resolve_model(repo))
    except Exception as exc:  # noqa: BLE001 — the hook must not fail the commit
        if any(flag in sys.argv for flag in ("--dry-run", "--demo", "--version", "--prime-triggers")):
            tell(f"merge-reviewer: {exc}")
            return 1
        tell(f"merge-reviewer: {exc}")
        return 0
