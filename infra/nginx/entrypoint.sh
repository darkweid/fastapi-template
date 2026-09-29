#!/bin/sh
# Entrypoint of the nginx service (infra/docker-compose.yml), run before the
# image's own. It writes /etc/nginx/upstream/app_upstream.inc, the servers of
# the app upstream in main.conf, before nginx reads it. Not named *.conf, so
# nginx does not load it from conf.d.
#
# infra/deploy/deploy.sh pins the upstream to single app containers while it
# rolls the app, and records each pin in app_upstream.applied once nginx has
# reloaded it. /etc/nginx/upstream is a named volume, so the record outlives a
# restart and a recreation of this container: nginx coming back in the middle
# of a roll routes where it did, not to the `app` service name, which Docker
# resolves to every replica - one still booting, or one already retired,
# included. A record naming a container that no longer resolves (the stack was
# taken down and brought up again) is stale, and the service name is used.
set -eu

state=/etc/nginx/upstream
upstream="$state/app_upstream.inc"
applied="$state/app_upstream.applied"

mkdir -p "$state"

pin_is_live() {
  [ -s "$applied" ] || return 1
  sed -n 's/^server \([^: ]*\):.*/\1/p' "$applied" | while read -r host; do
    getent hosts "$host" >/dev/null || exit 1
  done
}

if pin_is_live; then
  cp "$applied" "$upstream"
else
  printf 'server app:%s resolve;\n' "$APP_BACKEND_PORT" >"$upstream"
  cp "$upstream" "$applied"
fi

exec /docker-entrypoint.sh "$@"
