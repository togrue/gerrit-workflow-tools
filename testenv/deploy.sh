#!/usr/bin/env bash
# Copy testenv/jenkins to the Docker host and (re)start Jenkins. Settings: tests/integration/local.env
set -euo pipefail
. "$(dirname "$0")/env.sh"
SRC="$ROOT/testenv/jenkins"
ssh "$TESTENV_SSH" "mkdir -p '$TESTENV_DEPLOY_DIR/secrets'"
scp -q -r "$SRC/Dockerfile" "$SRC/docker-compose.yaml" "$SRC/casc" "$SRC/init.groovy.d" "$TESTENV_SSH:$TESTENV_DEPLOY_DIR/"
ENVFILE="$(cat <<EOT
JENKINS_PORT=$TESTENV_JENKINS_PORT
JENKINS_PUBLIC_URL=http://$TESTENV_GERRIT_HOST:$TESTENV_JENKINS_PORT/
JENKINS_ADMIN_PASSWORD=$TESTENV_JENKINS_ADMIN_PASSWORD
GERRIT_NETWORK=$TESTENV_GERRIT_DOCKER_NETWORK
GERRIT_INTERNAL_HOST=$TESTENV_GERRIT_DOCKER_HOST
GERRIT_SSH_PORT=$TESTENV_GERRIT_SSH_PORT
GERRIT_FRONTEND_URL=http://$TESTENV_GERRIT_HOST:$TESTENV_GERRIT_HTTP_PORT/
EOT
)"
printf '%s\n' "$ENVFILE" | ssh "$TESTENV_SSH" "umask 077; cat > '$TESTENV_DEPLOY_DIR/.env'"
ssh "$TESTENV_SSH" "cd '$TESTENV_DEPLOY_DIR' && \
  [ -f secrets/jenkins_ssh ] || ssh-keygen -t ed25519 -N '' -C jenkins@ci -f secrets/jenkins_ssh -q; \
  docker compose up -d --build"
echo "Jenkins: http://$TESTENV_GERRIT_HOST:$TESTENV_JENKINS_PORT/  (user admin)"
echo "Gerrit user 'jenkins' still needs creating once, see testenv/README.md. Public key:"
ssh "$TESTENV_SSH" "cat '$TESTENV_DEPLOY_DIR/secrets/jenkins_ssh.pub'"
