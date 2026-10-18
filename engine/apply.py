"""Apply or dismiss var/review-change.fixes.json. No commit and no push."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.ledger import blob_matches, record_dismissed
from engine.paths import repo_root


def safe_rel(path: str) -> Path:
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"unsafe path: {path}")
    return rel


def apply_fixes(repo: Path, payload: dict) -> int:
    applied = 0
    for item in payload.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = safe_rel(str(item.get("path") or ""))
        target = repo / rel
        if not target.is_file():
            print(f"skip missing {rel}", file=sys.stderr)
            continue
        expected = str(item.get("blob") or "")
        if not blob_matches(repo, str(rel), expected):
            print(
                f"skip changed {rel} — file differs from the reviewed blob; re-review before applying",
                file=sys.stderr,
            )
            continue
        text = target.read_text(encoding="utf-8")
        original = text
        for repl in item.get("replacements") or []:
            old = repl.get("old")
            new = repl.get("new")
            if not isinstance(old, str) or not isinstance(new, str):
                raise SystemExit(f"{rel}: replacement needs old/new strings")
            if old == new:
                continue
            count = text.count(old)
            if count == 0 and new in text:
                continue
            if count != 1:
                raise SystemExit(f"{rel}: expected one match for a replacement, got {count}")
            text = text.replace(old, new, 1)
        if text != original:
            target.write_text(text, encoding="utf-8")
            applied += 1
            print(f"applied {rel}")
        else:
            print(f"unchanged {rel}")
    mr = payload.get("mr") or {}
    title = str((mr or {}).get("title") or "").strip()
    body = str((mr or {}).get("body") or "").strip()
    if title or body:
        mr_path = repo / "var" / "review-change.mr.md"
        mr_path.parent.mkdir(parents=True, exist_ok=True)
        mr_path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        print(f"wrote {mr_path.relative_to(repo)}")
    return applied


def main() -> int:
    repo = repo_root()
    if "--skip" in sys.argv:
        added = record_dismissed(repo)
        print(f"dismissed {added} note(s) — Skip is sticky on this branch")
        return 0
    args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    raw = Path(args[0]) if args else repo / "var" / "review-change.fixes.json"
    if not raw.is_file():
        print(f"no fixes file: {raw}", file=sys.stderr)
        return 0
    payload = json.loads(raw.read_text(encoding="utf-8"))
    apply_fixes(repo, payload)
    return 0
