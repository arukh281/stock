#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

AUTH_FILE="$ROOT/playwright/.auth/user.json"
SESSION_FILE="$ROOT/.screener-session.json"

die() {
  echo "Error: $*" >&2
  exit 1
}

need_auth() {
  if [[ ! -f "$AUTH_FILE" ]]; then
    echo "No Screener session at playwright/.auth/user.json"
    echo "Run once: npm run auth:save   (log in, then close the browser)"
    exit 1
  fi
}

normalize_query() {
  local q="$1" prev
  q="$(printf '%s' "$q" | sed -E 's/[[:space:]]+/ /g; s/^[[:space:]]+//; s/[[:space:]]+$//')"
  while true; do
    prev="$q"
    q="$(printf '%s' "$q" | sed -E 's/[[:space:]]+(AND|OR)[[:space:]]*$//I')"
    [[ "$q" == "$prev" ]] && break
  done
  # Drop paste junk only (e.g. "0.4> > > >" or trailing "> > >") — keep real comparators (> 15, < 0.5).
  q="$(printf '%s' "$q" | sed -E 's/([0-9.])[[:space:]]*(>[[:space:]]*)+/\1/g')"
  q="$(printf '%s' "$q" | sed -E 's/[[:space:]]*(>[[:space:]]+){2,}$//')"
  q="$(printf '%s' "$q" | sed -E 's/[[:space:]]+/ /g; s/^[[:space:]]+//; s/[[:space:]]+$//')"
  # Fix glued AND (e.g. "25000 ANDIs not" or "0.4AND" after junk strip)
  q="$(printf '%s' "$q" | sed -E 's/([0-9.])(AND|OR)/\1 \2/gI; s/AND([A-Za-z])/AND \1/g')"
  printf '%s' "$q"
}

