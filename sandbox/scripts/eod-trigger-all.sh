#!/usr/bin/env bash
# Sequential EOD analyze triggers for Render free tier:
# - Wake cold API (/health retries)
# - One algo at a time (avoids 512MB OOM from parallel jobs)
# - Wait for each run to finish before starting the next
# - Keepalive pings during long runs (Render sleeps after ~15m idle)
#
# Env: API_URL, ANALYZE_API_KEY (required)
# Optional: EOD_SUMMARY_FILE, KEEPALIVE_INTERVAL_SEC, POLL_INTERVAL_SEC, RUN_TIMEOUT_SEC

set -euo pipefail

API_URL="${API_URL:?Set API_URL}"
ANALYZE_API_KEY="${ANALYZE_API_KEY:?Set ANALYZE_API_KEY}"
BASE="${API_URL%/}"
SUMMARY_FILE="${EOD_SUMMARY_FILE:-eod-summary.json}"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

KEEPALIVE_INTERVAL_SEC="${KEEPALIVE_INTERVAL_SEC:-300}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-30}"
RUN_TIMEOUT_SEC="${RUN_TIMEOUT_SEC:-2700}"
WAKE_MAX_ATTEMPTS="${WAKE_MAX_ATTEMPTS:-18}"
WAKE_SLEEP_SEC="${WAKE_SLEEP_SEC:-10}"
POST_MAX_ATTEMPTS="${POST_MAX_ATTEMPTS:-4}"
POST_MAX_TIME="${POST_MAX_TIME:-180}"
CURL_CONNECT_TIMEOUT="${CURL_CONNECT_TIMEOUT:-30}"

# slug|algo_id (DB /runs path)
ALGOS=(
  "44ma|44ma"
  "44ma-stacked-2ma|44ma_stacked_2ma"
  "financially-free|financially_free"
  "kali|kali"
)

last_keepalive=0
failures=0

init_summary() {
  jq -n \
    --arg started_at "${STARTED_AT}" \
    --arg api_url "${BASE}" \
    '{started_at: $started_at, api_url: $api_url, failure_count: 0, runs: []}' \
    > "${SUMMARY_FILE}"
}

append_run_json() {
  local entry="$1"
  local tmp
  tmp=$(mktemp)
  jq --argjson entry "${entry}" '.runs += [$entry]' "${SUMMARY_FILE}" > "${tmp}"
  mv "${tmp}" "${SUMMARY_FILE}"
}

