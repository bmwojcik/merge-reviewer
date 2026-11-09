"""HTML and Markdown report for hosts without a Cursor canvas (Claude Code).

Same analysis JSON, same labels as `engine/canvas.py`. Output goes to
`var/review-change.html` and `var/review-change.md`. A small status file,
`var/review-change.status.json`, lets the Claude Code hook announce a finished run.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from engine.canvas import _fix_diff_lines, _identity, collect_findings, review_ui, verdict_view
from engine.paths import REPORT_HTML_NAME, REPORT_MD_NAME, STATUS_NAME, reviewer_home
from engine.project import load_review_project

_X: dict[str, dict[str, str]] = {
    "en": {
        "model": "Model",
        "anchor": "Anchor",
        "actions": "Actions",
        "copy": "Copy",
        "copied": "Copied",
        "in_claude": "In Claude Code",
        "in_terminal": "In a terminal (git root)",
        "apply_hint": "Apply the proposed replacements. No commit, no push.",
        "skip_hint": "Dismiss these notes on this branch. Source files stay as they are.",
        "mr_hint": "Push HEAD and open the new merge request page. Only run it when you mean it.",
        "no_findings": "No findings.",
        "no_fixes": "No proposed fixes.",
        "running_lead": "The reviewer is still working. This page refreshes every 5 seconds.",
        "running_md": "The reviewer is still working. Open var/review-change.html or re-read this file in a minute.",
        "new_mr": "New merge request page",
        "files_reviewed": "Files reviewed",
        "line": "line",
        "fixed_by": "Written by merge-reviewer",
    },
    "pl": {
        "model": "Model",
        "anchor": "Kotwica",
        "actions": "Akcje",
        "copy": "Kopiuj",
        "copied": "Skopiowano",
        "in_claude": "W Claude Code",
        "in_terminal": "W terminalu (korzeń gita)",
        "apply_hint": "Nałóż proponowane zamiany. Bez commita i bez pusha.",
        "skip_hint": "Odrzuć te uwagi na tej gałęzi. Pliki źródłowe zostają bez zmian.",
        "mr_hint": "Wypchnij HEAD i otwórz stronę nowego MR. Uruchom tylko świadomie.",
        "no_findings": "Brak znalezisk.",
        "no_fixes": "Brak propozycji poprawek.",
        "running_lead": "Recenzja jeszcze trwa. Ta strona odświeża się co 5 sekund.",
        "running_md": "Recenzja jeszcze trwa. Otwórz var/review-change.html albo przeczytaj ten plik za minutę.",
        "new_mr": "Strona nowego MR",
        "files_reviewed": "Przejrzane pliki",
        "line": "linia",
        "fixed_by": "Wygenerowane przez merge-reviewer",
    },
}


def report_dir(repo: Path) -> Path:
    override = os.environ.get("REVIEW_CHANGE_REPORT_DIR", "").strip()
    return Path(override).expanduser() if override else repo / "var"


def html_path(repo: Path) -> Path:
    return report_dir(repo) / REPORT_HTML_NAME


def md_path(repo: Path) -> Path:
    return report_dir(repo) / REPORT_MD_NAME


def status_path(repo: Path) -> Path:
    return repo / "var" / STATUS_NAME


def log_path(repo: Path) -> Path:
    return repo / "var" / "review-change.log"


def _x(repo: Path) -> dict[str, str]:
    return _X["pl" if load_review_project(repo).language == "pl" else "en"]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def action_commands(repo: Path) -> dict[str, str]:
    home = reviewer_home()
    root = str(repo)
    return {
        "apply_skill": "/apply-review-fixes",
        "skip_skill": "/skip-review-fixes",
        "apply_shell": f'cd "{root}" && "{home}/bin/apply-review-fixes"',
        "skip_shell": f'cd "{root}" && "{home}/bin/apply-review-fixes" --skip',
        "mr_shell": f'cd "{root}" && "{home}/bin/post-commit-review" --create-mr',
    }


def _new_mr_url(repo: Path, meta: dict) -> str:
    if meta.get("demo"):
        return ""
    try:
        from engine.host import new_mr_url

        return new_mr_url(repo)
    except Exception:  # noqa: BLE001 - a missing remote must not break the report
        return ""


def _hunks(repo: Path, path: str, replacements: list[dict]) -> list[dict]:
    """One hunk per replacement: header, +/- lines, add/delete counts."""
    out: list[dict] = []
    for repl in replacements or []:
        lines, start, adds, dels = _fix_diff_lines(repo, path, [repl])
        if not lines:
            continue
        rows = [("-" if item["type"] == "removed" else "+", item["content"]) for item in lines]
        out.append(
            {
                "header": f"@@ -{start},{dels} +{start},{adds} @@",
                "start": start,
                "adds": adds,
                "dels": dels,
                "rows": rows,
            }
        )
    return out


def _model(data: dict) -> dict:
    """Common derived values shared by both renderers."""
    repo: Path = data["repo"]
    merged: dict = data["merged"]
    meta: dict = data["meta"]
    ui = review_ui(repo)
    sha, subject, branch = _identity(repo, meta)
    label, tone, incomplete, verdict = verdict_view(ui, merged)
    skipped = [str(item).strip() for item in merged.get("not_reviewed") or [] if str(item).strip()]
    if not incomplete and verdict == "approve" and skipped:
        label, tone = ui["approve_partial"], "warning"
    findings = collect_findings(merged)
    fixes = []
    for item in merged.get("files") or []:
        path = str((item or {}).get("path") or "").strip()
        if not path:
            continue
        hunks = _hunks(repo, path, (item or {}).get("replacements") or [])
        if hunks:
            fixes.append({"path": path, "hunks": hunks})
    mr = merged.get("mr") or {}
    stamp = " · ".join(
        part
        for part in (
            f"{ui['finished']} {meta['finished']}" if meta.get("finished") else "",
            f"{ui['took']} {meta['elapsed']}" if meta.get("elapsed") else "",
            f"{_x(repo)['model']} {meta['model']}" if meta.get("model") else "",
        )
        if part
    )
    return {
        "ui": ui,
        "x": _x(repo),
        "sha": sha,
        "subject": subject,
        "branch": branch,
        "label": label,
        "tone": tone or "ok",
        "incomplete": incomplete,
        "verdict": verdict,
        "skipped": skipped,
        "summary": str(merged.get("summary") or "").strip(),
        "blocking": [f for f in findings if f["blocking"]],
        "nits": [f for f in findings if not f["blocking"]],
        "fixes": fixes,
        "mr_title": str((mr or {}).get("title") or "").strip(),
        "mr_body": str((mr or {}).get("body") or "").strip(),
        "stamp": stamp,
        "pre_review": str(meta.get("pre_review") or "").strip(),
        "pre_command": load_review_project(repo).pre_review,
        "cmds": action_commands(repo),
        "mr_url": _new_mr_url(repo, meta),
        "html": str(html_path(repo)),
        "md": str(md_path(repo)),
    }


# ---------------------------------------------------------------- markdown


def _md_finding(item: dict, x: dict[str, str]) -> str:
    head = f"- `{item['path']}`"
    if item["category"]:
        head += f" [{item['category']}]"
    line = f"{head} — {item['note']}"
    if item["anchor"]:
        line += f" ({x['anchor']}: `{item['anchor'][:120].replace(chr(10), ' ')}`)"
    return line


def render_markdown(repo: Path, merged: dict, meta: dict | None = None) -> str:
    m = _model({"repo": repo, "merged": merged, "meta": meta or {}})
    ui, x, cmds = m["ui"], m["x"], m["cmds"]
    out: list[str] = [f"# {ui['h1']} — {m['label']}", ""]
    out.append(
        f"{ui['commit']} `{m['sha']}` {ui['on_branch']} `{m['branch']}` — {m['subject']}."
    )
    if m["stamp"]:
        out.append("")
        out.append(f"_{m['stamp']}_")
    out += ["", f"## {ui['verdict']}", "", f"**{m['label']}**"]
    out.append(f"{ui['blocking']}: {len(m['blocking'])} · {ui['notes']}: {len(m['nits'])}")
    if m["summary"]:
        out += ["", m["summary"]]
    if m["incomplete"]:
        out += ["", f"> **{ui['incomplete']}** — {ui['incomplete_body']}"]
    if m["skipped"]:
        out += ["", f"> **{ui['skipped_title']}** — {', '.join(m['skipped'])}"]
    if m["pre_review"]:
        out += ["", f"{ui['pre_review']} `{m['pre_command']}`: {m['pre_review']}"]
    out += ["", f"## {ui['findings']}", ""]
    if not (m["blocking"] or m["nits"]):
        out.append(x["no_findings"])
    if m["blocking"]:
        out += [f"### {ui['blocking']}", ""]
        out += [_md_finding(item, x) for item in m["blocking"]]
        out.append("")
    if m["nits"]:
        out += [f"### {ui['notes']}", ""]
        out += [_md_finding(item, x) for item in m["nits"]]
        out.append("")
    out += [f"## {ui['proposed']}", ""]
    if not m["fixes"]:
        out += [x["no_fixes"], ""]
    for fix in m["fixes"]:
        body = [f"--- a/{fix['path']}", f"+++ b/{fix['path']}"]
        for hunk in fix["hunks"]:
            body.append(hunk["header"])
            body += [sign + text for sign, text in hunk["rows"]]
        fence = "````" if any("```" in line for line in body) else "```"
        out += [f"### `{fix['path']}`", "", fence + "diff", *body, fence, ""]
    out += [f"## {ui['mr_draft']}", ""]
    if m["mr_title"]:
        out += [f"**{ui['mr_title']}:** {m['mr_title']}", ""]
    if m["mr_body"]:
        out += [f"**{ui['mr_body']}:**", "", m["mr_body"], ""]
    out += [f"## {x['actions']}", "", f"{x['in_claude']}:", ""]
    out.append(f"- `{cmds['apply_skill']}` — {ui['apply']}. {x['apply_hint']}")
    out.append(f"- `{cmds['skip_skill']}` — {ui['skip']}. {x['skip_hint']}")
    out += ["", f"{x['in_terminal']}:", "", "```sh"]
    out.append(f"# {ui['apply']}")
    out.append(cmds["apply_shell"])
    out.append(f"# {ui['skip']}")
    out.append(cmds["skip_shell"])
    out.append(f"# {ui['create_mr']}")
    out.append(cmds["mr_shell"])
    out += ["```", ""]
    if m["mr_url"]:
        out += [f"{x['new_mr']}: {m['mr_url']}", ""]
    out.append(f"{ui['log_line']}")
    out.append("")
    return "\n".join(out)


def render_pending_markdown(repo: Path, sha: str, subject: str, branch: str, started: str, branch_scope: bool) -> str:
    ui, x = review_ui(repo), _x(repo)
    scope = ui["pending_scope_branch"] if branch_scope else ui["pending_scope"]
    return "\n".join(
        [
            f"# {ui['h1']} — {ui['in_progress']}",
            "",
            f"{ui['commit']} `{sha}` {ui['on_branch']} `{branch}` — {subject}. {scope}",
            "",
            f"{ui['start']}: {started}",
            "",
            x["running_md"],
            "",
            ui["log_line"],
            "",
        ]
    )


def render_idle_markdown(repo: Path, sha: str, subject: str, pill: str, note: str) -> str:
    ui = review_ui(repo)
    return f"# {ui['h1']} — {pill}\n\n{ui['commit']} `{sha}` — {subject}.\n\n{note}\n"


# -------------------------------------------------------------------- html

_CSS = """
:root{color-scheme:light dark;--bg:#fafaf9;--fg:#1c1917;--muted:#57534e;--card:#fff;--line:#e7e5e4;
--ok:#15803d;--ok-bg:#dcfce7;--warn:#a16207;--warn-bg:#fef3c7;--bad:#b91c1c;--bad-bg:#fee2e2;
--add-bg:#dcfce7;--del-bg:#fee2e2;--code-bg:#f5f5f4;--accent:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#e7e5e4;--muted:#a8a29e;--card:#1c1b1a;--line:#2e2c2a;
--ok:#4ade80;--ok-bg:#14301f;--warn:#fbbf24;--warn-bg:#35290b;--bad:#f87171;--bad-bg:#3b1414;
--add-bg:#12301d;--del-bg:#3b1717;--code-bg:#242220;--accent:#7aa2ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0}h2{font-size:1.15rem;margin:28px 0 10px}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
code{background:var(--code-bg);padding:1px 5px;border-radius:4px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.muted{color:var(--muted)}.small{font-size:.85rem}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.85rem;font-weight:600;border:1px solid currentColor}
.pill.ok{color:var(--ok);background:var(--ok-bg)}.pill.warning{color:var(--warn);background:var(--warn-bg)}
.pill.danger{color:var(--bad);background:var(--bad-bg)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.stat b{display:block;font-size:1.25rem}.stat.danger b{color:var(--bad)}.stat.warning b{color:var(--warn)}
.callout{border-left:4px solid var(--line);background:var(--card);padding:10px 14px;border-radius:6px;margin:12px 0}
.callout.danger{border-color:var(--bad);background:var(--bad-bg)}.callout.warning{border-color:var(--warn);background:var(--warn-bg)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
summary{cursor:pointer;padding:10px 14px;font-weight:600;list-style-position:inside}
details>.body{padding:0 14px 12px}
.sev{display:inline-block;font-size:.72rem;padding:1px 8px;border-radius:999px;margin-right:6px;font-weight:700;vertical-align:1px}
.sev.danger{background:var(--bad-bg);color:var(--bad)}.sev.warning{background:var(--warn-bg);color:var(--warn)}
.diff{margin:0;overflow-x:auto;border-top:1px solid var(--line)}
.diff div{white-space:pre;padding:0 14px;min-width:max-content}
.diff .hunk{color:var(--muted);background:var(--code-bg)}.diff .add{background:var(--add-bg)}.diff .del{background:var(--del-bg)}
.cmd{display:flex;gap:8px;align-items:center;margin:2px 0 10px}
.cmd-hint{margin-top:8px}
.cmd pre{flex:1 1 auto;min-width:0;margin:0;padding:8px 10px;background:var(--code-bg);border-radius:6px;overflow-x:auto;white-space:pre}
button{font:inherit;cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--fg);padding:5px 12px;border-radius:8px}
button:hover{border-color:var(--accent);color:var(--accent)}
.body-text{white-space:pre-wrap}
a{color:var(--accent)}
"""

_JS = """
document.querySelectorAll('button[data-copy]').forEach(function(b){
  b.addEventListener('click',function(){
    var t=b.getAttribute('data-copy'),done=function(){var o=b.textContent;b.textContent=b.getAttribute('data-done');
      setTimeout(function(){b.textContent=o},1500)};
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fallback)}else{fallback()}
    function fallback(){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
      try{document.execCommand('copy');done()}catch(e){}document.body.removeChild(a)}
  });
});
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, body: str, refresh: bool = False, lang: str = "en") -> str:
    meta = '<meta http-equiv="refresh" content="5">' if refresh else ""
    return (
        f'<!doctype html>\n<html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"{meta}<title>{_e(title)}</title><style>{_CSS}</style></head><body><main>\n"
        f"{body}\n</main><script>{_JS}</script></body></html>\n"
    )


