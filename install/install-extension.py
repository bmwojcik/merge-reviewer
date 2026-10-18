#!/usr/bin/env python3
"""Pack and install the review-apply extension. Does not print secrets."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

PUBLISHER = "review"
NAME = "review-apply"
EXTENSION_ID = f"{PUBLISHER}.{NAME}"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>review apply</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.90.0" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui,workspace" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
  </Assets>
</PackageManifest>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
"""


def reviewer_home() -> Path:
    return Path(__file__).resolve().parents[1]


def source_dir() -> Path:
    return reviewer_home() / "cursor" / "extensions" / "review-apply"


def write_vsix(src: Path, vsix: Path, version: str, description: str) -> None:
    manifest = MANIFEST.format(
        name=NAME,
        version=version,
        publisher=PUBLISHER,
        description=escape(description or "review apply"),
    )
    vsix.parent.mkdir(parents=True, exist_ok=True)
    if vsix.exists():
        vsix.unlink()
    with zipfile.ZipFile(vsix, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extension.vsixmanifest", manifest)
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.write(src / "package.json", "extension/package.json")
        archive.write(src / "extension.js", "extension/extension.js")


def find_cursor() -> str | None:
    found = shutil.which("cursor")
    if found:
        return found
    for candidate in (
        Path("/usr/local/bin/cursor"),
        Path("/opt/homebrew/bin/cursor"),
        Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def windows_cursor_cmd() -> Path | None:
    users = Path("/mnt/c/Users")
    if not users.is_dir():
        return None
    rels = (
        "AppData/Local/Programs/cursor/resources/app/bin/cursor.cmd",
        "AppData/Local/Programs/Cursor/resources/app/bin/cursor.cmd",
        "Desktop/Cursor/Priv/resources/app/bin/cursor.cmd",
    )
    for home in sorted(users.iterdir()):
        for rel in rels:
            candidate = home / rel
            if candidate.is_file():
                return candidate
    return None


def to_windows_path(path: Path) -> str:
    text = str(path.resolve())
    if text.startswith("/mnt/") and len(text) > 6 and text[6] == "/":
        drive = text[5].upper()
        return drive + ":" + text[6:].replace("/", "\\")
    return text


def install_vsix(cursor: str, vsix: Path, label: str) -> int:
    completed = subprocess.run(
        [cursor, "--install-extension", str(vsix), "--force"],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.stdout:
        print(f"{label}: {completed.stdout.strip()}")
    if completed.stderr:
        print(f"{label}: {completed.stderr.strip()}")
    if completed.returncode != 0:
        print(f"{label}: cursor --install-extension failed ({completed.returncode})")
    return completed.returncode


def main() -> int:
    src = source_dir()
    if not (src / "package.json").is_file() or not (src / "extension.js").is_file():
        raise SystemExit("missing review-apply extension sources")
    package = json.loads((src / "package.json").read_text(encoding="utf-8"))
    version = str(package.get("version") or "0.1.0")
    description = str(package.get("description") or "review apply")
    vsix = Path(tempfile.gettempdir()) / f"{EXTENSION_ID}-{version}.vsix"
    write_vsix(src, vsix, version, description)
    print(f"packed {vsix.name}")
    cursor = find_cursor()
    if not cursor:
        print("cursor CLI not found; VSIX is ready for a manual install")
        return 1
    rc = install_vsix(cursor, vsix, "cursor")
    win_cursor = windows_cursor_cmd()
    if win_cursor is not None:
        win_vsix = Path("/mnt/c/Users") / win_cursor.parts[4] / "AppData/Local/Temp" / vsix.name
        try:
            shutil.copy2(vsix, win_vsix)
            cmd_rc = subprocess.run(
                [
                    "cmd.exe",
                    "/c",
                    to_windows_path(win_cursor),
                    "--install-extension",
                    to_windows_path(win_vsix),
                    "--force",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            if cmd_rc.stdout:
                print(f"windows-host: {cmd_rc.stdout.strip()}")
            if cmd_rc.stderr:
                print(f"windows-host: {cmd_rc.stderr.strip()}")
            if cmd_rc.returncode == 0:
                print(f"windows-host: installed {EXTENSION_ID}")
            else:
                print("windows-host: install failed — canvas buttons need the UI extension")
                rc = cmd_rc.returncode or rc
        except OSError as exc:
            print(f"windows-host: copy/install failed ({exc})")
    listed = subprocess.run(
        [cursor, "--list-extensions"],
        check=False,
        text=True,
        capture_output=True,
    )
    names = listed.stdout or ""
    if EXTENSION_ID in names:
        print(f"listed {EXTENSION_ID}")
    else:
        print(f"installed, but --list-extensions does not show {EXTENSION_ID} yet")
    print("Reload the Cursor window to activate review-apply")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
