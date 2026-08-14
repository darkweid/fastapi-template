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
# Consumed by infra/docker-compose.yml for app, worker, scheduler and the builder.
APP_IMAGE="${APP_IMAGE:-template-app-image:latest}"
export APP_IMAGE

COMPOSE=(docker compose --env-file .env -f infra/docker-compose.yml)

test -f .env || {
  echo "[deploy] .env is missing on the box - copy .env.example and fill it in"
  exit 1
}
python3 scripts/check_env.py

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

echo "[deploy] pruning superseded images"
docker image prune -f

echo "[deploy] done"
"${COMPOSE[@]}" ps