def _copy_row(text: str, x: dict[str, str], hint: str = "") -> str:
    note = f'<div class="muted small cmd-hint">{_e(hint)}</div>' if hint else ""
    return (
        f"{note}"
        '<div class="cmd">'
        f"<pre>{_e(text)}</pre>"
        f'<button type="button" data-copy="{_e(text)}" data-done="{_e(x["copied"])}">{_e(x["copy"])}</button>'
        "</div>"
    )


def _html_finding(item: dict, x: dict[str, str], ui: dict[str, str]) -> str:
    cls = "danger" if item["blocking"] else "warning"
    sev = ui["blocking"] if item["blocking"] else ui["notes"]
    title = f'<span class="sev {cls}">{_e(sev)}</span><code>{_e(item["path"])}</code>'
    if item["category"]:
        title += f' <span class="muted small">{_e(item["category"])}</span>'
    body = f"<p>{_e(item['note'])}</p>"
    if item["anchor"]:
        body += f'<p class="small muted">{_e(x["anchor"])}: <code>{_e(item["anchor"][:200])}</code></p>'
    return f"<details open><summary>{title}</summary><div class=\"body\">{body}</div></details>"


def _html_fix(fix: dict) -> str:
    adds = sum(h["adds"] for h in fix["hunks"])
    dels = sum(h["dels"] for h in fix["hunks"])
    rows: list[str] = []
    for hunk in fix["hunks"]:
        rows.append(f'<div class="hunk">{_e(hunk["header"])}</div>')
        for sign, text in hunk["rows"]:
            cls = "add" if sign == "+" else "del"
            rows.append(f'<div class="{cls}">{sign}{_e(text)}</div>')
    head = (
        f'<code>{_e(fix["path"])}</code> '
        f'<span class="small" style="color:var(--ok)">+{adds}</span> '
        f'<span class="small" style="color:var(--bad)">-{dels}</span>'
    )
    return f'<details open><summary>{head}</summary><pre class="diff">{"".join(rows)}</pre></details>'


