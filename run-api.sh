#!/usr/bin/env bash
# Start sandbox FastAPI — config from sandbox/.env only (no shell exports).
# Usage: ./run-api.sh

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

echo "API http://0.0.0.0:${API_PORT}  (Python: $PYTHON)"

exec "$PYTHON" -m uvicorn sandbox.api.main:app \
  --reload \
  --host 0.0.0.0 \
  --port "$API_PORT"
