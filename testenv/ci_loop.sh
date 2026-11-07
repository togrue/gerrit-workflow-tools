#!/usr/bin/env bash
# Feedback loop: push a harmless and a breaking change of ci-demo with `ger`, wait for Jenkins
# to vote Verified, print `ger log`. Needs the testenv/jenkins stack and the ci-demo project.
# Settings come from tests/integration/local.env (TESTENV_*); WAIT=<seconds> to wait for CI.
set -euo pipefail
. "$(dirname "$0")/env.sh"
HOST="$TESTENV_GERRIT_HOST"; PORT="$TESTENV_GERRIT_SSH_PORT"; HTTP_PORT="$TESTENV_GERRIT_HTTP_PORT"
WAIT="${WAIT:-60}"
GER=(uv run --project "$ROOT" ger)
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
git clone -q "ssh://$HOST:$PORT/ci-demo" "$W/ci-demo"; cd "$W/ci-demo"
git config user.email admin@example.com; git config user.name Admin
curl -s -o .git/hooks/commit-msg "http://$HOST:$HTTP_PORT/tools/hooks/commit-msg"
chmod +x .git/hooks/commit-msg
echo "loop $(date +%s)" >> NOTES.md; git add NOTES.md; git commit -q -m "ci-loop: harmless change"
sed -i 's/return a + b/return a - b/' demo/calc.py
git commit -qam "ci-loop: break add"
"${GER[@]}" push -y
sleep "$WAIT"
"${GER[@]}" log
