#!/usr/bin/env bash
# Laptop runtime. Writes MERGE_REVIEWER_HOME into ~/.config/review-change/cursor.env.
# Never prints a key.
#   cursor (default): agent CLI, cursor-sdk, review-apply extension, dashboard key.
#   claude: checks the claude CLI and that it is logged in. Nothing is installed or
#           stored; no Cursor key is asked for.
#   both: all of the above.
#
#   bash install/install-runtime.sh [--host cursor|claude|both]
#   bash install/install-runtime.sh --non-interactive
set -u

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
KEY_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/review-change"
KEY_FILE="$KEY_DIR/cursor.env"
DASHBOARD_URL="https://cursor.com/dashboard/integrations"
NONINTERACTIVE=0
SHELL_RC=""

HOST=cursor
while [ "$#" -gt 0 ]; do
  arg=$1
  shift
  case "$arg" in
    --non-interactive|-y) NONINTERACTIVE=1 ;;
    --host=*) HOST=${arg#--host=} ;;
    --host)
      [ "$#" -gt 0 ] || { printf 'error: --host needs cursor, claude, or both\n' >&2; exit 2; }
      HOST=$1
      shift
      ;;
    --help|-h)
      sed -n '1,14p' "$0"
      exit 0
      ;;
  esac
done
case "$HOST" in
  cursor|claude|both) ;;
  *) printf 'error: --host must be cursor, claude, or both\n' >&2; exit 2 ;;
esac
host_has() { [ "$HOST" = both ] || [ "$HOST" = "$1" ]; }

if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
  SHELL_RC="$HOME/.zshrc"
elif [ -n "${BASH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "bash" ]; then
  SHELL_RC="$HOME/.bashrc"
else
  SHELL_RC="$HOME/.profile"
fi

log() { printf '%s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

is_crsr_key() {
  case "$1" in
    crsr_????????*) return 0 ;;
    *) return 1 ;;
  esac
}

open_url() {
  local url="$1"
  if command -v wslview >/dev/null 2>&1; then
    wslview "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v explorer.exe >/dev/null 2>&1; then
    explorer.exe "$url" >/dev/null 2>&1 || true
  else
    log "Open this URL: $url"
  fi
}

ensure_path() {
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
  esac
  if [ -f "$SHELL_RC" ] && ! grep -qF '.local/bin' "$SHELL_RC" 2>/dev/null; then
    printf '\n# Cursor Agent CLI\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$SHELL_RC"
    log "Added ~/.local/bin to the shell rc file"
  fi
}

upsert_env() {
  key=$1
  value=$2
  mkdir -p "$KEY_DIR"
  umask 077
  tmp=$(mktemp)
  if [ -f "$KEY_FILE" ]; then
    grep -v "^${key}=" "$KEY_FILE" > "$tmp" || true
  else
    : > "$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$KEY_FILE"
  chmod 600 "$KEY_FILE"
}

install_agent_cli() {
  if command -v agent >/dev/null 2>&1; then
    log "agent CLI: present"
    return 0
  fi
  log "Installing Cursor Agent CLI"
  if ! curl -fsSL https://cursor.com/install | bash; then
    warn "curl install failed. Install the agent CLI manually, then re-run."
    return 1
  fi
  ensure_path
  command -v agent >/dev/null 2>&1 || die "agent not on PATH after install"
}

login_agent() {
  command -v agent >/dev/null 2>&1 || return 0
  if agent status >/dev/null 2>&1; then
    log "agent session: logged in"
    return 0
  fi
  if [ "$NONINTERACTIVE" -eq 1 ]; then
    warn "agent not logged in. Run: agent login"
    return 0
  fi
  log "Opening browser login for the agent CLI"
  agent login || warn "agent login failed; print mode may still work with the dashboard key"
}

install_cursor_sdk() {
  command -v python3 >/dev/null 2>&1 || die "python3 is required"
  if python3 -c 'import cursor_sdk' 2>/dev/null; then
    log "cursor-sdk: already installed"
    return 0
  fi
  log "Installing cursor-sdk (pip install --user)"
  python3 -m pip install --user --upgrade cursor-sdk || warn "pip install cursor-sdk failed"
}

