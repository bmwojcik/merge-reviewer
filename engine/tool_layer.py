"""Local tool symlinks the reviewer must not treat as lost project files.

Managed exclude blocks list the paths. A path counts only when it is a symlink
into cursor-template or merge-reviewer. The block is added to the prompt only
when Agent layer is committed. In local mode the whole layer stays off git.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from engine.ledger import _load_json, _write_json, analysis_file
from engine.paths import git_ok
from engine.project import ReviewProject, git_dir

LAYER_BUDGET = 12_000
_BLOCK_NAMES = ("cursor-template", "merge-reviewer", "agent-layer")
_TOOL_PARTS = {"cursor-template", "merge-reviewer"}


@dataclass(frozen=True)
class ToolLayer:
    mode: str
    keep_local: tuple[str, ...]
    links: tuple[str, ...]
    vendored: tuple[str, ...]
    deleted_links: tuple[str, ...]

    @property
    def in_prompt(self) -> bool:
        return self.mode == "committed"


def _norm(path: str) -> str:
    text = path.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _exclude_text(repo: Path) -> str:
    directory = git_dir(repo)
    if directory is None:
        return ""
    return _read(directory / "info" / "exclude")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _exclude_paths(text: str) -> list[str]:
    lines = text.splitlines()
    found: list[str] = []
    active = ""
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("# >>> "):
            name = stripped[len("# >>> ") :].split()[0].strip()
            active = name if name in _BLOCK_NAMES else ""
            continue
        if stripped.startswith("# <<< ") and active:
            active = ""
            continue
        if not active or not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        if any(char in stripped for char in "*?["):
            continue
        rel = _norm(stripped)
        if rel and rel not in found:
            found.append(rel)
    return found


def _tool_homes() -> list[Path]:
    homes: list[Path] = []
    for name in ("MERGE_REVIEWER_HOME", "CURSOR_TEMPLATE_HOME"):
        raw = os.environ.get(name, "").strip()
        if raw:
            homes.append(Path(raw).expanduser())
    return homes


def _points_at_tools(path: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if _TOOL_PARTS.intersection(resolved.parts):
        return True
    for home in _tool_homes():
        try:
            resolved.relative_to(home.resolve())
        except (OSError, ValueError):
            continue
        return True
    return False


def local_tool_links(repo: Path) -> list[str]:
    links: list[str] = []
    for rel in _exclude_paths(_exclude_text(repo)):
        path = repo / rel
        if _points_at_tools(path):
            links.append(rel)
    return links


def vendored_paths(repo: Path) -> list[str]:
    path = repo / ".agent-tools" / "vendored.lock"
    if not path.is_file():
        return []
    text = _read(path).strip()
    if not text:
        return []
    parsed: list[str] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    rows: list[object]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        raw = data.get("paths", data.get("files"))
        rows = list(raw) if isinstance(raw, list) else list(data.keys())
    else:
        rows = []
        for line in text.splitlines():
            piece = line.split("#", 1)[0].strip()
            if piece:
                rows.append(piece.split()[0])
    for item in rows:
        rel = _norm(str(item))
        if rel and not rel.startswith("_") and rel not in parsed:
            parsed.append(rel)
    return parsed


def _deleted(repo: Path, scope: str) -> list[str]:
    out = git_ok(repo, "diff", "--diff-filter=D", "--name-only", f"{scope}...HEAD")
    return [_norm(line) for line in out.splitlines() if line.strip()]


def _owned(rel: str, links: tuple[str, ...]) -> bool:
    for link in links:
        if rel == link or rel.startswith(link.rstrip("/") + "/"):
            return True
    return False


def load_tool_layer(repo: Path, scope: str, project: ReviewProject) -> ToolLayer:
    links = tuple(local_tool_links(repo))
    deleted = tuple(path for path in _deleted(repo, scope) if _owned(path, links))
    return ToolLayer(
        mode=project.agent_layer,
        keep_local=project.keep_local,
        links=links,
        vendored=tuple(vendored_paths(repo)),
        deleted_links=deleted if project.agent_layer == "committed" else (),
    )


def render_local_layer(layer: ToolLayer) -> str:
    if not layer.in_prompt:
        return ""
    lines = [
        "## Local tool layer",
        "",
        f"Agent layer: {layer.mode}",
        "Keep local (committed on purpose, not a tool link):",
    ]
    lines.extend(f"- {path}" for path in layer.keep_local)
    if not layer.keep_local:
        lines.append("- (none)")
    lines.append("")
    lines.append(
        "Local symlinks on this laptop (the target lives in cursor-template or merge-reviewer):"
    )
    lines.extend(f"- {path}" for path in layer.links)
    if not layer.links:
        lines.append("- (none)")
    lines.append("")
    lines.append("Vendored paths (`.agent-tools/vendored.lock`, present in git for teammates):")
    lines.extend(f"- {path}" for path in layer.vendored)
    if not layer.vendored:
        lines.append("- (none)")
    if layer.deleted_links:
        lines.append("")
        lines.append("Deleted in this commit and now a local link (not lost on this laptop):")
        lines.extend(f"- {path}" for path in layer.deleted_links)
    lines.extend(
        [
            "",
            "Rules:",
            "- A deleted tracked file that is now one of these local symlinks, or a file inside one, "
            "is not missing for the author of this laptop. Do not flag it as lost. Do not review "
            "the deleted blob as the current text of that file.",
            "- The script records each such deleted path under `reviewed` with empty findings. "
            "Leave those findings empty if you list the path.",
            "- Agent layer is committed, so a teammate without the tools will not have the symlinks. "
            "If a committed file in this diff tells the reader to read or run a path that exists only "
            "as a local symlink (not Keep local, not vendored, and not still tracked), add a finding "
            "with category `team-ref-to-local`. Severity is nit, unless that reference is a mandatory "
            "instruction, then blocking.",
            "- Do not use `team-ref-to-local` for Keep local paths or vendored paths.",
            "",
        ]
    )
    text = "\n".join(lines)
    if len(text) <= LAYER_BUDGET:
        return text
    return text[:LAYER_BUDGET].rstrip() + "\n\n[truncated]\n"


def apply_tool_owned_reviews(repo: Path, layer: ToolLayer) -> list[str]:
    """Mark deleted tool links reviewed with no notes. Other deletions stay for the model."""
    if not layer.deleted_links:
        return []
    path = analysis_file(repo)
    data = _load_json(path)
    if not isinstance(data, dict) or data.get("pass_failed"):
        return []
    owned = {_norm(item) for item in layer.deleted_links}
    reviewed = [item for item in (data.get("reviewed") or []) if isinstance(item, dict)]
    seen: set[str] = set()
    for item in reviewed:
        rel = _norm(str(item.get("path") or ""))
        if rel in owned:
            item["path"] = rel
            item["findings"] = []
            seen.add(rel)
    for rel in layer.deleted_links:
        if rel not in seen:
            reviewed.append({"path": rel, "findings": []})
    kept_notes: list[str] = []
    for raw in data.get("not_reviewed") or []:
        rel = _norm(str(raw or "").split(" ", 1)[0])
        if rel not in owned:
            kept_notes.append(str(raw))
    data["reviewed"] = reviewed
    data["not_reviewed"] = kept_notes
    files = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        if _norm(str(item.get("path") or "")) in owned:
            continue
        files.append(item)
    data["files"] = files
    _write_json(path, data)
    return list(layer.deleted_links)
