"""Deterministic canvas. The model does not render it."""

from __future__ import annotations

import json
import os
from pathlib import Path

from engine.paths import (
    APPLY_TRIGGER_NAME,
    CANVAS_NAME,
    MR_TRIGGER_NAME,
    SKIP_TRIGGER_NAME,
    git,
)
from engine.project import load_review_project

_UI: dict[str, dict[str, str]] = {
    "en": {
        "h1": "Review preview",
        "in_progress": "in progress",
        "stage": "Stage",
        "start": "Start",
        "commit": "Commit",
        "verdict": "Verdict",
        "blocking_notes": "Blocking / notes",
        "findings": "Findings",
        "blocking": "Blocking",
        "notes": "Notes",
        "proposed": "Proposed fixes",
        "mr_draft": "Merge request draft",
        "mr_title": "Title",
        "mr_body": "Description",
        "apply": "Apply fixes",
        "skip": "Skip fixes",
        "create_mr": "Create merge request",
        "approve": "Approved",
        "approve_nits": "Approved with notes",
        "request_changes": "Needs changes",
        "approve_partial": "Approved (some skipped)",
        "incomplete": "Analysis incomplete",
        "incomplete_body": (
            "The run returned no verdict — the review may have been interrupted. "
            "Commit again or check var/review-change.log."
        ),
        "skipped_title": "Skipped (will be re-reviewed)",
        "no_changes": "No changes",
        "no_changes_note": "Nothing changed since the last verified pass — nothing to review.",
        "incomplete_pill": "Incomplete",
        "incomplete_note": (
            "Analysis did not start or crashed — check var/review-change.log. "
            "Files return to review on the next commit."
        ),
        "finished": "Finished",
        "took": "took",
        "log_line": "Log: var/review-change.log in this repo",
        "pending_scope": "This commit is under review. The canvas updates when the reviewer finishes.",
        "pending_scope_branch": (
            "The whole branch is under review. The canvas updates when the reviewer finishes."
        ),
        "manual_before": "If the paste does not land in chat, type ",
        "manual_after": " in this agent window.",
        "pre_review": "Pre-review",
        "note_language": "English",
        "on_branch": "on",
        "too_large": "Too large",
        "retry_hold": "Not retried",
        "retry_note": (
            "The previous pass failed on these identical files. "
            "Amend the commit or set REVIEW_CHANGE_RETRY=1."
        ),
    },
    "pl": {
        "h1": "Podgląd review",
        "in_progress": "w toku",
        "stage": "Etap",
        "start": "Start",
        "commit": "Commit",
        "verdict": "Werdykt",
        "blocking_notes": "Blokujące / drobne",
        "findings": "Znaleziska",
        "blocking": "Blokujące",
        "notes": "Drobne",
        "proposed": "Propozycje poprawek",
        "mr_draft": "Szkic merge requestu",
        "mr_title": "Tytuł",
        "mr_body": "Opis",
        "apply": "Nałóż poprawki",
        "skip": "Olej poprawki",
        "create_mr": "Utwórz MR",
        "approve": "Zaakceptowane",
        "approve_nits": "Zaakceptowane z uwagami",
        "request_changes": "Wymaga poprawek",
        "approve_partial": "Zaakceptowane (część pominięta)",
        "incomplete": "Analiza niekompletna",
        "incomplete_body": (
            "Przebieg nie zwrócił werdyktu — recenzja mogła zostać przerwana. "
            "Zrób commit ponownie albo sprawdź var/review-change.log."
        ),
        "skipped_title": "Pominięte (wrócą do recenzji)",
        "no_changes": "Brak zmian",
        "no_changes_note": "Nic nie zmienione od ostatniej zweryfikowanej recenzji — nic do sprawdzenia.",
        "incomplete_pill": "Niekompletna",
        "incomplete_note": (
            "Analiza nie wystartowała lub padła — sprawdź var/review-change.log. "
            "Pliki wrócą do recenzji przy następnym commicie."
        ),
        "finished": "Zakończono",
        "took": "trwało",
        "log_line": "Log: var/review-change.log",
        "pending_scope": "Ten commit jest w recenzji. Canvas zaktualizuje się, gdy recenzja się skończy.",
        "pending_scope_branch": (
            "Cała gałąź jest w recenzji. Canvas zaktualizuje się, gdy recenzja się skończy."
        ),
        "manual_before": "Jeśli wklejenie nie trafi do czatu, wpisz ",
        "manual_after": " w tym oknie agenta.",
        "pre_review": "Przed recenzją",
        "note_language": "Polish",
        "on_branch": "na",
        "too_large": "Za duży commit",
        "retry_hold": "Bez ponowienia",
        "retry_note": (
            "Poprzednie przejście padło na tych samych plikach. "
            "Zmień commit albo ustaw REVIEW_CHANGE_RETRY=1."
        ),
    },
}

