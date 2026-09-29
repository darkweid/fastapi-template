#!/usr/bin/env bash
# Deploy the stack on the target box. Run from anywhere inside the checkout.
#
# Modes:
#   BUILD=1 (default) - build the application image on the box. Bootstrap path:
#           works before any registry exists, and is what `make deploy-prod` uses.
#   BUILD=0 - pull APP_IMAGE, the image CI already built and pushed to GHCR.
#           The CD path: the box never compiles, so a broken build fails in CI
#           instead of halfway through a production deploy.
#
# Order matters: data services first, then migrations, then the application.
# A failed migration aborts the deploy with the previous app still serving. The
# app rolls without downtime: a new container starts beside the serving one and
# takes over only once healthy (roll_app below); one that never turns healthy is
# removed and the previous one keeps serving.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

BUILD="${BUILD:-1}"
APP_IMAGE_WAS_GIVEN="${APP_IMAGE:+yes}"
# Consumed by infra/docker-compose.yml for app, worker, scheduler and the builder.
APP_IMAGE="${APP_IMAGE:-template-app-image:latest}"
export APP_IMAGE

if [ "$BUILD" = "0" ] && [ -z "$APP_IMAGE_WAS_GIVEN" ]; then
  echo "[deploy] BUILD=0 requires APP_IMAGE - the local fallback tag exists in no registry"
  exit 1
fi

COMPOSE=(docker compose --env-file .env -f infra/docker-compose.yml)

# A new app container gets this long to turn healthy before it is removed. The
# app healthcheck in infra/docker-compose.yml reports one that never answers
# unhealthy sooner (start_period plus retries x interval, about 80s).
APP_HEALTHY_TIMEOUT_SECONDS=120
# How long the nginx workers retired by a reload get to finish the requests they
# still carry to the old app. Only a long-lived connection (a WebSocket) holds
# one longer; it is cut when the old app stops.
NGINX_DRAIN_TIMEOUT_SECONDS=60

nginx_is_running() {
  [ -n "$("${COMPOSE[@]}" ps -q nginx)" ]
}

# nginx routes to whatever /etc/nginx/app_upstream.inc names (the upstream in
# infra/nginx/main.conf). It is written inside the nginx container, not in the
# checkout: the file changes on every deploy, and a restarted nginx renders it
# again from app_upstream.inc.template, which names the `app` service. Naming
# single containers instead keeps nginx off a new one until it is healthy and
# off an old one before it stops, which Docker's service name cannot do - it
# resolves to every container of the service, started or stopping.
# A reload finishes the requests in flight; a restart would drop them.
route_app_to() {
  nginx_is_running || return 0
  # shellcheck disable=SC2016  # expanded by the shell inside the nginx container
  "${COMPOSE[@]}" exec -T nginx sh -c '
    for host in "$@"; do
      printf "server %s:%s resolve;\n" "$host" "$APP_BACKEND_PORT"
    done > /etc/nginx/app_upstream.inc.next
    mv /etc/nginx/app_upstream.inc.next /etc/nginx/app_upstream.inc
  ' sh "$@" || return 1
  "${COMPOSE[@]}" exec -T nginx nginx -s reload || return 1
}

# The workers a reload retires keep the requests they already accepted, a body
# still uploading among them, and forward those to the upstream they were
# started with. The old app has to stay up until they are gone.
wait_for_retired_nginx_workers() {
  nginx_is_running || return 0
  local deadline=$((SECONDS + NGINX_DRAIN_TIMEOUT_SECONDS))
  # `nginx -s reload` returns once the master is signalled, before it retires
  # anything.
  sleep 1
  while "${COMPOSE[@]}" exec -T nginx sh -c \
    'grep -qs "^nginx: worker process is shutting down" /proc/[0-9]*/cmdline'; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "[deploy] nginx workers retired ${NGINX_DRAIN_TIMEOUT_SECONDS}s ago still hold connections; stopping the old app anyway"
      return 0
    fi
    sleep 1
  done
}

# Healthy by the container's own healthcheck, not the service's: the old
# container is healthy too. A restart counts as a failure - the restart policy
# would otherwise hide a crash loop behind a fresh `starting`.
wait_until_healthy() {
  local container="$1" name="$2"
  local deadline=$((SECONDS + APP_HEALTHY_TIMEOUT_SECONDS))
  local state
  while true; do
    state="$(docker inspect -f '{{.State.Status}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container")"
    case "$state" in
      "running 0 healthy") return 0 ;;
      "running 0 starting") ;;
      *)
        echo "[deploy] ${name} is ${state:-gone}"
        return 1
        ;;
    esac
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "[deploy] ${name} did not turn healthy within ${APP_HEALTHY_TIMEOUT_SECONDS}s"
      return 1
    fi
    sleep 2
  done
}