def render_html(repo: Path, merged: dict, meta: dict | None = None) -> str:
    m = _model({"repo": repo, "merged": merged, "meta": meta or {}})
    ui, x, cmds = m["ui"], m["x"], m["cmds"]
    counts = f"{len(m['blocking'])} / {len(m['nits'])}"
    count_cls = "danger" if m["blocking"] else ("warning" if m["nits"] else "")
    parts: list[str] = []
    parts.append(
        f'<div class="row"><h1>{_e(ui["h1"])}</h1><span class="pill {m["tone"]}">{_e(m["label"])}</span></div>'
    )
    parts.append(
        f'<p class="muted">{_e(ui["commit"])} <code>{_e(m["sha"])}</code> {_e(ui["on_branch"])} '
        f'<code>{_e(m["branch"])}</code> — {_e(m["subject"])}.</p>'
    )
    if m["stamp"]:
        parts.append(f'<p class="muted small">{_e(m["stamp"])}</p>')
    parts.append(
        '<div class="stats">'
        f'<div class="stat"><span class="muted small">{_e(ui["commit"])}</span><b>{_e(m["sha"])}</b></div>'
        f'<div class="stat {"" if m["tone"] == "ok" else m["tone"]}"><span class="muted small">{_e(ui["verdict"])}</span><b>{_e(m["label"])}</b></div>'
        f'<div class="stat {count_cls}"><span class="muted small">{_e(ui["blocking_notes"])}</span><b>{_e(counts)}</b></div>'
        "</div>"
    )
    if m["pre_review"]:
        parts.append(
            f'<p class="muted">{_e(ui["pre_review"])} <code>{_e(m["pre_command"])}</code>: {_e(m["pre_review"])}</p>'
        )
    if m["incomplete"]:
        parts.append(
            f'<div class="callout danger"><b>{_e(ui["incomplete"])}</b><br>{_e(ui["incomplete_body"])}</div>'
        )
    if m["skipped"]:
        parts.append(
            f'<div class="callout warning"><b>{_e(ui["skipped_title"])}</b><br>{_e(", ".join(m["skipped"]))}</div>'
        )
    if m["summary"]:
        cls = "" if m["tone"] == "ok" else m["tone"]
        parts.append(f'<div class="callout {cls}">{_e(m["summary"])}</div>')
    parts.append(f'<h2>{_e(ui["findings"])}</h2>')
    if not (m["blocking"] or m["nits"]):
        parts.append(f'<p class="muted">{_e(x["no_findings"])}</p>')
    parts += [_html_finding(item, x, ui) for item in m["blocking"] + m["nits"]]
    parts.append(f'<h2>{_e(ui["proposed"])}</h2>')
    if not m["fixes"]:
        parts.append(f'<p class="muted">{_e(x["no_fixes"])}</p>')
    parts += [_html_fix(fix) for fix in m["fixes"]]
    parts.append(f'<h2>{_e(ui["mr_draft"])}</h2>')
    if m["mr_title"]:
        parts.append(
            f'<p class="muted small" style="margin-bottom:0">{_e(ui["mr_title"])}</p>'
            f"<p style=\"margin-top:2px\"><b>{_e(m['mr_title'])}</b></p>"
        )
        parts.append(_copy_row(m["mr_title"], x))
    if m["mr_body"]:
        parts.append(
            f'<p class="muted small" style="margin-bottom:0">{_e(ui["mr_body"])}</p>'
            f'<p class="body-text" style="margin-top:2px">{_e(m["mr_body"])}</p>'
        )
        parts.append(_copy_row(m["mr_body"], x))
    parts.append(f'<h2>{_e(x["actions"])}</h2>')
    parts.append(f'<p class="muted small">{_e(x["in_claude"])}</p>')
    parts.append(_copy_row(cmds["apply_skill"], x, f'{ui["apply"]}. {x["apply_hint"]}'))
    parts.append(_copy_row(cmds["skip_skill"], x, f'{ui["skip"]}. {x["skip_hint"]}'))
    parts.append(f'<p class="muted small">{_e(x["in_terminal"])}</p>')
    parts.append(_copy_row(cmds["apply_shell"], x, ui["apply"]))
    parts.append(_copy_row(cmds["skip_shell"], x, ui["skip"]))
    parts.append(_copy_row(cmds["mr_shell"], x, f'{ui["create_mr"]}. {x["mr_hint"]}'))
    if m["mr_url"]:
        parts.append(
            f'<p class="small">{_e(x["new_mr"])}: <a href="{_e(m["mr_url"])}" rel="noopener">{_e(m["mr_url"])}</a></p>'
        )
    parts.append(f'<p class="muted small">{_e(ui["log_line"])} · <code>{_e(m["md"])}</code></p>')
    lang = "pl" if load_review_project(repo).language == "pl" else "en"
    return _page(f"{ui['h1']} {m['sha']}", "\n".join(parts), lang=lang)