extract_existing_key() {
  if [ -n "${CURSOR_API_KEY:-}" ] && is_crsr_key "$CURSOR_API_KEY"; then
    printf '%s' "$CURSOR_API_KEY"
    return 0
  fi
  if [ -f "$KEY_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$KEY_FILE"
    set +a
    if [ -n "${CURSOR_API_KEY:-}" ] && is_crsr_key "$CURSOR_API_KEY"; then
      printf '%s' "$CURSOR_API_KEY"
      return 0
    fi
  fi
  return 1
}

prompt_api_key() {
  local existing=""
  if existing="$(extract_existing_key)"; then
    log "CURSOR_API_KEY already present"
    upsert_env CURSOR_API_KEY "$existing"
    export CURSOR_API_KEY="$existing"
    return 0
  fi
  log "Create a dashboard user API key (it starts with crsr_ and is shown once)."
  log "$DASHBOARD_URL"
  if [ "$NONINTERACTIVE" -eq 1 ]; then
    warn "No crsr_ key found. Re-run without --non-interactive to paste it."
    open_url "$DASHBOARD_URL"
    return 0
  fi
  open_url "$DASHBOARD_URL"
  printf 'Paste the dashboard key (input hidden), or empty to skip: '
  if [ -n "${ZSH_VERSION:-}" ]; then
    read -s KEY
    printf '\n'
  elif [ -n "${BASH_VERSION:-}" ]; then
    read -r -s KEY
    printf '\n'
  else
    read -r KEY
  fi
  if [ -z "${KEY:-}" ]; then
    warn "Skipped the API key. The SDK path stays off until you re-run this script."
    return 0
  fi
  KEY="$(printf '%s' "$KEY" | tr -d '[:space:]')"
  if ! is_crsr_key "$KEY"; then
    warn "That does not look like a dashboard key. Not saved."
    return 1
  fi
  upsert_env CURSOR_API_KEY "$KEY"
  export CURSOR_API_KEY="$KEY"
  log "Saved the dashboard key (value not shown)."
}

source_env_from_shell() {
  if [ -n "$SHELL_RC" ] && [ -f "$SHELL_RC" ]; then
    if ! grep -qF 'review-change/cursor.env' "$SHELL_RC" 2>/dev/null; then
      printf '\n# merge-reviewer (Cursor SDK). File mode 600 — do not commit.\n[ -f %s ] && set -a && . %s && set +a\n' \
        "$KEY_FILE" "$KEY_FILE" >> "$SHELL_RC"
      log "Shell rc now sources the review-change env file"
    fi
  fi
}

install_extension() {
  if python3 "$ROOT/install/install-extension.py"; then
    log "review-apply extension: installed (reload the Cursor window)"
  else
    warn "review-apply extension: install failed or cursor CLI missing"
  fi
}

check_claude() {
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) PATH="$HOME/.local/bin:$PATH" ;;
  esac
  if ! command -v claude >/dev/null 2>&1; then
    warn "claude CLI not found. Install Claude Code (https://code.claude.com/docs/en/setup), then re-run."
    return 1
  fi
  log "claude CLI: $(claude --version 2>/dev/null | head -n 1)"
  # Read-only status check. Only the loggedIn flag is looked at; nothing is printed from it.
  if claude auth status 2>/dev/null | grep -Eq '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
    log "claude session: logged in"
  elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    log "claude session: ANTHROPIC_API_KEY is set (API billing)"
  else
    warn "claude is not logged in. Run: claude  (then /login)"
    return 1
  fi
}

CLAUDE_OK=skipped
if host_has claude; then
  if check_claude; then CLAUDE_OK=ok; else CLAUDE_OK=problem; fi
fi

if host_has cursor; then
  ensure_path
  install_agent_cli || true
  login_agent
  install_cursor_sdk
fi
upsert_env MERGE_REVIEWER_HOME "$ROOT"
log "MERGE_REVIEWER_HOME written"
if host_has cursor; then
  prompt_api_key || true
  source_env_from_shell
  install_extension
fi

log ""
log "=== merge-reviewer runtime (host: $HOST) ==="
if host_has cursor; then
  log "agent: $(command -v agent 2>/dev/null || echo missing)"
  if python3 -c 'import cursor_sdk' 2>/dev/null; then
    log "sdk: installed"
  else
    log "sdk: missing"
  fi
  if [ -n "${CURSOR_API_KEY:-}" ] && is_crsr_key "$CURSOR_API_KEY"; then
    log "api key: present"
  else
    log "api key: missing"
  fi
  log "Reload the Cursor window after linking a project."
fi
if host_has claude; then
  log "claude: $(command -v claude 2>/dev/null || echo missing) ($CLAUDE_OK)"
  log "Link projects with: bash install/link-project.sh <project> --host $HOST"
fi
