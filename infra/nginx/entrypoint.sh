#!/bin/sh
# Entrypoint of the nginx service (infra/docker-compose.yml), run before the
# image's own. It writes /etc/nginx/app_upstream.inc, the servers of the app
# upstream in main.conf, before nginx reads it. Not named *.conf, so nginx does
# not load it from conf.d.
#
# infra/deploy/deploy.sh pins the upstream to single app containers while it
# rolls the app, and records each pin in app_upstream.applied once nginx has
# reloaded it. A restart keeps the container's files, so a restart in the
# middle of a roll comes back on the containers nginx was routing to, not on
# the `app` service name, which Docker resolves to every replica - one still
# booting, or one already retired, included. A new container starts on the
# service name.
set -eu

upstream=/etc/nginx/app_upstream.inc
applied=/etc/nginx/app_upstream.applied

if [ -f "$applied" ]; then
  cp "$applied" "$upstream"
else
  printf 'server app:%s resolve;\n' "$APP_BACKEND_PORT" >"$upstream"
fi

exec /docker-entrypoint.sh "$@"