_VERDICT_TONE = {
    "approve": None,
    "approve-with-nits": "warning",
    "request-changes": "danger",
}

_CANVAS_HEAD = """import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Code,
  DiffStats,
  DiffView,
  Grid,
  H1,
  H2,
  Pill,
  Row,
  Stack,
  Stat,
  Text,
  useCanvasAction,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

type DiffLine = {
  type: "added" | "removed" | "unchanged";
  content: string;
  lineNumber?: number;
};

function FileDiff({
  path,
  startLine,
  additions,
  deletions,
  lines,
}: {
  path: string;
  startLine: number;
  additions: number;
  deletions: number;
  lines: DiffLine[];
}) {
  const dispatch = useCanvasAction();
  return (
    <Card collapsible defaultOpen>
      <CardHeader trailing={<DiffStats additions={additions} deletions={deletions} />}>
        <Button
          variant="ghost"
          onClick={() =>
            dispatch({
              type: "openFile",
              path,
              selection: { startLineNumber: startLine },
            })
          }
        >
          {path}
        </Button>
      </CardHeader>
      <CardBody style={{ padding: 0 }}>
        <DiffView path={path} lines={lines} />
      </CardBody>
    </Card>
  );
}

export default function ReviewChangePreview() {
  const theme = useHostTheme();
  const dispatch = useCanvasAction();
  const [applyTick, setApplyTick] = useCanvasState("applyTick", 1);

  return (
"""

_CANVAS_TAIL = """  );
}
"""


def review_ui(repo: Path) -> dict[str, str]:
    project = load_review_project(repo)
    ui = dict(_UI["pl"] if project.language == "pl" else _UI["en"])
    if project.labels:
        approve, nits, changes = project.labels
        ui["approve"] = approve
        ui["approve_nits"] = nits
        ui["request_changes"] = changes
        if project.language == "pl":
            ui["approve_partial"] = f"{approve} (część pominięta)"
        else:
            ui["approve_partial"] = f"{approve} (some skipped)"
    return ui


def _cursor_slug(path: Path) -> str:
    return str(path.resolve()).lstrip("/").replace("/", "-")


def _cursor_slugs(path: Path) -> list[str]:
    raw = _cursor_slug(path)
    dotted = raw.replace(".", "-")
    return [raw] if raw == dotted else [raw, dotted]