read_query() {
  echo
  echo "Paste your Screener screen query (multiple lines OK)."
  echo "Each line is joined into one query. Finish with an empty line."
  echo "(Only Enter on the first line = cancel.)"
  echo
  local lines=() line
  while true; do
    read -r -p "> " line || break
    if [[ ${#lines[@]} -eq 0 && -z "${line//[[:space:]]/}" ]]; then
      return 1
    fi
    [[ -z "${line//[[:space:]]/}" ]] && break
    lines+=("$line")
  done

  QUERY=""
  for line in "${lines[@]}"; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    if [[ -n "$QUERY" ]]; then
      QUERY="$QUERY $line"
    else
      QUERY="$line"
    fi
  done
  QUERY="$(normalize_query "$QUERY")"
  [[ -n "${QUERY//[[:space:]]/}" ]] || return 1

  echo
  echo "Query sent to Screener:"
  echo "  $QUERY"
  echo
  read -r -p "Run this query? [Y/n]: " confirm || true
  case "${confirm:-Y}" in
    n|N|no|NO) return 1 ;;
  esac
}

run_list_query() {
  local query="$1"
  need_auth
  echo >&2
  echo "Running query on screener.in …" >&2
  local out
  if ! out="$(
  SCREENER_QUERY="$query" SCREENER_LIST_ONLY=1 npm run screener:list 2>&1
  )"; then
    echo "$out" >&2
    die "Query run failed."
  fi
  local line
  line="$(printf '%s\n' "$out" | grep '^SCREENER_RESULT:' | tail -1 || true)"
  [[ -n "$line" ]] || {
    echo "$out" >&2
    die "Could not read screen results from Playwright output."
  }
  printf '%s\n' "${line#SCREENER_RESULT:}"
}

save_session() {
  local query="$1" result_json="$2" take="$3"
  node -e "
    const fs = require('fs');
    fs.writeFileSync(process.argv[1], JSON.stringify({
      query: process.argv[2],
      result: JSON.parse(process.argv[3]),
      take: process.argv[4],
      savedAt: new Date().toISOString(),
    }, null, 2));
  " "$SESSION_FILE" "$query" "$result_json" "$take"
}

load_session() {
  [[ -f "$SESSION_FILE" ]] || return 1
  node -e "
    const fs = require('fs');
  try {
    const d = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
    process.stdout.write(JSON.stringify(d));
  } catch { process.exit(1); }
  " "$SESSION_FILE"
}

pick_count() {
  local total="$1"
  local take=""
  while true; do
    read -r -p "How many to take? (1-$total, or 'all'): " take || true
    take="${take// /}"
    [[ -z "$take" ]] && continue
    if [[ "$take" == "all" || "$take" == "ALL" ]]; then
      echo "$total"
      return
    fi
    if [[ "$take" =~ ^[0-9]+$ ]] && (( take >= 1 && take <= total )); then
      echo "$take"
      return
    fi
    echo "Enter a number between 1 and $total, or 'all'." >&2
  done
}

# Parallel export workers (default 2). Override: SCREENER_EXPORT_WORKERS=3
export SCREENER_EXPORT_WORKERS="${SCREENER_EXPORT_WORKERS:-2}"
# Delay second+ worker start (seconds) to reduce Screener rate limits. Default 45.
export SCREENER_WORKER_STAGGER_SEC="${SCREENER_WORKER_STAGGER_SEC:-45}"

write_export_manifest() {
  local out_dir="$1" result_json="$2" take="$3" query="$4"
  node -e "
    const fs = require('fs');
    const path = require('path');
    const r = JSON.parse(process.argv[1]);
    const takeArg = process.argv[2];
    const n = takeArg === 'all' ? r.companies.length : Math.min(parseInt(takeArg, 10), r.companies.length);
    const companies = r.companies.slice(0, n).map((c) => ({
      companyId: c.companyId,
      name: c.name,
      path: c.path,
    }));
    const outDir = process.argv[3];
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(
      path.join(outDir, 'export-manifest.json'),
      JSON.stringify({ query: process.argv[4], companies }, null, 2),
    );
    console.log(companies.length);
  " "$result_json" "$take" "$out_dir" "$query"
}

run_parallel_export() {
  local query="$1" result_json="$2" take="$3"
  local workers="$SCREENER_EXPORT_WORKERS"
  local stamp out_dir count w pid failed=0

  if ! [[ "$workers" =~ ^[0-9]+$ ]] || (( workers < 1 )); then
    die "SCREENER_EXPORT_WORKERS must be a positive integer (got: $workers)"
  fi

  stamp="$(node -e "console.log(new Date().toISOString().replace(/[:.]/g,'-'))")"
  out_dir="$ROOT/downloads/screen-export-${stamp}"
  mkdir -p "$out_dir"

  count="$(write_export_manifest "$out_dir" "$result_json" "$take" "$query")"

  echo
  echo "Exporting $count companies with $workers parallel worker(s) …"
  echo "  → $out_dir"
  echo "  Each company → .features.json (xlsx deleted immediately)"
  echo

  local pids=() stagger="$SCREENER_WORKER_STAGGER_SEC"
  for ((w = 0; w < workers; w++)); do
    if (( w > 0 )); then
      echo "[worker $((w + 1))/$workers] starts in ${stagger}s (stagger) …"
      sleep "$stagger"
    fi
    echo "[worker $((w + 1))/$workers] starting …"
    (
      SCREENER_EXPORT_ONLY=1 \
      SCREENER_EXPORT_MANIFEST="$out_dir/export-manifest.json" \
      SCREENER_EXPORT_DIR="$out_dir" \
      SCREENER_EXPORT_WORKER="$w" \
      SCREENER_EXPORT_WORKERS="$workers" \
      SCREENER_EXPORT_SKIP_FINALIZE=1 \
      npm run screener:export
    ) &
    pids+=($!)
  done

  for pid in "${pids[@]}"; do
    wait "$pid" || failed=1
  done

  if (( failed )); then
    echo
    echo "One or more workers failed. Partial .features.json in:"
    echo "  $out_dir"
    echo
    read -r -p "Retry missing companies now? [Y/n]: " retry_ans || true
    case "${retry_ans:-Y}" in
      n|N|no|NO) ;;
      *)
        retry_missing_export "$out_dir" && failed=0
        ;;
    esac
    if (( failed )); then
      echo "Or later: npm run screener:retry-export -- \"$out_dir\""
      echo "Or menu 4 to finalize partial batch only."
      return 1
    fi
  fi

  echo
  echo "All workers finished. Building features.compact.json …"
  npm run screener:finalize -- "$out_dir"
  echo
  echo "Done: $out_dir"
  echo "  features.compact.json + *.features.json  ← attach to LLM"
}

print_company_list() {
  local result_json="$1" take="$2"
  node -e "
    const d = JSON.parse(process.argv[1]);
    const take = process.argv[2];
    const n = take === 'all' ? d.companies.length : Math.min(parseInt(take, 10), d.companies.length);
    const list = d.companies.slice(0, n);
    console.log('');
    console.log('Companies to extract (' + list.length + '):');
    console.log('────────────────────────────────────────');
    list.forEach((c, i) => console.log(String(i + 1).padStart(3) + '. ' + c.name));
    console.log('────────────────────────────────────────');
  " "$result_json" "$take"
}

