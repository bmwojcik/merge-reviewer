const fs = require("fs");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const APPLY_NAME = "review-change.apply.request";
const SKIP_NAME = "review-change.skip.request";
const MR_NAME = "review-change.mr.request";
const CANVAS_NAME = "review-change-preview.canvas.tsx";
const APPLY_TEXT = "@" + "apply-review-fixes";
const SKIP_TEXT = "@" + "skip-review-fixes";
const DEBOUNCE_MS = 2000;
const TRIGGER_NAMES = [APPLY_NAME, SKIP_NAME, MR_NAME];
const VERSION = "0.1.0";

let lastAt = 0;
let busy = false;
let canvasBusy = false;

function stamp() {
  const now = new Date();
  const hours = String(now.getHours()).padStart(2, "0");
  const minutes = String(now.getMinutes()).padStart(2, "0");
  const seconds = String(now.getSeconds()).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function envFromFile(name) {
  if (process.env[name]) {
    return process.env[name];
  }
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  const file = path.join(base, "review-change", "cursor.env");
  let text = "";
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) {
      continue;
    }
    const eq = trimmed.indexOf("=");
    const key = trimmed.slice(0, eq).trim();
    if (key !== name) {
      continue;
    }
    return trimmed.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "");
  }
  return "";
}

function reviewRoot() {
  const folders = vscode.workspace.workspaceFolders || [];
  let withTrigger;
  for (const folder of folders) {
    const trigger = path.join(folder.uri.fsPath, ".cursor", APPLY_NAME);
    if (!withTrigger && fs.existsSync(trigger)) {
      withTrigger = folder;
    }
  }
  return withTrigger || folders[0];
}

function engineBin() {
  const home = envFromFile("MERGE_REVIEWER_HOME");
  if (home) {
    const bin = path.join(home, "bin", "post-commit-review");
    if (fs.existsSync(bin)) {
      return bin;
    }
  }
  const template = envFromFile("CURSOR_TEMPLATE_HOME");
  if (template) {
    const bin = path.join(
      path.dirname(template),
      "merge-reviewer",
      "bin",
      "post-commit-review"
    );
    if (fs.existsSync(bin)) {
      return bin;
    }
  }
  const folder = reviewRoot();
  if (folder) {
    const bin = path.join(
      folder.uri.fsPath,
      "tools",
      "merge-reviewer",
      "bin",
      "post-commit-review"
    );
    if (fs.existsSync(bin)) {
      return bin;
    }
  }
  return "";
}

function log(message) {
  const folder = reviewRoot();
  if (!folder) {
    return;
  }
  const logPath = path.join(folder.uri.fsPath, "var", "review-change.log");
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `${stamp()} ${message}\n`);
  } catch {
    return;
  }
}

function isTrigger(uri, name) {
  const filePath = (uri.fsPath || "").replace(/\\/g, "/");
  return (
    filePath.endsWith("/.cursor/" + name) || filePath.endsWith(".cursor/" + name)
  );
}

function tabUri(tab) {
  const input = tab.input;
  if (!input) {
    return undefined;
  }
  if (input.uri) {
    return input.uri;
  }
  if (input.modified) {
    return input.modified;
  }
  return undefined;
}

