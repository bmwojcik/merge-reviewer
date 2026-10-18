"""Build the review prompt context: diff preview, test edits, inlined docs.

The model must not spend turns discovering docs. Matching INDEX rows and note
files are inlined here, bounded in size. With no INDEX and no notes, the diff
stands alone.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.paths import DOC_BUDGET, NOTE_BUDGET, git_ok
from engine.project import ReviewProject, load_review_project

_INDEX_CELL = re.compile(r"`([^`]+)`")
_VENDOR_MODULE = re.compile(r"[A-Z][A-Za-z0-9]+_[A-Z][A-Za-z0-9_]+")


@dataclass
class ReviewContext:
    preview: str
    knowledge: str
    index_slugs: list[str] = field(default_factory=list)
    index_lines: list[str] = field(default_factory=list)
    note_paths: list[str] = field(default_factory=list)
    note_excerpts: list[tuple[str, str]] = field(default_factory=list)
    test_edits: list[str] = field(default_factory=list)


def matches_glob(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.startswith("**/"):
        rest = pattern[3:]
        if fnmatch.fnmatch(path, rest) or fnmatch.fnmatch(path, "**/" + rest):
            return True
    return False


def is_test_path(path: str, globs: tuple[str, ...]) -> bool:
    return any(matches_glob(path, pattern) for pattern in globs)


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[truncated]\n"


def _index_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        slug = cells[0].strip("`").strip()
        if not slug or slug.lower() == "slug" or set(slug) <= {"-", ":"}:
            continue
        rows.append(
            {
                "slug": slug,
                "modules": cells[2] if len(cells) > 2 else "",
                "keywords": cells[3] if len(cells) > 3 else "",
                "raw": line,
            }
        )
    return rows


def _needles(row: dict[str, str]) -> list[str]:
    needles: list[str] = []
    blob = f"{row.get('modules', '')} {row.get('keywords', '')}"
    for token in _INDEX_CELL.findall(blob):
        token = token.strip()
        if not token or token == "—":
            continue
        if "/" in token:
            needles.append(token)
            continue
        if _VENDOR_MODULE.fullmatch(token):
            vendor, module = token.split("_", 1)
            needles.append(f"app/code/{vendor}/{module}")
    return needles


def _row_matches(row: dict[str, str], paths: list[str]) -> bool:
    slug = row.get("slug") or ""
    needles = _needles(row)
    for path in paths:
        pure = Path(path)
        if slug and (pure.stem == slug or slug in pure.parts):
            return True
        for needle in needles:
            if needle and needle in path.replace("\\", "/"):
                return True
    return False


def _numstat(repo: Path, scope: str, paths: list[str]) -> dict[str, tuple[str, str]]:
    if not paths:
        return {}
    out = git_ok(repo, "diff", "--numstat", f"{scope}...HEAD", "--", *paths)
    stats: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        stats[cols[-1]] = (cols[0], cols[1])
    return stats


def _unit_of(path: str) -> str:
    parts = Path(path).parts
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else path


def matching_index(repo: Path, paths: list[str]) -> tuple[list[dict[str, str]], bool]:
    index_path = repo / "docs" / "features" / "INDEX.md"
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return [], False
    rows = [row for row in _index_rows(text) if _row_matches(row, paths)]
    return rows, True


def _note_paths(repo: Path, rows: list[dict[str, str]]) -> list[str]:
    found: list[str] = []
    general = "docs/notes/_general.md"
    if (repo / general).is_file():
        found.append(general)
    for row in rows:
        rel = f"docs/notes/{row['slug']}.md"
        if (repo / rel).is_file() and rel not in found:
            found.append(rel)
    return found


def knowledge_block(repo: Path, paths: list[str]) -> tuple[str, list[dict[str, str]], list[str], list[tuple[str, str]]]:
    rows, index_exists = matching_index(repo, paths)
    notes = _note_paths(repo, rows)
    if not index_exists and not notes:
        return "", [], [], []
    parts: list[str] = [
        "## Project knowledge (inlined by the script — do not open these files)",
        "",
    ]
    excerpts: list[tuple[str, str]] = []
    parts.append("### Matching INDEX rows")
    if not index_exists:
        parts.append("(no docs/features/INDEX.md)")
    elif rows:
        parts.extend(row["raw"] for row in rows)
    else:
        parts.append("(no INDEX row matched this diff)")
    parts.append("")
    if notes:
        remaining = DOC_BUDGET
        for rel in notes:
            try:
                body = (repo / rel).read_text(encoding="utf-8")
            except OSError:
                continue
            clipped = _bound(body, min(NOTE_BUDGET, max(0, remaining)))
            remaining -= len(clipped)
            excerpt = ""
            for note_line in clipped.splitlines():
                stripped = note_line.strip()
                if stripped:
                    excerpt = stripped[:80]
                    break
            excerpts.append((rel, excerpt))
            parts.append(f"### {rel}")
            parts.append(clipped.rstrip())
            parts.append("")
            if remaining <= 0:
                parts.append("[truncated]")
                break
    elif index_exists:
        parts.append("### Notes")
        parts.append("(none)")
    text = _bound("\n".join(parts).rstrip() + "\n", DOC_BUDGET)
    return text, rows, notes, excerpts


def _test_edits(repo: Path, scope: str, globs: tuple[str, ...]) -> list[str]:
    out = git_ok(repo, "diff", "--numstat", f"{scope}...HEAD")
    edits: list[str] = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        deleted = cols[1]
        path = cols[-1]
        if deleted in {"0", "-"}:
            continue
        if is_test_path(path, globs):
            edits.append(f"- {path} (+{cols[0]} -{deleted})")
    return edits


def build_context(repo: Path, base: str, scope: str, project: ReviewProject | None = None) -> ReviewContext:
    project = project or load_review_project(repo)
    diff_ref = scope or base
    subject = git_ok(repo, "log", "-1", "--pretty=%s")
    body = git_ok(repo, "log", "-1", "--pretty=%b")
    branch = git_ok(repo, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    ahead = git_ok(repo, "rev-list", "--count", f"{base}..HEAD") if base != "HEAD" else ""
    behind = git_ok(repo, "rev-list", "--count", f"HEAD..{base}") if base != "HEAD" else ""
    names = changed_files_safe(repo, diff_ref)
    stats = _numstat(repo, diff_ref, names)
    knowledge, rows, notes, excerpts = knowledge_block(repo, names)
    test_edits = _test_edits(repo, diff_ref, project.test_paths)
    remote = git_ok(repo, "remote", "get-url", "origin")
    lines: list[str] = [
        "# Merge-request preview",
        "",
        f"- remote: {remote or 'unknown'}",
        f"- source: {branch}",
        f"- target_ref: {base}",
        f"- scope_ref: {diff_ref}",
        f"- ahead: {ahead or '0'}",
        f"- behind: {behind or '0'}",
        "",
        "## Status",
        git_ok(repo, "status", "-sb") or "(clean)",
        "",
        f"## Commits ({base}..HEAD)",
        git_ok(repo, "log", "--oneline", f"{base}..HEAD") or "(none)",
        "",
        "## Subject",
        subject or "(none)",
        "",
        "## Body",
        body or "(none)",
        "",
        f"## Files ({diff_ref}...HEAD)",
    ]
    if not names:
        lines.append("- (none)")
    groups: dict[str, list[str]] = {}
    for path in names:
        groups.setdefault(_unit_of(path), []).append(path)
    for unit, unit_paths in groups.items():
        lines.append(f"### {unit}")
        for path in unit_paths:
            added, deleted = stats.get(path, ("0", "0"))
            lines.append(f"- {path} (+{added} -{deleted})")
    diff = git_diff_text(repo, diff_ref)
    budget = _inline_budget()
    lines.append("")
    if len(diff.encode("utf-8")) <= budget:
        lines.append(f"## Full diff ({diff_ref}...HEAD) — inline, do not re-run git diff for these files")
        lines.append("```diff")
        lines.append(diff)
        lines.append("```")
    else:
        lines.append(f"## How to read (diff {len(diff.encode('utf-8'))} B too big to inline)")
        lines.append(f"Pull hunks per file with `git diff {diff_ref}...HEAD -- <path>`.")
        lines.append("Review only the files under '## Review ledger → In scope'.")
    lines.append("")
    lines.append("## Test edits (non-additive — inspect assertions and expected values)")
    if test_edits:
        lines.extend(test_edits)
    else:
        lines.append("- (no non-additive test edits)")
    lines.append("")
    lines.append("## Uncommitted (not in the request unless committed)")
    unstaged = git_ok(repo, "diff", "--name-only")
    staged = git_ok(repo, "diff", "--name-only", "--cached")
    lines.append(unstaged or "(none)")
    if staged:
        lines.append(staged)
    preview = "\n".join(lines).rstrip() + "\n"
    return ReviewContext(
        preview=preview,
        knowledge=knowledge,
        index_slugs=[row["slug"] for row in rows],
        index_lines=[row["raw"] for row in rows],
        note_paths=notes,
        note_excerpts=excerpts,
        test_edits=test_edits,
    )


def changed_files_safe(repo: Path, scope: str) -> list[str]:
    out = git_ok(repo, "diff", "--name-only", f"{scope}...HEAD")
    return [line for line in out.splitlines() if line.strip()]


def git_diff_text(repo: Path, scope: str) -> str:
    return git_ok(repo, "diff", "--no-color", "-U20", f"{scope}...HEAD")


def _inline_budget() -> int:
    raw = os.environ.get("REVIEW_CHANGE_INLINE_DIFF_BYTES", "").strip()
    if not raw:
        return 96000
    try:
        value = int(raw)
    except ValueError:
        return 96000
    return value if value > 0 else 96000


def write_brief(repo: Path, context: ReviewContext) -> Path:
    from engine.paths import BRIEF_NAME, var_path

    path = var_path(repo, BRIEF_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = context.knowledge or "(no matching project docs)\n"
    path.write_text(body, encoding="utf-8")
    return path
