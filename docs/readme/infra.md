# Infrastructure and Operations

## Services and Ports

Only **Nginx** is published on a public address. The services talk to each other
over the `app-network` bridge by service name; Postgres and Redis are also
published on the host's loopback, so a server's data stores are one SSH tunnel
away.

| Service | Port | Host exposure |
|---|---|---|
| Nginx | 80 / 443 | **Public** (`0.0.0.0`) — proxies to `app:8001`; dev publishes `8000` instead |
| App | 8001 | Internal only (dev: `127.0.0.1:8001` for direct access) |
| Postgres | 5432 | `127.0.0.1:${POSTGRES_HOST_PORT:-5432}`, every environment |
| Redis | 6379 | `127.0.0.1:${REDIS_HOST_PORT:-6379}`, every environment |

`POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` move only the host side of those binds,
for a server where the default port is taken; `POSTGRES_PORT` / `REDIS_PORT` stay
what the app dials inside the network. Loopback binds cannot be reached from the
network, so the Docker iptables/UFW bypass does not apply — see
`docs/readme/security.md` → *Host Port Exposure (Docker & UFW)*.

Configs live in `infra/` (compose, nginx, dockerfiles, redis/postgres, requirements).

## Containers
- **Postgres:** `infra/postgres/Dockerfile`, stores data in volume. The image carries `infra/postgres/postgresql.conf` and the server reads it at every start (`-c config_file=`), so a changed setting reaches an existing database on the next deploy, which rebuilds the image with `--pull` and recreates the container.
- **App:** Uvicorn/Gunicorn serving FastAPI under a non-root runtime user.
- **Worker:** Runs taskiq tasks consumed from Redis Streams, with `IdempotencyReceiver` for worker-side dedup (a running task holds a claim it renews every 20 seconds, so a second delivery of the same task id is skipped however long the first one runs) and `--max-async-tasks 20` concurrency (mirrored by the `tasks_engine` pool in `src/core/database/engine.py`).
- **Scheduler:** Fires periodic tasks (`schedule=[{"cron": "..."}]` on the task decorator) into the stream, including the outbox sweeper (every minute, taking only rows older than `SWEEPER_GRACE` so it does not race a row's own after-commit publish) and purge (daily) tasks, and fires delayed retries written by `SmartRetryMiddleware`; exactly one instance runs. Its healthcheck asserts a heartbeat the scheduler loop writes to Redis at every schedule update, once a minute, under a key named after the container (`taskiq_worker/heartbeat.py`), so a wedged loop turns the container unhealthy even while Redis answers.
- **Nginx:** Reverse proxy to app with template security headers.
- **Redis:** Cache backend with password; also the taskiq broker (Streams), the retry schedule source for delayed retries, and storage for `IdempotencyReceiver` dedup markers — no task result backend.

## Redis, not Valkey
The template runs Redis 8 from the official image, unmodified. Redis 8 is
available under AGPLv3 next to RSALv2 and SSPLv1. The AGPL obligations attach
to modifying the server and offering it over a network, or to redistributing
it; running the stock image as a backing service does neither.

Valkey (the BSD-3 Linux Foundation fork of Redis 7.2) is protocol-compatible
with everything the application does: `redis-py`, `taskiq-redis`, the Lua
scripts (`redis.call` works unchanged), `INFO memory` behind `/health/`. Valkey
9.1 is still not a drop-in swap here:

- It refuses to start on `aof-load-corrupt-tail-max-size` in
  `infra/redis.conf` and has no equivalent setting. Without it a host crash
  that leaves a malformed command at the AOF tail keeps the instance down, and
  the app and worker with it, until someone repairs the file by hand.
- That repair is harder than the server's error message suggests:
  `valkey-check-aof --fix` on the manifest misreads the Valkey RDB base file as
  broken and repairs nothing (9.1.2 and 9.2.0-rc1). It works only when run on
  the newest `*.incr.aof` file directly.
- It cannot load an RDB written by Redis 8 (`Can't handle RDB format version`),
  so the choice is made when a project starts: a volume written by one does not
  load in the other.

Switching is worth revisiting once Valkey gains a corrupt-tail setting. It then
means `valkey/valkey` as the image, `valkey-server` in the compose `command:`
(the image's entrypoint drops root only for that name; started as
`redis-server` the server runs as root) and the directive above removed. The
image's `redis-cli` symlink keeps the healthcheck and `make redis-cli` working,
`REDISCLI_AUTH` stays (valkey-cli reads it, and
`tests/unit/test_docker_compose_config.py` pins it), and the application code,
the `REDIS_*` settings and the `redis://` URLs stay as they are.

## Cache Operations
The cache layer (`src/core/cache/`) has no dedicated Redis connection — it runs on
`app.state.redis_client`, the application client created in
`src/main/lifespan.py` and shared with auth token storage and the health probe.
There is no separate service or port to provision.

The rate limiter runs on that same client: `lifespan` hands it to
`FastAPILimiter.init`. Only taskiq keeps a connection of its own (the broker plus
the retry schedule source), so an API container holds two Redis connection pools
and a worker or scheduler container holds the broker's — size `maxclients` from
that count, not from one pool per process.

- Keep `maxmemory-policy noeviction` (`infra/redis.conf`). One instance holds
  sessions and refresh-token state, OTP and one-time challenges, rate-limit
  windows and the task queue next to the cache, and none of those may disappear
  to make room: an evicted session logs a user out, an evicted stream entry is a
  task that never runs, and an evicted cache version counter falls back to `0`
  and serves a value an `invalidate()` or `invalidate_tags()` call already
  retired. Under `noeviction` a full Redis refuses writes instead, which fails
  loudly. Watch for it before it happens: `/health/` reports
  `redis_memory_used_ratio` and turns `degraded` at 90% of `maxmemory`. When the
  cache outgrows its share, move it to a separate instance with its own eviction
  policy rather than turning eviction on here.
- The cache's Lua scripts (`src/core/cache/scripts/*.lua`) address multiple keys
  per invocation without hash tags, so as written they run correctly against a
  single Redis instance but not against a sharded Redis Cluster — a cluster
  deployment needs hash-tagged keys (or a separate non-clustered instance for the
  cache) before this layer would work unmodified. Tags widen this: a read of a
  tagged entry resolves the namespace counter, every tag counter, and the value
  key in one script, so all of them must hash to the same slot.

## Prerequisites
- Python 3.13 (for local scripts/hooks)
- Docker
- Docker Compose

## Quick Start
```bash
cp .env.example .env   # main env
make run-dev          # dev images + autoreload, exposes 8000 via nginx
# or:
make run              # prod-like build
make migrate          # required on first run - the stack never migrates itself
```

Open:
- App via Nginx: http://localhost:8000
- Docs: http://localhost:8000/docs
- Direct app (bypass Nginx): http://localhost:8001/docs — **dev only** (`make
  run-dev`); the base/prod stack does not publish the app port.

## Common Commands
```bash
make                  # list every target with its description
make run-dev          # build+up with override (reload)
make run              # build+up prod-like
make logs             # tail all services
make logs s=app       # tail one service
make migrate          # alembic upgrade head
make migration m="add users table"  # create alembic revision
make test             # pytest
make test-cov         # pytest + coverage
make lint             # pre-commit hooks
make down             # stop stack
make clean            # remove stack + volumes/images/orphans
```

## Database & Redis Operations
- `make backup` — dumps the database to `backups/<UTC timestamp>.dump` with `pg_dump -Fc` (custom format, restorable with `pg_restore`). `backups/` is git-ignored; copy dumps off the server before it is rebuilt or recycled.
- `make restore f=backups/<file>.dump` — restores from a dump with `pg_restore --clean --if-exists`, which drops conflicting existing objects before recreating them. Run it against a stopped or otherwise quiesced app to avoid restoring under live writes.
- `make psql` — opens an interactive `psql` shell inside the Postgres container, authenticated with the compose-provided `POSTGRES_USER`/`POSTGRES_DB`.
- `make redis-cli` — opens an interactive `redis-cli` shell inside the Redis container, authenticated with `REDIS_PASSWORD`.
- `make create-admin` — bootstraps the first admin account, or promotes an existing account to admin, via `scripts/app/create_admin.py`; see the *Bootstrap the first admin* section in [README.md](../../README.md) for the environment variables it reads.

## Dependencies (pip-tools)
- Source files: `infra/requirements/*.in` list direct dependencies (no pins by default).
- Lockfiles: `infra/requirements/*.txt` are generated by `pip-compile`.
- Update lockfiles: `make req-compile`
- Sync environment: `make req-sync-dev` / `make req-sync-prod`
- Add pins/ranges in `.in` only when needed (e.g. `fastapi>=0.110,<1`), then recompile.
- `make req-compile` runs inside `python:3.13-slim-bookworm` with `linux/amd64` by default to match the production resolver context more closely.
- Override the platform when production differs, for example `make req-compile REQ_COMPILE_PLATFORM=linux/arm64`.

## Troubleshooting
- Ensure Docker/Compose are installed.
- `.env` must be filled (ports, DB/Redis credentials). `.env.test` used for local test runs `make test` / `make test-cov`.
- The integration suite (`make test-integration`) brings up its own throwaway PostgreSQL from `infra/docker-compose.test.yml` and overrides the connection settings itself — it needs Docker, but not a running dev stack.
- Use `make logs` or service-specific logs to inspect errors.
- If migrations fail, check Postgres health first.

## Deployment Notes
- `infra/deploy/deploy.sh` is the single deploy path, run on the server: it validates `.env`, tests the nginx configuration it ships in a throwaway container (`nginx -t` with the new mounts), brings up Postgres and Redis, applies migrations, then rolls the app, then `worker` and `scheduler`. Migrations run before any new code serves traffic, and a failed one aborts the deploy with the previous containers still up; so does a failed nginx test.
- The app rolls without a refused request. nginx proxies to the upstream in `infra/nginx/main.conf`, whose servers come from `/etc/nginx/upstream/app_upstream.inc`, on the `nginx-upstream` volume: written at nginx start by `infra/nginx/entrypoint.sh` (the `app` service name), and rewritten by the deploy, followed by `nginx -s reload`; once the reload is sent the deploy records the pin in `app_upstream.applied`, which the next deploy reads after an interrupted roll and the entrypoint restores when nginx restarts or is recreated, so nginx coming back mid-roll does not fall back to the service name; a recorded pin naming containers that no longer resolve (the stack went down and up) is dropped for the service name. The deploy pins nginx to the serving container by name (a healthy one; after an interrupted roll, the healthy one nginx is already pinned to, and any other container is stopped gracefully once the nginx workers still routing to it have finished; a deploy that finds nginx not running fails instead of rolling unrouted), starts a second one beside it (`up --no-recreate --scale app=2`), waits for that container's own healthcheck (by id, never the service's: the old one is healthy too), moves nginx onto it, waits until the nginx workers the reload retired have finished what they carry, then stops the old container - gunicorn stops accepting on SIGTERM and finishes what it serves within `--graceful-timeout` (30s), inside the compose `stop_grace_period` (35s) - and points nginx back at the service name. Docker's service name alone would not do: it resolves to every container of the service, one that is still booting or never turns healthy included. `resolve` on the upstream servers re-reads Docker's DNS every 5s, so an app container that restarts on a new address is followed without a reload.
- A new app container that is not healthy within two minutes (or restarts, or turns `unhealthy`) is removed without having served a request and the previous one keeps serving; the deploy fails. `worker` and `scheduler` are recreated in place after the app - the scheduler must stay one instance, and tasks wait in Redis meanwhile. If they do not turn healthy, both go back to the image the app ran before (recorded by id, since a build on the server reuses the tag) and the app rolls back to it the same zero-downtime way, so no two versions keep running together; the migrations stay applied, so the previous code runs against the new schema. A first deploy has nothing to roll back to and just fails.
- nginx is brought up to date before the app rolls, since the roll relies on it reading `app_upstream.inc`: it is recreated only when its service in `infra/docker-compose.yml` changes (image, mounts, environment), and that drops the connections it holds for a moment. A change to a file under `infra/nginx/` needs no recreation: the directory is mounted as `/etc/nginx/conf.d`, so the reload during the roll serves what the checkout holds. Never mount those files one by one - `git checkout` replaces a changed file with a new inode, a single-file bind mount keeps the old one, and the reload would serve the previous configuration while the `nginx -t` pre-check, in a fresh container, passed the new one.
- CD (prod) (`.github/workflows/prod_deploy.yml`, calling `_deploy.yml`) normally triggers automatically once CI (prod) succeeds on `main`. It also accepts a manual `workflow_dispatch` run (Actions tab → *CD (prod)* → *Run workflow*, or `gh workflow run prod_deploy.yml`) — the same `PROD_DEPLOY_ENABLED` gate and `deploy-production` concurrency group apply, so a manual run still queues behind an in-flight automatic one. Its optional `image_tag` input deploys a specific already-built image tag; left blank, it computes `sha-<12>` from the dispatched commit (`main` HEAD unless another ref is picked in the UI). `stage_deploy.yml` is the same reusable workflow bound to the `stage` branch and the `staging` environment; its ref selector also defaults to `main`, so a blank `image_tag` on a stage dispatch deploys `main` HEAD unless `stage` is picked.
- Two modes. `BUILD=1` (the default, `make deploy-prod`) builds the image on the server — the bootstrap path, before any registry exists. `BUILD=0` with `APP_IMAGE=ghcr.io/<owner>/<repo>:sha-<12>` (`make deploy-image APP_IMAGE=…`) pulls the image CI already built; this is what CD uses, so the production server never compiles.
- `APP_IMAGE` is the only knob: unset, every service falls back to the locally built `template-app-image:latest`, so `make run` and `make run-dev` behave exactly as before.
- The server pulls the private package with the deploy job's own workflow token (`packages: read`), logged in for the length of the deploy and logged out after it; no registry credential is stored on the server or in the environment. A manual `make deploy-image` on the server needs a `docker login ghcr.io` of its own first. Postgres is always built on the server — CD ships application code, never the database image.
- `infra/docker-compose.yml` is production-oriented and does not mount host source code into `app`, `worker`, or `scheduler`.
- It publishes Nginx publicly and Postgres/Redis on `127.0.0.1` only; the app stays internal to `app-network`. Any further host port follows the same rule: bind it to `127.0.0.1` (or restrict it via a `DOCKER-USER` firewall rule) — never the short `host:container` syntax, which binds `0.0.0.0` and bypasses UFW. See `docs/readme/security.md`.
- Source bind mounts remain only in `infra/docker-compose.override.yml` for local development.
- `infra/nginx/security_headers.inc` sets baseline security headers at the reverse-proxy layer, included by every server and every JSON error page, while the FastAPI app keeps the same headers as a fallback for direct app access and tests; `proxy.inc` hides the app's copies so a proxied response carries each once. The proxy body itself lives in `infra/nginx/proxy.inc`, shared with the TLS server so the two cannot drift apart. Dev serves the same `app.conf`, only on a different published port.
- The app server answers only for the names in its `server_name` (`localhost 127.0.0.1` out of the box). Every other Host lands on a `default_server` that closes the connection without an answer (444), and under TLS an unknown SNI name is refused during the handshake (`ssl_reject_handshake`). **Add the API's hostname to `server_name` before the first deploy**, and the host's IP address if anything calls it by IP - until then every request is dropped.
- The app does not redirect a path to its slash-twin (`redirect_slashes=False` in `src/main/web.py`): Starlette built that redirect from the Host header and lost the proxy's port, so a forged Host made it an open redirect. A route is served exactly as declared; `/live` answers 404 where `/live/` exists.
- TLS terminates at Nginx: replace the content of `infra/nginx/app.conf` with `infra/nginx/tls.conf.example`, put the certificate under `infra/nginx/certs/` (git-ignored) and set the real hostname. It redirects plain http to https and leaves the ACME challenge path reachable.
- `Strict-Transport-Security` comes from the application, not from Nginx — one header, one source. It is only appropriate once clients actually reach the site over HTTPS end to end.
- `infra/ansible` provisions the host: users, key-only SSH, Docker, UFW plus a fail-closed `DOCKER-USER` chain installed as a systemd unit, the application checkout. See `infra/ansible/README.md`.
- `client_max_body_size 20m` is the current default, kept in sync with `S3_MAX_UPLOAD_SIZE_BYTES`. Change both together if the project needs larger uploads. Nginx answers some errors itself - 400 (a malformed request line such as `/%00`, oversized headers), 405 (a method outside GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, TRACE included), 413, 414, 421, 502 and 504 - so `error_pages.inc` rewrites those into the same `{"code", "message"}` JSON the app returns. Every server includes it, the default ones too: a request line nginx cannot parse is refused before the Host is read, by the default server.
- Probes: `/live/` has no dependencies and is what the `app` container healthcheck polls. Plain Compose never restarts a container on a failed healthcheck — what an `unhealthy` app used to cost was `depends_on: service_healthy`, which kept nginx from starting, plus a misleading `docker ps`; under an orchestrator or an autoheal sidecar it costs the container. `/ready/` returns 503 while Postgres is unreachable or the connection pool cannot hand out a connection within two seconds — the gate to put in front of a load balancer. `/health/` is the detailed report for monitoring and always answers 200, with `"status": "degraded"` and a per-dependency breakdown, precisely so the body survives the outage it describes. `/live/` is public; nginx serves `/ready/` and `/health/` only to loopback and private networks (`infra/nginx/proxy.inc`) and answers everyone else the JSON 404 of a missing route, since each call costs a Postgres query and a Redis ping and logs a warning per call during an outage. The `app-network` gateway is denied even though it is private: Docker forwards published-port connections it proxies from there, every IPv6 client included. None of them reports to Sentry: they run on a timer, and a single outage would file one event per poll.
- Liveness independence covers a running process, not a restarting one: `on_redis_startup` (`src/core/redis/lifecycle.py`) pings Redis and raises, so a container restarted during a Redis outage never reaches `/live/` at all.
- `worker` has no HTTP surface, so its healthcheck pings the broker Redis from inside the container. It catches an unreachable broker or a wrong password — the container-level failure that otherwise looks fine. It does **not** prove the consumer is still pulling from the stream: a deadlocked worker with a healthy Redis still reports `healthy`. Catching that needs a heartbeat key the worker itself refreshes. The `scheduler` already has one: its healthcheck (`python -m taskiq_worker.heartbeat`) asserts the heartbeat its schedule loop writes once a minute.
