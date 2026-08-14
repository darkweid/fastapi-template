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
# A failed migration aborts the deploy with the previous app still serving.
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

test -f .env || {
  echo "[deploy] .env is missing on the box - copy .env.example and fill it in"
  exit 1
}
python3 scripts/check_env.py

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

echo "[deploy] rolling the application containers"
# --no-deps keeps app-builder out of the CD path: with BUILD=0 the image comes
# from the registry and there is nothing to build.
"${COMPOSE[@]}" up -d --no-deps --wait app worker scheduler
"${COMPOSE[@]}" up -d --no-deps nginx
# nginx resolves the app upstream once at startup, so a recreated app container
# leaves it serving 502 until it restarts.
"${COMPOSE[@]}" restart nginx

# Every BUILD=0 deploy leaves a tagged sha- image behind, and plain
# `image prune` only touches dangling ones, so the disk grows until a pull
# fails. A week keeps enough recent tags to roll back to.
echo "[deploy] pruning images and build cache unused for a week"
docker image prune -af --filter "until=168h"
docker builder prune -f --filter "until=168h"

echo "[deploy] done"
"${COMPOSE[@]}" ps
