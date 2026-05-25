#!/usr/bin/env bash

set -u
set -o pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
OUTPUT_FILE="${1:-./ger-observe-snapshot.txt}"
PYTHON_BIN="${PYTHON_BIN:-python}"

export PYTHONPATH="$WORKSPACE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONIOENCODING="utf-8"
export NO_COLOR=1
export CLICOLOR=0
export TERM=dumb

ger() {
  "$PYTHON_BIN" -m gerrit_workflow_tools.cli_ger "$@"
}

print_command() {
  printf "+"
  printf " %q" "$@"
  printf "\n"
}

run_case() {
  local label="$1"
  shift

  printf "\n===== %s =====\n" "$label"
  print_command "$@"
  "$@"
  local rc=$?
  printf "[exit %d]\n" "$rc"
}

have_rev() {
  git rev-parse --verify --quiet "$1" >/dev/null 2>&1
}

head_change_id() {
  local out
  local last

  out="$(ger change-id HEAD 2>/dev/null)" || return 1
  last="$(printf "%s\n" "$out" | awk 'NF { line = $0 } END { print line }')"
  if [[ "$last" =~ ^I[0-9A-Fa-f]{40}$ ]]; then
    printf "%s\n" "$last"
    return 0
  fi
  return 1
}

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf "error: run this script inside the test git repository\n" >&2
  exit 1
fi

exec > >(tee "$OUTPUT_FILE") 2>&1

printf "Writing snapshot to %s\n" "$OUTPUT_FILE"

run_case "ger help" ger --help

run_case "ger bash-completion help" ger bash-completion --help
run_case "ger bash-completion" ger bash-completion

run_case "ger cache help" ger cache --help

run_case "ger change-id help" ger change-id --help
run_case "ger change-id default" ger change-id
run_case "ger change-id HEAD" ger change-id HEAD
run_case "ger change-id start-at-remote" ger change-id --start-at-remote
run_case "ger change-id check-duplicates" ger change-id --check-duplicates
run_case "ger sha help" ger sha --help

if have_rev "HEAD~1"; then
  run_case "ger change-id HEAD~1..HEAD" ger change-id HEAD~1..HEAD
fi

if HEAD_CHANGE_ID="$(head_change_id)"; then
  run_case "ger change-id passthrough" ger change-id "$HEAD_CHANGE_ID"

  run_case "ger sha default" ger sha "$HEAD_CHANGE_ID"
  run_case "ger sha short" ger sha --short "$HEAD_CHANGE_ID"
  run_case "ger sha subject" ger sha --subject "$HEAD_CHANGE_ID"
  run_case "ger sha json" ger sha --json "$HEAD_CHANGE_ID"
  run_case "ger sha all" ger sha --all "$HEAD_CHANGE_ID"

  if have_rev "HEAD~1"; then
    run_case "ger sha range" ger sha --range HEAD~1..HEAD "$HEAD_CHANGE_ID"
  fi

  run_case "ger show by change-id" ger show "$HEAD_CHANGE_ID"
  run_case "ger show json by change-id" ger show --json "$HEAD_CHANGE_ID"
else
  printf "\n===== derived HEAD Change-Id =====\n"
  printf "Skipping `ger sha` and Change-Id based `ger show` variants because `ger change-id HEAD` did not return a valid Change-Id.\n"
fi

run_case "ger log help" ger log --help
run_case "ger log default" ger log
run_case "ger log json" ger log --json
run_case "ger log show-change-id" ger log --show-change-id
run_case "ger log url" ger log --url
run_case "ger log verbose" ger log -v

run_case "ger show help" ger show --help
run_case "ger show default" ger show
run_case "ger show HEAD" ger show HEAD
run_case "ger show json" ger show --json
run_case "ger show full" ger show --full
run_case "ger show tail-lines" ger show --comment-tail-lines 3

run_case "ger fetch-api help" ger fetch-api --help
run_case "ger fetch-api detail" ger fetch-api accounts/self/detail
run_case "ger fetch-api compact" ger fetch-api --compact accounts/self/detail
