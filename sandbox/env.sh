# Shared env for database.sh, run-api.sh — source from repo root scripts only.
# Usage: source "$(dirname "$0")/sandbox/env.sh"   (after setting ROOT)

: "${ROOT:?ROOT must be set to repo root before sourcing env.sh}"

_load_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] || [[ "$line" != *"="* ]] && continue
    local key="${line%%=*}"
    local val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "$val" == \"*\" && "$val" == *\" ]]; then
      val="${val:1:${#val}-2}"
    elif [[ "$val" == \'*\' && "$val" == *\' ]]; then
      val="${val:1:${#val}-2}"
    fi
    export "$key=$val"
  done < "$f"
}

_load_env_file "$ROOT/sandbox/.env"
_load_env_file "$ROOT/.env"

_resolve_python() {
  local p="${PYTHON:-}"
  if [[ -n "$p" ]]; then
    if [[ -x "$p" ]]; then
      echo "$p"
      return
    fi
    if [[ -x "$ROOT/$p" ]]; then
      echo "$ROOT/$p"
      return
    fi
    echo "PYTHON in sandbox/.env is not executable: $p" >&2
    return 1
  fi
  if [[ -x "$ROOT/44ma/.venv/bin/python" ]]; then
    echo "$ROOT/44ma/.venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  echo "Set PYTHON= in sandbox/.env (see sandbox/.env.example)." >&2
  return 1
}

PYTHON="$(_resolve_python)" || exit 1
export PYTHON

# Default repo PYTHONPATH (no manual export in terminal)
if [[ -z "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="${ROOT}:${ROOT}/44ma:${ROOT}/hybrid_swing:${ROOT}/KALI/src:${ROOT}/financially free"
fi

# Sensible API defaults if not in .env
export ANALYZE_API_KEY="${ANALYZE_API_KEY:-dev-secret}"
export API_PORT="${API_PORT:-8000}"
export USE_NIFTY100="${USE_NIFTY100:-true}"