do_query_flow() {
  local with_export="${1:-0}"
  read_query || return 0
  local result_json total take

  result_json="$(run_list_query "$QUERY")"
  if [[ "$result_json" != '{'* ]]; then
    result_json="$(printf '%s\n' "$result_json" | awk '/^\{/{print; exit}')"
  fi
  total="$(node -e "console.log(JSON.parse(process.argv[1]).total)" "$result_json")"

  echo
  if (( total == 0 )); then
    echo "→ No companies matched your query."
    echo "  Try relaxing filters or fixing metric names on screener.in, then run again."
    return 0
  fi
  echo "→ $total companies matched your query."

  take="$(pick_count "$total")"
  print_company_list "$result_json" "$take"
  save_session "$QUERY" "$result_json" "$take"

  if [[ "$with_export" != "1" ]]; then
    echo
    read -r -p "Export Excel for these $take companies? [y/N]: " ans || true
    case "$ans" in
      y|Y|yes|YES) ;;
      *) return 0 ;;
    esac
  fi

  run_parallel_export "$QUERY" "$result_json" "$take"
}

do_export_from_session() {
  local session
  session="$(load_session)" || die "No saved session. Run a query first (option 1 or 2)."
  local query take total
  query="$(node -e "console.log(JSON.parse(process.argv[1]).query)" "$session")"
  take="$(node -e "console.log(JSON.parse(process.argv[1]).take)" "$session")"
  total="$(node -e "console.log(JSON.parse(process.argv[1]).result.total)" "$session")"

  echo "Last query: $query"
  echo "Last pick: $take of $total companies"
  print_company_list "$(node -e "console.log(JSON.stringify(JSON.parse(process.argv[1]).result))" "$session")" "$take"
  echo
  read -r -p "Export these? [y/N]: " ans || true
  case "$ans" in
    y|Y|yes|YES) ;;
    *) return 0 ;;
  esac

  local result_json
  result_json="$(node -e "console.log(JSON.stringify(JSON.parse(process.argv[1]).result))" "$session")"
  run_parallel_export "$query" "$result_json" "$take"
}

do_retry_missing() {
  echo
  echo "Export folders under downloads/:"
  local dirs=()
  while IFS= read -r d; do
    dirs+=("$d")
  done < <(find "$ROOT/downloads" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r || true)

  if ((${#dirs[@]} == 0)); then
    die "No folders in downloads/"
  fi

  local i=1
  for d in "${dirs[@]}"; do
    echo "  $i) $(basename "$d")"
    ((i++)) || true
  done
  echo
  read -r -p "Pick folder number: " pick || true
  [[ "$pick" =~ ^[0-9]+$ ]] && (( pick >= 1 && pick <= ${#dirs[@]} )) || die "Invalid choice."
  retry_missing_export "${dirs[$((pick - 1))]}"
}

do_consolidate() {
  echo
  echo "Export folders under downloads/:"
  local dirs=()
  while IFS= read -r d; do
    dirs+=("$d")
  done < <(find "$ROOT/downloads" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r || true)

  if ((${#dirs[@]} == 0)); then
    die "No folders in downloads/"
  fi

  local i=1
  for d in "${dirs[@]}"; do
    echo "  $i) $(basename "$d")"
    ((i++)) || true
  done
  echo
  read -r -p "Pick folder number: " pick || true
  [[ "$pick" =~ ^[0-9]+$ ]] && (( pick >= 1 && pick <= ${#dirs[@]} )) || die "Invalid choice."
  local dir="${dirs[$((pick - 1))]}"
  post_process_export_dir "$dir"
}

retry_missing_export() {
  local dir="$1"
  npm run screener:retry-export -- "$dir"
}

post_process_export_dir() {
  local dir="$1"
  echo
  echo "Post-processing $(basename "$dir") …"
  echo "  → features.compact.json + per-company *.features.json (extras removed)"
  echo
  npm run screener:post-process -- "$dir"
}

main_menu() {
  while true; do
    echo
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         Screener CLI                                     ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    echo "║  1) Run query → count → pick → list                      ║"
    echo "║  2) Same as 1, then export (+ 2 parallel workers)        ║"
    echo "║  3) Export last list (2 parallel workers)                ║"
    echo "║  4) Finalize folder (partial export → compact + screening) ║"
    echo "║  5) Retry missing exports in a folder                    ║"
    echo "║  0) Quit                                                 ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    read -r -p "Choice> " choice || true

    case "${choice:-}" in
      1) do_query_flow 0 ;;
      2) do_query_flow 1 ;;
      3) do_export_from_session ;;
      4) do_consolidate ;;
      5) do_retry_missing ;;
      0|q|Q) echo "Bye."; exit 0 ;;
      *) echo "Pick 0–5." ;;
    esac
  done
}

main_menu