container_names() {
  local id
  for id in "$@"; do
    docker inspect -f '{{.Name}}' "$id" | sed 's|^/||'
  done
}

# The app containers that are running and passing their own healthcheck - the
# only ones nginx may be pinned to. A deploy interrupted mid-roll can leave a
# second container behind that is still starting, unhealthy or crash-looping.
healthy_app_ids() {
  local id
  for id in $("${COMPOSE[@]}" ps -a -q app); do
    if [ "$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")" = "running healthy" ]; then
      echo "$id"
    fi
  done
}

# Rolls the app to the image given, with no request refused: pin nginx to the
# healthy serving containers, start one more beside them, wait for it to turn
# healthy, move nginx onto it, let the retired nginx workers finish, then stop
# the old containers - gunicorn drains what they still serve within its graceful
# timeout, which the compose stop_grace_period covers - and point nginx back at
# the service name. A new container that never turns healthy is removed without
# having served anything.
#
# Callers test it in an `if`, which switches errexit off for everything inside,
# so every step that must not be skipped checks its own status.
roll_app() {
  local image="$1"
  local all_ids old_ids new_id new_name id count=1

  # A stopped container of the service would be started again by the scale-up
  # below and counted as one of its replicas. `ps -a` from here on: a container
  # the restart policy is bringing back up is not listed as running.
  "${COMPOSE[@]}" rm -f app >/dev/null || return 1

  all_ids="$("${COMPOSE[@]}" ps -a -q app)" || return 1
  old_ids="$(healthy_app_ids)" || return 1
  if [ -n "$old_ids" ]; then
    for id in $all_ids; do
      if ! printf '%s\n' "$old_ids" | grep -qxF "$id"; then
        echo "[deploy] removing $(container_names "$id"), left behind unhealthy by an earlier deploy"
        docker rm -f "$id" >/dev/null || return 1
      fi
    done
    # shellcheck disable=SC2046,SC2086  # container ids and names, one word each
    route_app_to $(container_names $old_ids) || return 1
  else
    # Nothing is verified to serve, so there is nothing to pin nginx to; the
    # containers still count as old ones and stop once the new one is healthy.
    old_ids="$all_ids"
    if [ -n "$old_ids" ]; then
      echo "[deploy] no app container is healthy; the new one takes over once it is"
    fi
  fi
  for id in $old_ids; do
    count=$((count + 1))
  done

  # --no-recreate leaves the serving containers alone and adds one more from the
  # current configuration; --no-deps keeps app-builder out of the CD path.
  if ! APP_IMAGE="$image" "${COMPOSE[@]}" up -d --no-deps --no-recreate --scale "app=${count}" app; then
    echo "[deploy] starting the new app container failed"
    discard_new_app "$old_ids"
    return 1
  fi

  new_id=""
  for id in $("${COMPOSE[@]}" ps -a -q app); do
    printf '%s\n' "$old_ids" | grep -qxF "$id" || new_id="$id"
  done
  if [ -z "$new_id" ]; then
    echo "[deploy] no new app container was started"
    route_app_to app || true
    return 1
  fi
  new_name="$(container_names "$new_id")"
  echo "[deploy] waiting for ${new_name} to turn healthy"

  if ! wait_until_healthy "$new_id" "$new_name"; then
    echo "[deploy] last log lines of ${new_name}:"
    docker logs --tail 50 "$new_id" 2>&1 || true
    discard_new_app "$old_ids"
    return 1
  fi

  if ! route_app_to "$new_name"; then
    echo "[deploy] nginx could not be moved onto ${new_name}"
    discard_new_app "$old_ids"
    return 1
  fi
  if [ -n "$old_ids" ]; then
    wait_for_retired_nginx_workers
    echo "[deploy] stopping the previous app container"
    # shellcheck disable=SC2086  # container ids, one word each
    docker stop $old_ids >/dev/null || return 1
    # shellcheck disable=SC2086
    docker rm $old_ids >/dev/null || return 1
  fi
  route_app_to app || return 1
}