def render_pending_html(repo: Path, sha: str, subject: str, branch: str, started: str, branch_scope: bool) -> str:
    ui, x = review_ui(repo), _x(repo)
    scope = ui["pending_scope_branch"] if branch_scope else ui["pending_scope"]
    body = (
        f'<div class="row"><h1>{_e(ui["h1"])}</h1><span class="pill warning">{_e(ui["in_progress"])}</span></div>'
        f'<p class="muted">{_e(ui["commit"])} <code>{_e(sha)}</code> {_e(ui["on_branch"])} '
        f"<code>{_e(branch)}</code> — {_e(subject)}. {_e(scope)}</p>"
        '<div class="stats">'
        f'<div class="stat"><span class="muted small">{_e(ui["commit"])}</span><b>{_e(sha)}</b></div>'
        f'<div class="stat"><span class="muted small">{_e(ui["stage"])}</span><b>{_e(ui["in_progress"])}</b></div>'
        f'<div class="stat"><span class="muted small">{_e(ui["start"])}</span><b>{_e(started)}</b></div>'
        "</div>"
        f'<p>{_e(x["running_lead"])}</p>'
        f'<p class="muted small">{_e(ui["log_line"])}</p>'
    )
    lang = "pl" if load_review_project(repo).language == "pl" else "en"
    return _page(f"{ui['h1']} {sha}", body, refresh=True, lang=lang)