def _workspace_includes(workspace: Path, repo: Path) -> bool:
    try:
        data = json.loads(workspace.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    base = workspace.parent.resolve()
    for item in data.get("folders") or []:
        raw = item.get("path") if isinstance(item, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            continue
        folder = Path(raw)
        folder = folder.resolve() if folder.is_absolute() else (base / folder).resolve()
        if folder == repo:
            return True
    return False


def _workspace_files_for(repo: Path) -> list[Path]:
    root = repo.resolve()
    found: list[Path] = []
    folder = root
    for _ in range(4):
        for workspace in sorted(folder.glob("*.code-workspace")):
            if _workspace_includes(workspace, root):
                found.append(workspace)
        if folder.parent == folder:
            break
        folder = folder.parent
    return found


def canvas_paths_for_repo(repo: Path) -> list[Path]:
    override = os.environ.get("REVIEW_CHANGE_CANVAS", "").strip()
    if override:
        return [Path(override).expanduser()]
    root = repo.resolve()
    parent = root.parent
    projects = Path.home() / ".cursor" / "projects"
    slugs: list[str] = []
    workspaces = _workspace_files_for(root)
    if workspaces:
        slugs.extend(_cursor_slugs(parent))
        for workspace in workspaces:
            slugs.extend(_cursor_slugs(workspace))
            slugs.extend(_cursor_slugs(workspace.parent))
    slugs.extend(_cursor_slugs(root))
    out: list[Path] = []
    seen: set[Path] = set()
    for slug in slugs:
        path = projects / slug / "canvases" / CANVAS_NAME
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def canvas_path(repo: Path) -> Path:
    candidates = canvas_paths_for_repo(repo)
    for path in candidates:
        if path.is_file():
            return path
    for path in candidates:
        if path.parent.parent.is_dir():
            return path
    return candidates[0]


def publish_canvas(repo: Path) -> Path | None:
    paths = canvas_paths_for_repo(repo)
    richest: Path | None = None
    richest_size = -1
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "useCanvasAction" not in text:
            continue
        size = path.stat().st_size
        if size > richest_size:
            richest = path
            richest_size = size
    if richest is None:
        return None
    body = richest.read_text(encoding="utf-8")
    for path in paths:
        if path == richest:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return richest


def _write_all(repo: Path, body: str) -> Path:
    written: Path | None = None
    for path in canvas_paths_for_repo(repo):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        written = path
    if written is None:
        raise OSError("no canvas path")
    return canvas_path(repo)


def _js(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return json.dumps(text, ensure_ascii=False)


def _locate(text_lines: list[str], old_lines: list[str]) -> int | None:
    if not old_lines:
        return None
    first = old_lines[0]
    span = len(old_lines)
    for index in range(len(text_lines)):
        if text_lines[index] == first and text_lines[index : index + span] == old_lines:
            return index + 1
    stripped = first.strip()
    if stripped:
        for index, line in enumerate(text_lines):
            if stripped in line:
                return index + 1
    return None


def _fix_diff_lines(
    repo: Path, path: str, replacements: list[dict]
) -> tuple[list[dict], int, int, int]:
    try:
        text = (repo / path).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        text = []
    out: list[dict] = []
    start = 0
    adds = dels = 0
    for repl in replacements or []:
        old = str((repl or {}).get("old") or "")
        new = str((repl or {}).get("new") or "")
        old_lines = old.split("\n") if old else []
        new_lines = new.split("\n") if new else []
        anchor = _locate(text, old_lines) if old_lines else None
        if anchor and not start:
            start = anchor
        for offset, line in enumerate(old_lines):
            item: dict = {"type": "removed", "content": line}
            if anchor:
                item["lineNumber"] = anchor + offset
            out.append(item)
            dels += 1
        for line in new_lines:
            out.append({"type": "added", "content": line})
            adds += 1
    return out, (start or 1), adds, dels


def _finding_rows(pairs: list[tuple[str, str]]) -> str:
    rows = [
        f'            <Text><Code>{{{_js(path)}}}</Code>{{" — "}}{{{_js(note)}}}</Text>'
        for path, note in pairs
    ]
    return "\n".join(rows)


def _identity(repo: Path, meta: dict) -> tuple[str, str, str]:
    if meta.get("demo"):
        return (
            str(meta.get("sha") or "demo"),
            str(meta.get("subject") or "demo"),
            str(meta.get("branch") or "demo"),
        )
    return (
        git(repo, "rev-parse", "--short", "HEAD"),
        git(repo, "log", "-1", "--pretty=%s"),
        git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
    )


def render_canvas_tsx(repo: Path, merged: dict, meta: dict | None = None) -> str:
    meta = meta or {}
    sha, subject, branch = _identity(repo, meta)
    ui = review_ui(repo)
    verdict_labels = {
        "approve": (ui["approve"], _VERDICT_TONE["approve"]),
        "approve-with-nits": (ui["approve_nits"], _VERDICT_TONE["approve-with-nits"]),
        "request-changes": (ui["request_changes"], _VERDICT_TONE["request-changes"]),
        "too-large": (ui["too_large"], "warning"),
    }
    verdict = str(merged.get("verdict") or "").strip()
    incomplete = verdict not in verdict_labels
    if incomplete:
        label, tone = (ui["incomplete"], "danger")
    else:
        label, tone = verdict_labels[verdict]
    skipped = [str(item).strip() for item in merged.get("not_reviewed") or [] if str(item).strip()]
    if not incomplete and verdict == "approve" and skipped:
        label, tone = (ui["approve_partial"], "warning")
    summary = str(merged.get("summary") or "").strip()
    blocking: list[tuple[str, str]] = []
    nits: list[tuple[str, str]] = []
    for item in merged.get("reviewed") or []:
        path = str((item or {}).get("path") or "")
        for finding in (item or {}).get("findings") or []:
            if not isinstance(finding, dict):
                continue
            note = str(
                finding.get("note") or finding.get("category") or finding.get("anchor") or ""
            ).strip()
            if not note:
                continue
            severity = str(finding.get("severity") or "").lower()
            (blocking if severity.startswith("block") else nits).append((path, note))
    count_tone = "danger" if blocking else ("warning" if nits else None)
    body: list[str] = []
    body.append("    <Stack gap={24} style={{ padding: 24, maxWidth: 920 }}>")
    body.append("      <Stack gap={8}>")
    body.append('        <Row gap={8} align="center">')
    body.append(f"          <H1>{ui['h1']}</H1>")
    body.append(f"          <Pill active>{{{_js(label)}}}</Pill>")
    body.append("        </Row>")
    body.append('        <Text tone="secondary">')
    body.append(
        f"          {ui['commit']} <Code>{{{_js(sha)}}}</Code> {ui['on_branch']} "
        f'<Code>{{{_js(branch)}}}</Code>{{" — "}}{{{_js(subject)}}}.'
    )
    body.append("        </Text>")
    finished = str(meta.get("finished") or "").strip()
    elapsed = str(meta.get("elapsed") or "").strip()
    if finished or elapsed:
        stamp = " · ".join(
            part
            for part in (
                f"{ui['finished']} {finished}" if finished else "",
                f"{ui['took']} {elapsed}" if elapsed else "",
            )
            if part
        )
        body.append(
            '        <Text size="small" tone="tertiary" style={{ color: theme.text.tertiary }}>'
        )
        body.append(f"          {{{_js(stamp)}}}")
        body.append("        </Text>")
    body.append("      </Stack>")
    body.append("")
    body.append("      <Grid columns={3} gap={12}>")
    body.append(f'        <Stat value={{{_js(sha)}}} label="{ui["commit"]}" />')
    verdict_tone = f' tone="{tone}"' if tone else ""
    body.append(f'        <Stat value={{{_js(label)}}} label="{ui["verdict"]}"{verdict_tone} />')
    counts = f"{len(blocking)} / {len(nits)}"
    count_tone_prop = f' tone="{count_tone}"' if count_tone else ""
    body.append(
        f'        <Stat value={{{_js(counts)}}} label="{ui["blocking_notes"]}"{count_tone_prop} />'
    )
    body.append("      </Grid>")
    pre_review = str(meta.get("pre_review") or "").strip()
    if pre_review:
        command = load_review_project(repo).pre_review
        body.append("")
        body.append('      <Text tone="secondary">')
        body.append(
            f"        {ui['pre_review']} <Code>{{{_js(command)}}}</Code>: {{{_js(pre_review)}}}"
        )
        body.append("      </Text>")
    if incomplete:
        body.append("")
        body.append(
            f'      <Callout tone="danger" title="{ui["incomplete"]}">{ui["incomplete_body"]}</Callout>'
        )
    if skipped:
        body.append("")
        body.append(f'      <Callout tone="warning" title="{ui["skipped_title"]}">')
        body.append(f"{{{_js(', '.join(skipped))}}}</Callout>")
    if summary:
        if tone:
            body.append("")
            body.append(
                f'      <Callout tone="{tone}" title={{{_js(label)}}}>{{{_js(summary)}}}</Callout>'
            )
        else:
            body.append("")
            body.append(f"      <Text>{{{_js(summary)}}}</Text>")
    if blocking or nits:
        body.append("")
        body.append("      <Stack gap={12}>")
        body.append(f"        <H2>{ui['findings']}</H2>")
        if blocking:
            body.append(f'        <Callout tone="danger" title="{ui["blocking"]}">')
            body.append("          <Stack gap={4}>")
            body.append(_finding_rows(blocking))
            body.append("          </Stack>")
            body.append("        </Callout>")
        if nits:
            body.append(f'        <Callout tone="warning" title="{ui["notes"]}">')
            body.append("          <Stack gap={4}>")
            body.append(_finding_rows(nits))
            body.append("          </Stack>")
            body.append("        </Callout>")
        body.append("      </Stack>")
    proposals: list[str] = []
    for item in merged.get("files") or []:
        path = str((item or {}).get("path") or "").strip()
        if not path:
            continue
        lines, start, adds, dels = _fix_diff_lines(repo, path, (item or {}).get("replacements") or [])
        if not lines:
            continue
        proposals.append(
            "        <FileDiff\n"
            f"          path={{{_js(path)}}}\n"
            f"          startLine={{{start}}}\n"
            f"          additions={{{adds}}}\n"
            f"          deletions={{{dels}}}\n"
            f"          lines={{{json.dumps(lines, ensure_ascii=False)}}}\n"
            "        />"
        )
    if proposals:
        body.append("")
        body.append("      <Stack gap={12}>")
        body.append(f"        <H2>{ui['proposed']}</H2>")
        body.extend(proposals)
        body.append("      </Stack>")
    mr = merged.get("mr") or {}
    mr_title = str((mr or {}).get("title") or "").strip()
    mr_body = str((mr or {}).get("body") or "").strip()
    apply_path = ".cursor/" + APPLY_TRIGGER_NAME
    skip_path = ".cursor/" + SKIP_TRIGGER_NAME
    mr_path = ".cursor/" + MR_TRIGGER_NAME
    apply_mirror = repo.name + "/" + apply_path
    skip_mirror = repo.name + "/" + skip_path
    mr_mirror = repo.name + "/" + mr_path
    body.append("")
    body.append("      <Card>")
    body.append("        <CardBody>")
    body.append("          <Stack gap={12}>")
    body.append(f"            <H2>{ui['mr_draft']}</H2>")
    if mr_title:
        body.append("            <Stack gap={4}>")
        body.append(f'              <Text size="small" tone="secondary">{ui["mr_title"]}</Text>')
        body.append(f"              <Text>{{{_js(mr_title)}}}</Text>")
        body.append("            </Stack>")
    if mr_body:
        body.append("            <Stack gap={4}>")
        body.append(f'              <Text size="small" tone="secondary">{ui["mr_body"]}</Text>')
        body.append(f"              <Text>{{{_js(mr_body)}}}</Text>")
        body.append("            </Stack>")
    body.append('            <Row gap={8} align="center" wrap>')
    body.append("              <Button")
    body.append('                variant="primary"')
    body.append("                onClick={() => {")
    body.append("                  dispatch({")
    body.append('                    type: "openFile",')
    body.append(f"                    path: {_js(apply_path)},")
    body.append("                    selection: { startLineNumber: 2, startColumn: applyTick },")
    body.append("                  });")
    body.append("                  dispatch({")
    body.append('                    type: "openFile",')
    body.append(f"                    path: {_js(apply_mirror)},")
    body.append("                    selection: { startLineNumber: 2, startColumn: applyTick },")
    body.append("                  });")
    body.append("                  setApplyTick((n) => (n % 40) + 1);")
    body.append("                }}")
    body.append("              >")
    body.append(f"                {ui['apply']}")
    body.append("              </Button>")
    body.append("              <Button")
    body.append('                variant="secondary"')
    body.append("                onClick={() => {")
    body.append("                  dispatch({")
    body.append('                    type: "openFile",')
    body.append(f"                    path: {_js(skip_path)},")
    body.append("                    selection: { startLineNumber: 1 },")
    body.append("                  });")
    body.append("                  dispatch({")
    body.append('                    type: "openFile",')
    body.append(f"                    path: {_js(skip_mirror)},")
    body.append("                    selection: { startLineNumber: 1 },")
    body.append("                  });")
    body.append("                }}")
    body.append("              >")
    body.append(f"                {ui['skip']}")
    body.append("              </Button>")
    body.append("              <Button")
    body.append('                variant="ghost"')
    body.append("                onClick={() => {")
    body.append(f'                  dispatch({{ type: "openFile", path: {_js(mr_path)} }});')
    body.append(f'                  dispatch({{ type: "openFile", path: {_js(mr_mirror)} }});')
    body.append("                }}")
    body.append("              >")
    body.append(f"                {ui['create_mr']}")
    body.append("              </Button>")
    body.append("            </Row>")
    body.append(
        '            <Text size="small" tone="tertiary" style={{ color: theme.text.tertiary }}>'
    )
    body.append(
        f"              {ui['manual_before']}<Code>@apply-review-fixes</Code>{ui['manual_after']}"
    )
    body.append("            </Text>")
    body.append("          </Stack>")
    body.append("        </CardBody>")
    body.append("      </Card>")
    body.append("    </Stack>")
    return _CANVAS_HEAD + "\n".join(body) + "\n" + _CANVAS_TAIL


def write_canvas(repo: Path, merged: dict, meta: dict | None = None) -> Path:
    return _write_all(repo, render_canvas_tsx(repo, merged, meta))


def write_pending_canvas(
    repo: Path,
    sha: str,
    subject: str,
    branch: str,
    started: str,
    pre_review: str = "",
    branch_scope: bool = False,
) -> Path:
    ui = review_ui(repo)
    sha_js = json.dumps(sha, ensure_ascii=False)
    subject_js = json.dumps(subject, ensure_ascii=False)
    branch_js = json.dumps(branch, ensure_ascii=False)
    started_js = json.dumps(started, ensure_ascii=False)
    scope_text = ui["pending_scope_branch"] if branch_scope else ui["pending_scope"]
    scope_js = json.dumps(scope_text, ensure_ascii=False)
    lead = (
        f"          {ui['commit']} <Code>{{{sha_js}}}</Code> {ui['on_branch']} "
        f"<Code>{{{branch_js}}}</Code> — {{{subject_js}}}. {{{scope_js}}}"
    )
    pre_block = ""
    if pre_review:
        command = load_review_project(repo).pre_review
        pre_block = (
            "\n      <Text tone=\"secondary\">\n"
            f"        {ui['pre_review']} <Code>{{{json.dumps(command, ensure_ascii=False)}}}</Code>"
            f": {{{json.dumps(pre_review, ensure_ascii=False)}}}\n"
            "      </Text>\n"
        )
    body = f"""import {{
  Code,
  Grid,
  H1,
  Pill,
  Row,
  Stack,
  Stat,
  Text,
  useHostTheme,
}} from "cursor/canvas";

export default function ReviewChangePreview() {{
  const theme = useHostTheme();

  return (
    <Stack gap={{24}} style={{{{ padding: 24, maxWidth: 920 }}}}>
      <Stack gap={{8}}>
        <Row gap={{8}} align="center">
          <H1>{ui["h1"]}</H1>
          <Pill active>{ui["in_progress"]}</Pill>
        </Row>
        <Text tone="secondary">
{lead}
        </Text>
      </Stack>
      <Grid columns={{3}} gap={{12}}>
        <Stat value={{{sha_js}}} label="{ui["commit"]}" />
        <Stat value="{ui["in_progress"]}" label="{ui["stage"]}" />
        <Stat value={{{started_js}}} label="{ui["start"]}" />
      </Grid>
{pre_block}
      <Text size="small" tone="tertiary" style={{{{ color: theme.text.tertiary }}}}>
        {ui["log_line"]}
      </Text>
    </Stack>
  );
}}
"""
    return _write_all(repo, body)


def write_idle_canvas(repo: Path, sha: str, subject: str, pill: str, note: str) -> Path:
    ui = review_ui(repo)
    parts = [
        'import { Code, H1, Pill, Row, Stack, Text } from "cursor/canvas";',
        "",
        "export default function ReviewChangePreview() {",
        "  return (",
        "    <Stack gap={16} style={{ padding: 24, maxWidth: 920 }}>",
        '      <Row gap={8} align="center">',
        f"        <H1>{ui['h1']}</H1>",
        f"        <Pill>{{{_js(pill)}}}</Pill>",
        "      </Row>",
        '      <Text tone="secondary">',
        f'        {ui["commit"]} <Code>{{{_js(sha)}}}</Code>{{" — "}}{{{_js(subject)}}}.',
        "      </Text>",
        f"      <Text>{{{_js(note)}}}</Text>",
        "    </Stack>",
        "  );",
        "}",
        "",
    ]
    return _write_all(repo, "\n".join(parts))