async function closeAllTriggerTabs() {
  const tabs = [];
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const uri = tabUri(tab);
      if (!uri) {
        continue;
      }
      if (TRIGGER_NAMES.some((name) => isTrigger(uri, name))) {
        tabs.push(tab);
      }
    }
  }
  if (tabs.length) {
    await vscode.window.tabGroups.close(tabs, true);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function claimClick() {
  const now = Date.now();
  if (busy || now - lastAt < DEBOUNCE_MS) {
    return false;
  }
  lastAt = now;
  busy = true;
  return true;
}

async function focusChat() {
  try {
    await vscode.commands.executeCommand("aichat.newfollowupaction");
    return;
  } catch (error) {
    log("newfollowupaction failed: " + String(error));
  }
  try {
    await vscode.commands.executeCommand("composer.focusComposer");
  } catch (focusError) {
    log("focusComposer failed: " + String(focusError));
  }
}

async function pasteIntoCurrentChat(text) {
  const previous = await vscode.env.clipboard.readText();
  try {
    await vscode.env.clipboard.writeText(text);
    await vscode.commands.executeCommand("editor.action.clipboardPasteAction");
    log("pasted skill");
    return true;
  } catch (error) {
    log("clipboard paste failed: " + String(error));
    return false;
  } finally {
    await vscode.env.clipboard.writeText(previous);
  }
}

function startCreateMr() {
  const folder = reviewRoot();
  if (!folder) {
    log("create-mr: no workspace");
    return;
  }
  const bin = engineBin();
  if (!bin) {
    log("create-mr: engine not found");
    return;
  }
  const term = vscode.window.createTerminal({
    name: "review-change MR",
    cwd: folder.uri.fsPath,
  });
  term.show(false);
  term.sendText(`python3 ${JSON.stringify(bin)} --create-mr`);
  log("create-mr: terminal");
}

async function handle(kind) {
  if (!claimClick()) {
    return;
  }
  try {
    log("click " + kind);
    if (kind === "mr") {
      startCreateMr();
      await closeAllTriggerTabs();
      await openRenderedCanvas();
      return;
    }
    // Multi-root: openFile replaces the canvas with the trigger tab.
    // Focus chat, close that tab, then paste — otherwise the paste
    // lands in the trigger file. A single-root window never hits this.
    await focusChat();
    await closeAllTriggerTabs();
    await sleep(150);
    const text = kind === "skip" ? SKIP_TEXT : APPLY_TEXT;
    const ok = await pasteIntoCurrentChat(text);
    await sleep(200);
    await openRenderedCanvas();
    if (!ok) {
      void vscode.window.showInformationMessage(
        "review: could not paste into chat — Command Palette: Paste apply-review-fixes"
      );
    }
  } finally {
    lastAt = Date.now();
    busy = false;
  }
}

function kindForUri(uri) {
  if (isTrigger(uri, APPLY_NAME)) {
    return "apply";
  }
  if (isTrigger(uri, SKIP_NAME)) {
    return "skip";
  }
  if (isTrigger(uri, MR_NAME)) {
    return "mr";
  }
  return "";
}

function isReviewCanvasUri(uri) {
  if (!uri || uri.scheme === "cursor-canvas") {
    return false;
  }
  const filePath = (uri.fsPath || "").replace(/\\/g, "/");
  return filePath.endsWith("/canvases/" + CANVAS_NAME);
}

function cursorSlugsFor(folderPath) {
  const raw = folderPath.replace(/\\/g, "/").replace(/^\//, "").replace(/\//g, "-");
  const dotted = raw.replace(/\./g, "-");
  return raw === dotted ? [raw] : [raw, dotted];
}

function reviewCanvasPath() {
  const folders = vscode.workspace.workspaceFolders || [];
  const home = os.homedir();
  const candidates = [];
  for (const folder of folders) {
    for (const slug of cursorSlugsFor(folder.uri.fsPath)) {
      candidates.push(
        path.join(home, ".cursor", "projects", slug, "canvases", CANVAS_NAME)
      );
    }
  }
  const root = reviewRoot();
  if (root) {
    for (const slug of cursorSlugsFor(root.uri.fsPath)) {
      candidates.push(
        path.join(home, ".cursor", "projects", slug, "canvases", CANVAS_NAME)
      );
    }
  }
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return candidates[0] || "";
}

async function closeReviewCanvasTextTabs() {
  const tabs = [];
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const uri = tabUri(tab);
      if (uri && isReviewCanvasUri(uri)) {
        tabs.push(tab);
      }
    }
  }
  if (tabs.length) {
    await vscode.window.tabGroups.close(tabs, true);
  }
}

async function openRenderedCanvas(fsPath) {
  const target = fsPath || reviewCanvasPath();
  if (!target) {
    log("open canvas: no path");
    return false;
  }
  try {
    await vscode.commands.executeCommand("canvas.openFileInWorkbench", target);
    log("opened canvas via canvas.openFileInWorkbench");
    return true;
  } catch (error) {
    log("canvas.openFileInWorkbench failed: " + String(error));
  }
  try {
    const canvasUri = vscode.Uri.from({
      scheme: "cursor-canvas",
      path: target,
    });
    await vscode.commands.executeCommand(
      "vscode.openWith",
      canvasUri,
      "workbench.editor.canvas"
    );
    log("opened canvas via vscode.openWith");
    return true;
  } catch (error) {
    log("vscode.openWith failed: " + String(error));
  }
  try {
    await vscode.commands.executeCommand("workbench.action.openCanvas");
    log("opened canvas via workbench.action.openCanvas");
    return true;
  } catch (error) {
    log("workbench.action.openCanvas failed: " + String(error));
  }
  return false;
}

async function redirectCanvasFile(uri) {
  if (!isReviewCanvasUri(uri) || canvasBusy) {
    return;
  }
  canvasBusy = true;
  try {
    const opened = await openRenderedCanvas(uri.fsPath);
    await closeReviewCanvasTextTabs();
    if (!opened) {
      void vscode.window.showInformationMessage(
        "review: Command Palette → View: Open Canvas → review-change-preview"
      );
    }
  } finally {
    setTimeout(() => {
      canvasBusy = false;
    }, 400);
  }
}

function activate(context) {
  log("extension: activated " + VERSION);
  context.subscriptions.push(
    vscode.commands.registerCommand("review.pasteApply", () =>
      handle("apply")
    ),
    vscode.commands.registerCommand("review.pasteSkip", () =>
      handle("skip")
    ),
    vscode.commands.registerCommand("review.openCanvas", () =>
      openRenderedCanvas()
    ),
    vscode.window.registerUriHandler({
      handleUri(uri) {
        const pathName = (uri.path || "").replace(/\/+$/, "");
        if (pathName === "/skip" || pathName.endsWith("/skip")) {
          void handle("skip");
          return;
        }
        if (pathName === "/mr" || pathName.endsWith("/mr")) {
          void handle("mr");
          return;
        }
        if (pathName === "/canvas" || pathName.endsWith("/canvas")) {
          void openRenderedCanvas();
          return;
        }
        void handle("apply");
      },
    }),
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) {
        void redirectCanvasFile(editor.document.uri);
      }
    }),
    vscode.workspace.onDidOpenTextDocument((doc) => {
      const kind = kindForUri(doc.uri);
      if (kind) {
        void handle(kind);
      }
    }),
    vscode.window.onDidChangeTextEditorSelection((event) => {
      const kind = kindForUri(event.textEditor.document.uri);
      if (kind) {
        const col = event.selections[0]?.start.character ?? 0;
        log("selection " + kind + " col=" + col);
        void handle(kind);
      }
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