def render_idle_html(repo: Path, sha: str, subject: str, pill: str, note: str) -> str:
    ui = review_ui(repo)
    body = (
        f'<div class="row"><h1>{_e(ui["h1"])}</h1><span class="pill warning">{_e(pill)}</span></div>'
        f'<p class="muted">{_e(ui["commit"])} <code>{_e(sha)}</code> — {_e(subject)}.</p>'
        f"<p>{_e(note)}</p>"
    )
    lang = "pl" if load_review_project(repo).language == "pl" else "en"
    return _page(f"{ui['h1']} {sha}", body, lang=lang)


# ------------------------------------------------------------------ writers


def write_status(repo: Path, state: str, **fields: object) -> None:
    """Best-effort status file for the Claude Code hook. Never raises."""
    try:
        now = time.time()
        sha = str(fields.get("sha") or "")
        started_epoch = float(fields.get("started_epoch") or now)
        payload: dict = {
            "version": 1,
            "host": "claude",
            "state": state,
            "id": f"{sha}-{int(started_epoch)}",
            "updated_epoch": now,
            "started_epoch": started_epoch,
            "html": str(html_path(repo)),
            "md": str(md_path(repo)),
            "log": str(log_path(repo)),
        }
        for key, value in fields.items():
            if value is not None and key != "started_epoch":
                payload[key] = value
        _atomic_write(status_path(repo), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return


def write_report(repo: Path, merged: dict, meta: dict | None = None) -> Path:
    meta = meta or {}
    _atomic_write(md_path(repo), render_markdown(repo, merged, meta))
    _atomic_write(html_path(repo), render_html(repo, merged, meta))
    return html_path(repo)


def write_pending_report(
    repo: Path, sha: str, subject: str, branch: str, started: str, branch_scope: bool = False
) -> Path:
    _atomic_write(md_path(repo), render_pending_markdown(repo, sha, subject, branch, started, branch_scope))
    _atomic_write(html_path(repo), render_pending_html(repo, sha, subject, branch, started, branch_scope))
    return html_path(repo)


def write_idle_report(repo: Path, sha: str, subject: str, pill: str, note: str) -> Path:
    _atomic_write(md_path(repo), render_idle_markdown(repo, sha, subject, pill, note))
    _atomic_write(html_path(repo), render_idle_html(repo, sha, subject, pill, note))
    return html_path(repo)


def open_report(path: Path) -> None:
    """Best effort: wslview, xdg-open, then explorer.exe. Only when REVIEW_CHANGE_OPEN=1."""
    if os.environ.get("REVIEW_CHANGE_OPEN", "").strip().lower() not in {"1", "true", "yes"}:
        return
    attempts: list[list[str]] = []
    if shutil.which("wslview"):
        attempts.append(["wslview", str(path)])
    if shutil.which("xdg-open"):
        attempts.append(["xdg-open", str(path)])
    if shutil.which("explorer.exe"):
        target = str(path)
        if shutil.which("wslpath"):
            try:
                target = subprocess.check_output(["wslpath", "-w", str(path)], text=True, timeout=5).strip()
            except (OSError, subprocess.SubprocessError):
                pass
        attempts.append(["explorer.exe", target])
    for args in attempts:
        try:
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except OSError:
            continue