fetch_run_entry() {
  local slug="$1"
  local algo_id="$2"
  local run_id="$3"
  local wait_ok="${4:-true}"
  local payload
  if [[ -n "${run_id}" ]] && payload=$(api GET "${BASE}/runs/id/${run_id}" 2>/dev/null); then
    echo "${payload}" | jq \
      --arg slug "${slug}" \
      --arg algo_id "${algo_id}" \
      --argjson trigger_ok "${wait_ok}" \
      '{
        slug: $slug,
        algo_id: $algo_id,
        run_id: .id,
        status: .status,
        trigger_ok: $trigger_ok,
        session_date: (.summary.session_date // null),
        skipped: (.summary.skipped // null),
        line_count: (.summary.line_count // null),
        equity: (.summary.equity // null),
        error_message: (.error_message // null),
        sample_lines: ((.summary.lines // []) | .[:8])
      }'
  else
    jq -n \
      --arg slug "${slug}" \
      --arg algo_id "${algo_id}" \
      --arg run_id "${run_id}" \
      --argjson trigger_ok "${wait_ok}" \
      '{
        slug: $slug,
        algo_id: $algo_id,
        run_id: ($run_id | if . == "" then null else . end),
        status: "failed",
        trigger_ok: $trigger_ok,
        error: "Could not fetch run details from API"
      }'
  fi
}

finalize_summary() {
  local tmp
  tmp=$(mktemp)
  jq \
    --arg finished_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson failure_count "${failures}" \
    '.finished_at = $finished_at | .failure_count = $failure_count' \
    "${SUMMARY_FILE}" > "${tmp}"
  mv "${tmp}" "${SUMMARY_FILE}"
}

api() {
  local method="$1"
  shift
  curl -fsS -X "$method" \
    -H "X-API-Key: ${ANALYZE_API_KEY}" \
    -H "Content-Type: application/json" \
    --connect-timeout "${CURL_CONNECT_TIMEOUT}" \
    "$@"
}

maybe_keepalive() {
  local now
  now=$(date +%s)
  if (( now - last_keepalive >= KEEPALIVE_INTERVAL_SEC )); then
    last_keepalive=$now
    echo "  [keepalive] GET /health"
    curl -fsS --connect-timeout "${CURL_CONNECT_TIMEOUT}" --max-time 90 \
      "${BASE}/health" >/dev/null || true
  fi
}

wake_api() {
  echo "== Waking API (cold start on Render free tier) =="
  local attempt=1
  while (( attempt <= WAKE_MAX_ATTEMPTS )); do
    echo "  attempt ${attempt}/${WAKE_MAX_ATTEMPTS}: GET /health"
    if curl -fsS --connect-timeout "${CURL_CONNECT_TIMEOUT}" --max-time 90 \
      "${BASE}/health" >/dev/null 2>&1; then
      echo "  API is up."
      return 0
    fi
    sleep "${WAKE_SLEEP_SEC}"
    (( attempt++ )) || true
  done
  echo "ERROR: API did not respond to /health in time." >&2
  return 1
}

find_running_run_id() {
  local algo_id="$1"
  api GET "${BASE}/runs/${algo_id}?limit=5" \
    | jq -r '[.[] | select(.status == "running")][0].id // empty'
}

wait_for_run() {
  local algo_id="$1"
  local run_id="$2"
  local slug="$3"
  local start_ts now elapsed status
  start_ts=$(date +%s)

  echo "  waiting for run ${run_id} (${slug}) …"
  while true; do
    maybe_keepalive
    status=$(api GET "${BASE}/runs/id/${run_id}" | jq -r '.status // empty')
    if [[ -z "${status}" ]]; then
      echo "  WARN: run ${run_id} not found; treating as done." >&2
      return 0
    fi
    if [[ "${status}" != "running" ]]; then
      echo "  run ${run_id} finished: status=${status}"
      if [[ "${status}" != "ok" ]]; then
        echo "  ERROR: analyze failed for ${slug} (status=${status})" >&2
        return 1
      fi
      return 0
    fi
    now=$(date +%s)
    elapsed=$(( now - start_ts ))
    if (( elapsed >= RUN_TIMEOUT_SEC )); then
      echo "  ERROR: timeout after ${RUN_TIMEOUT_SEC}s waiting for ${slug}" >&2
      return 1
    fi
    echo "  still running (${elapsed}s elapsed) …"
    sleep "${POLL_INTERVAL_SEC}"
  done
}

post_analyze() {
  local slug="$1"
  local attempt=1
  local resp code run_id
  while (( attempt <= POST_MAX_ATTEMPTS )); do
    echo "  POST /analyze/${slug} (attempt ${attempt}/${POST_MAX_ATTEMPTS})" >&2
    code=0
    resp=$(curl -sS -w "\n%{http_code}" -X POST \
      -H "X-API-Key: ${ANALYZE_API_KEY}" \
      -H "Content-Type: application/json" \
      --connect-timeout "${CURL_CONNECT_TIMEOUT}" \
      --max-time "${POST_MAX_TIME}" \
      "${BASE}/analyze/${slug}") || code=$?

    if (( code != 0 )); then
      echo "  POST failed (curl exit ${code}); retrying…" >&2
      sleep 20
      (( attempt++ )) || true
      continue
    fi

    local http_body http_status
    http_status=$(echo "${resp}" | tail -n1)
    http_body=$(echo "${resp}" | sed '$d')

    if [[ "${http_status}" == "200" ]]; then
      run_id=$(echo "${http_body}" | jq -r '.run_id // empty')
      if [[ -z "${run_id}" ]]; then
        echo "  ERROR: no run_id in response: ${http_body}" >&2
        return 1
      fi
      echo "  started run_id=${run_id}" >&2
      echo "${run_id}"
      return 0
    fi

    if [[ "${http_status}" == "409" ]]; then
      echo "  analyze already running (409); will wait for existing run" >&2
      echo "${http_body}" >&2
      return 2
    fi

    echo "  POST HTTP ${http_status}: ${http_body}" >&2
    sleep 20
    (( attempt++ )) || true
  done
  return 1
}

run_algo() {
  local slug="$1"
  local algo_id="$2"
  local run_id rc wait_rc=0 entry

  echo ""
  echo "== ${slug} (${algo_id}) =="

  rc=0
  run_id=$(post_analyze "${slug}") || rc=$?

  if (( rc == 2 )); then
    run_id=$(find_running_run_id "${algo_id}")
    if [[ -z "${run_id}" ]]; then
      echo "  ERROR: 409 but no running run in /runs/${algo_id}" >&2
      entry=$(jq -n --arg slug "${slug}" --arg algo_id "${algo_id}" \
        '{slug: $slug, algo_id: $algo_id, status: "failed", trigger_ok: false, error: "409 but no running run"}')
      append_run_json "${entry}"
      return 1
    fi
    echo "  joining existing run_id=${run_id}"
  elif (( rc != 0 )) || [[ -z "${run_id}" ]]; then
    entry=$(jq -n --arg slug "${slug}" --arg algo_id "${algo_id}" \
      '{slug: $slug, algo_id: $algo_id, status: "failed", trigger_ok: false, error: "POST /analyze failed"}')
    append_run_json "${entry}"
    return 1
  fi

  wait_for_run "${algo_id}" "${run_id}" "${slug}" || wait_rc=$?
  entry=$(fetch_run_entry "${slug}" "${algo_id}" "${run_id}" "$( [[ ${wait_rc} -eq 0 ]] && echo true || echo false )")
  append_run_json "${entry}"
  return "${wait_rc}"
}

main() {
  command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

  init_summary
  wake_api
  last_keepalive=$(date +%s)

  for entry in "${ALGOS[@]}"; do
    slug="${entry%%|*}"
    algo_id="${entry##*|}"
    if run_algo "${slug}" "${algo_id}"; then
      echo "== ${slug}: OK =="
    else
      echo "== ${slug}: FAILED ==" >&2
      failures=$(( failures + 1 ))
    fi
    maybe_keepalive
  done

  finalize_summary
  echo ""
  echo "Summary written to ${SUMMARY_FILE}"
  if (( failures > 0 )); then
    echo "EOD trigger finished with ${failures} failure(s)." >&2
    exit 1
  fi
  echo "EOD trigger finished: all algos OK."
}

main "$@"