# Removes every app container the roll started, the ones given excepted, and
# pins nginx back to those. Best effort: it runs on a path that already failed.
discard_new_app() {
  local keep="$1" id
  for id in $("${COMPOSE[@]}" ps -a -q app); do
    printf '%s\n' "$keep" | grep -qxF "$id" || docker rm -f "$id" >/dev/null || true
  done
  route_app_to app || true
}

test -f .env || {
  echo "[deploy] .env is missing on the box - copy .env.example and fill it in"
  exit 1
}
python3 scripts/ops/check_env.py

# The nginx configuration this checkout ships is tested in a fresh container
# with the new mounts before anything is rolled: a broken vhost or a missing
# certificate aborts the deploy with the previous stack still serving. The
# image's own entrypoint runs first, so app_upstream.inc is rendered from its
# template the way a real start renders it; the upstream is resolved at run
# time, so the test needs no app container and runs on a first deploy too.
echo "[deploy] testing the nginx configuration"
"${COMPOSE[@]}" run --rm --no-deps nginx nginx -t

# The image the serving app runs, recorded before anything is rolled, so the
# whole stack can go back to it when the worker or the scheduler never turns
# healthy. The image id, not the tag: BUILD=1 rebuilds the same tag, which would
# then name the new image. Empty on the first deploy, when there is nothing to
# go back to.
PREVIOUS_IMAGE=""
RUNNING_APP="$(healthy_app_ids | head -n 1)"
if [ -n "$RUNNING_APP" ]; then
  PREVIOUS_IMAGE="$(docker inspect -f '{{.Image}}' "$RUNNING_APP")"
fi

# The database image stays box-local in both modes - CD ships application code,
# never the database. It is rebuilt every deploy because `up` reuses an existing
# tag, so a change to infra/postgres/ would otherwise never reach the server.
echo "[deploy] building the postgres image"
"${COMPOSE[@]}" build postgres

if [ "$BUILD" = "1" ]; then
  echo "[deploy] building ${APP_IMAGE} on the box"
  "${COMPOSE[@]}" build app-builder
else
  echo "[deploy] pulling ${APP_IMAGE}"
  "${COMPOSE[@]}" pull app
fi

echo "[deploy] starting data services"
"${COMPOSE[@]}" up -d --wait postgres redis

echo "[deploy] applying migrations before any new code serves traffic"
"${COMPOSE[@]}" run --rm --no-deps app alembic upgrade head

echo "[deploy] rolling the app"
if ! roll_app "$APP_IMAGE"; then
  echo "[deploy] rolling the app failed; the previous one keeps serving and the deploy failed"
  exit 1
fi

# The worker and the scheduler are recreated in place: nothing waits on them
# synchronously, tasks queue in Redis while they restart, and taskiq has no
# duplicate-fire protection, so two schedulers side by side would fire every
# cron twice. When they never turn healthy the whole stack goes back to the
# previous image, the app through the same zero-downtime roll, so no two
# versions of the code keep running together. The migrations stay applied:
# the previous code runs against the new schema.
echo "[deploy] rolling the worker and the scheduler"
if ! "${COMPOSE[@]}" up -d --no-deps --wait worker scheduler; then
  if [ -z "$PREVIOUS_IMAGE" ]; then
    echo "[deploy] the worker or the scheduler did not become healthy and there is no previous image to roll back to"
    exit 1
  fi
  echo "[deploy] the worker or the scheduler did not become healthy; rolling back to ${PREVIOUS_IMAGE}"
  APP_IMAGE="$PREVIOUS_IMAGE" "${COMPOSE[@]}" up -d --no-deps --wait worker scheduler
  roll_app "$PREVIOUS_IMAGE"
  echo "[deploy] rolled back; the deploy failed"
  exit 1
fi

# Starts nginx on the first deploy. Later it is a no-op unless the nginx
# service itself changed in infra/docker-compose.yml (its image, its mounts),
# which recreates it and drops the connections it holds.
"${COMPOSE[@]}" up -d --no-deps nginx

# Every BUILD=0 deploy leaves a tagged sha- image behind, and plain
# `image prune` only touches dangling ones, so the disk grows until a pull
# fails. A week keeps enough recent tags to roll back to.
echo "[deploy] pruning images and build cache unused for a week"
docker image prune -af --filter "until=168h"
docker builder prune -f --filter "until=168h"

echo "[deploy] done"
"${COMPOSE[@]}" ps
