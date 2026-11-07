# Sourced by deploy.sh and ci_loop.sh: loads tests/integration/local.env and applies defaults.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_ENV="${LOCAL_ENV:-$ROOT/tests/integration/local.env}"
if [[ -f "$LOCAL_ENV" ]]; then set -a; . "$LOCAL_ENV"; set +a; fi
: "${TESTENV_SSH:?set TESTENV_SSH (ssh host that runs Docker) in $LOCAL_ENV, see local.env.example}"
: "${TESTENV_GERRIT_HOST:?set TESTENV_GERRIT_HOST in $LOCAL_ENV}"
: "${TESTENV_JENKINS_ADMIN_PASSWORD:?set TESTENV_JENKINS_ADMIN_PASSWORD in $LOCAL_ENV}"
TESTENV_GERRIT_HTTP_PORT="${TESTENV_GERRIT_HTTP_PORT:-8080}"
TESTENV_GERRIT_SSH_PORT="${TESTENV_GERRIT_SSH_PORT:-29418}"
TESTENV_JENKINS_PORT="${TESTENV_JENKINS_PORT:-8090}"
TESTENV_GERRIT_DOCKER_NETWORK="${TESTENV_GERRIT_DOCKER_NETWORK:-gerrit_default}"
TESTENV_GERRIT_DOCKER_HOST="${TESTENV_GERRIT_DOCKER_HOST:-gerrit}"
TESTENV_DEPLOY_DIR="${TESTENV_DEPLOY_DIR:-docker/jenkins}"
