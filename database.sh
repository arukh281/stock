#!/usr/bin/env bash
# Interactive Supabase paper-ledger reset. Config: sandbox/.env
# Usage: ./database.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# shellcheck source=sandbox/env.sh
source "$ROOT/sandbox/env.sh"

if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
  echo "Missing Supabase config in sandbox/.env" >&2
  echo "  cp sandbox/.env.example sandbox/.env" >&2
  exit 1
fi

if [[ "$SUPABASE_URL" == *YOUR_PROJECT* ]] || [[ "$SUPABASE_SERVICE_ROLE_KEY" == *your-service-role* ]]; then
  echo "sandbox/.env still has placeholder Supabase values." >&2
  echo "  Edit SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (Supabase → Settings → API)." >&2
  exit 1
fi

if ! "$PYTHON" -c "import supabase" 2>/dev/null; then
  echo "Installing sandbox Python deps..."
  "$PYTHON" -m pip install -q -r "$ROOT/sandbox/requirements.txt"
fi

echo "Using Python: $PYTHON"
echo "Supabase: ${SUPABASE_URL}"

exec "$PYTHON" -m sandbox.scripts.db_admin
