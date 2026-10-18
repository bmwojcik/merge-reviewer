"""Read review settings and project guidance.

Tool settings live in `.agent-tools/config.md` (`- **Key:** value`, HTML comments
only). If that file is missing, the same keys are read from `PROJECT.md` and one
migration line is logged. Blocking rules are `## Quality bar` in `PROJECT.md`
(`## Review rules` when Quality bar is absent). `## Agent rules` is extra context.
On/off is `<git-dir>/agent-tools.local`, not a committed file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from engine.paths import git_ok, tell

_EMPTY_VALUES = {"", "none", "leave empty", "-", "—", "–", "(empty)", "empty", "replace_me"}
_POLISH = {"polish", "polski", "pl"}
_OFF = {"off", "false", "no", "0", "disabled"}
_STACKS = {"magento2", "shopify", "generic"}
DEFAULT_TEST_PATHS = ("**/Test/**", "**/tests/**", "**/test/**")
AGENT_RULES_BUDGET = 8_000
LEGACY_SETTINGS_LOG = (
    "merge-reviewer: settings still in PROJECT.md — "
    "run cursor-template install.sh --migrate-config"
)
IGNORED_SWITCH_LOG = (
    "merge-reviewer: ignoring Merge reviewer in committed settings — "
    "opt out in .git/agent-tools.local"
)
_LOGGED: set[str] = set()


@dataclass(frozen=True)
class ReviewProject:
    stack: str
    enabled: bool
    opted_out: bool
    config_source: str
    min_version: str
    language: str
    labels: tuple[str, str, str] | None
    pre_review: str
    test_file_command: str
    test_paths: tuple[str, ...]
    base_branch: str
    git_host: str
    git_project: str
    model: str
    agent_layer: str
    keep_local: tuple[str, ...]
    rules: str
    agent_rules: str
    skip_branches: tuple[str, ...] = ()
    base_suffixes: tuple[tuple[str, str], ...] = ()
    legacy_base: str = ""


def _is_explanatory_comment(text: str) -> bool:
    """True when text after ` — ` explains the value rather than continuing a name."""
    comment = text.strip()
    if not comment:
        return False
    if comment[0].islower() or comment[0] in "(`'\"":
        return True
    if any(mark in comment for mark in (".", ",", ";", ":")):
        return True
    return len(comment.split()) >= 3


def _strip_explanatory(raw: str) -> str:
    for sep in (" — ", " – "):
        if sep not in raw:
            continue
        left, right = raw.split(sep, 1)
        if _is_explanatory_comment(right):
            return left.strip()
        return raw
    return raw


def _is_dash_comment(raw: str) -> bool:
    if not raw:
        return False
    if raw[0] in {"—", "–"}:
        return True
    return raw == "-" or raw.startswith("- ")


def _field_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("`"):
        end = raw.find("`", 1)
        raw = raw[1:end].strip() if end > 1 else raw.strip("`")
    else:
        raw = _strip_explanatory(raw).strip("`").strip()
    if _is_dash_comment(raw):
        return ""
    lowered = raw.lower().rstrip(".")
    if (
        lowered in _EMPTY_VALUES
        or lowered.startswith("leave empty")
        or lowered.startswith("empty")
        or lowered.startswith("(empty)")
    ):
        return ""
    return raw


def _normalize_language(raw: str) -> str:
    if raw.strip().lower() in _POLISH:
        return "pl"
    return "en"


def _parse_labels(raw: str) -> tuple[str, str, str] | None:
    if not raw:
        return None
    parts = [part.strip() for part in (raw.split(" / ") if " / " in raw else raw.split("/"))]
    parts = [part for part in parts if part]
    if len(parts) != 3:
        return None
    return (parts[0], parts[1], parts[2])


def _parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parse_suffixes(raw: str) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for part in raw.split(","):
        piece = part.strip()
        if "=" not in piece:
            continue
        suffix, ref = piece.split("=", 1)
        suffix, ref = suffix.strip(), ref.strip()
        if suffix and ref:
            pairs.append((suffix, ref))
    return tuple(pairs)


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _log_once(key: str, message: str) -> None:
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    tell(message)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _fields(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in _strip_html_comments(text).splitlines():
        match = re.match(r"^\s*-\s+\*\*([^*]+)\*\*:?\s*(.*)$", line)
        if not match:
            continue
        key = re.sub(r"\s+", " ", match.group(1).strip().lower()).rstrip(":")
        found[key] = _field_value(match.group(2))
    return found


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[truncated]\n"


def git_dir(repo: Path) -> Path | None:
    raw = git_ok(repo, "rev-parse", "--git-dir")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (repo / path).resolve()
    return path


def is_opted_out(repo: Path) -> bool:
    """True when `<git-dir>/agent-tools.local` sets Merge reviewer to off."""
    directory = git_dir(repo)
    if directory is None:
        return False
    fields = _fields(_read_text(directory / "agent-tools.local"))
    return fields.get("merge reviewer", "").strip().lower() in _OFF


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start: int | None = None
    wanted = heading.strip().lower()
    for index, line in enumerate(lines):
        if line.strip().lower() == wanted:
            start = index + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def _legacy_base(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().lower().startswith("## default git base"):
            continue
        for follow in lines[index + 1 : index + 8]:
            stripped = follow.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("```"):
                continue
            if stripped.startswith("-"):
                continue
            return _field_value(stripped)
        return ""
    return ""


def _normalize_stack(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"magento", "magento2"}:
        return "magento2"
    if value == "shopify":
        return "shopify"
    if value in _STACKS:
        return value
    return "generic"


def version_tuple(raw: str) -> tuple[int, ...]:
    text = raw.strip().lstrip("vV")
    parts: list[int] = []
    for piece in text.split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def version_older(installed: str, required: str) -> bool:
    left = version_tuple(installed)
    right = version_tuple(required)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left < right


def load_review_project(repo: Path) -> ReviewProject:
    project_text = _read_text(repo / "PROJECT.md")
    config_path = repo / ".agent-tools" / "config.md"
    if config_path.is_file():
        settings_text = _read_text(config_path)
        source = "config"
    else:
        settings_text = project_text
        source = "legacy"
        _log_once("legacy-settings", LEGACY_SETTINGS_LOG)
    fields = _fields(settings_text)
    project_fields = _fields(project_text)
    if "merge reviewer" in fields or "merge reviewer" in project_fields:
        _log_once("ignored-switch", IGNORED_SWITCH_LOG)
    guidance = _strip_html_comments(project_text)
    quality = _section(guidance, "## Quality bar") or _section(guidance, "## Review rules")
    stack_raw = fields.get("stack") or fields.get("kind") or "generic"
    test_raw = fields.get("test paths", "")
    test_paths = _parse_csv(test_raw) if test_raw else DEFAULT_TEST_PATHS
    opted_out = is_opted_out(repo)
    return ReviewProject(
        stack=_normalize_stack(stack_raw),
        enabled=not opted_out,
        opted_out=opted_out,
        config_source=source,
        min_version=fields.get("merge reviewer version", ""),
        language=_normalize_language(fields.get("review language", "")),
        labels=_parse_labels(fields.get("review canvas labels", "")),
        pre_review=fields.get("pre-review command", ""),
        test_file_command=fields.get("test file command", ""),
        test_paths=test_paths,
        base_branch=fields.get("base branch", ""),
        git_host=fields.get("git host") or fields.get("review_change_git_host", ""),
        git_project=fields.get("git project") or fields.get("review_change_project", ""),
        model=fields.get("review model", ""),
        agent_layer=fields.get("agent layer", "").strip().lower(),
        keep_local=_parse_csv(fields.get("keep local", "")),
        rules=quality,
        agent_rules=_bound(_section(guidance, "## Agent rules"), AGENT_RULES_BUDGET),
        skip_branches=_parse_csv(fields.get("skip branches", "")),
        base_suffixes=_parse_suffixes(fields.get("git base suffixes", "")),
        legacy_base=_legacy_base(_strip_html_comments(settings_text)),
    )
