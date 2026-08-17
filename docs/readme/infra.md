# Infrastructure and Operations

## Services and Ports

Only **Nginx** is published to the host. All other services are internal-only —
they talk to each other over the `app-network` bridge by service name and are
never bound to a host interface in production.

| Service | Port | Host exposure |
|---|---|---|
| Nginx | 80 / 443 | **Public** (`0.0.0.0`) — proxies to `app:8001`; dev publishes `8000` instead |
| App | 8001 | Internal only (dev: `127.0.0.1:8001` for direct access) |
| Postgres | 5432 | Internal only (dev: `127.0.0.1:5432`) |
| Redis | 6379 | Internal only (dev: `127.0.0.1:6379`) |

Backing-service host ports live **only** in `docker-compose.override.yml` (dev)
and are bound to `127.0.0.1`. `make run` / `make up` (base file) publish nothing
but Nginx. This avoids exposing data stores to the internet via the Docker
iptables/UFW bypass — see `docs/readme/security.md` → *Host Port Exposure
(Docker & UFW)*.

Configs live in `infra/` (compose, nginx, dockerfiles, redis/postgres, requirements).

## Containers
- **Postgres:** `infra/postgres/Dockerfile`, stores data in volume.
- **App:** Uvicorn/Gunicorn serving FastAPI under a non-root runtime user.
- **Worker:** Runs taskiq tasks consumed from Redis Streams, with `IdempotencyReceiver` for worker-side dedup and `--max-async-tasks 20` concurrency (mirrored by the `tasks_engine` pool in `src/core/database/engine.py`).
- **Scheduler:** Fires periodic tasks (`schedule=[{"cron": "..."}]` on the task decorator) into the stream, including the outbox sweeper (every minute) and purge (daily) tasks, and fires delayed retries written by `SmartRetryMiddleware`; exactly one instance runs.
- **Nginx:** Reverse proxy to app with template security headers.
- **Redis:** Cache backend with password; also the taskiq broker (Streams), the retry schedule source for delayed retries, and storage for `IdempotencyReceiver` dedup markers — no task result backend.

## Cache Operations
The cache layer (`src/core/cache/`) has no dedicated Redis connection — it runs on
`app.state.redis_client`, the application client created in
`src/main/lifespan.py` and shared with auth token storage and the health probe.
There is no separate service or port to provision.

Rate limiting and taskiq do *not* share that client: `on_limiter_startup` hands
`FastAPILimiter.init` a DSN string and it opens its own pool, and the taskiq
broker (plus the retry schedule source) connects on its own as well. An API
container therefore holds three independent Redis connection pools, and a worker
or scheduler container holds the broker's — size `maxclients` from that count,
not from one pool per process.

- Under memory pressure, prefer `maxmemory-policy allkeys-lru` (or actively monitor
  `INFO stats` → `evicted_keys`). Every namespace and tag version counter is itself
  a Redis key; if the eviction policy reclaims a counter before the values it
  guards, the next read falls back to version `0` and can serve a value that a
  prior `invalidate()` or `invalidate_tags()` call was supposed to have retired.
  Counters are few and tiny — one per namespace, one per tag — so this is a
  policy question, not a capacity one.
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
- `make backup` — dumps the database to `backups/<UTC timestamp>.dump` with `pg_dump -Fc` (custom format, restorable with `pg_restore`). `backups/` is git-ignored; copy dumps off the box before it is rebuilt or recycled.
- `make restore f=backups/<file>.dump` — restores from a dump with `pg_restore --clean --if-exists`, which drops conflicting existing objects before recreating them. Run it against a stopped or otherwise quiesced app to avoid restoring under live writes.
- `make psql` — opens an interactive `psql` shell inside the Postgres container, authenticated with the compose-provided `POSTGRES_USER`/`POSTGRES_DB`.
- `make redis-cli` — opens an interactive `redis-cli` shell inside the Redis container, authenticated with `REDIS_PASSWORD`.
- `make create-admin` — bootstraps the first admin account, or promotes an existing account to admin, via `scripts/create_admin.py`; see the *Bootstrap the first admin* section in [README.md](../../README.md) for the environment variables it reads.

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
- `infra/deploy/deploy.sh` is the single deploy path, run on the box: it validates `.env`, brings up Postgres and Redis, applies migrations, then rolls `app`, `worker`, `scheduler` and restarts nginx. Migrations run before any new code serves traffic, and a failed one aborts the deploy with the previous containers still up.
- CD (`.github/workflows/deploy.yml`) normally triggers automatically once CI succeeds on `main`. It also accepts a manual `workflow_dispatch` run (Actions tab → *CD* → *Run workflow*, or `gh workflow run deploy.yml`) — the same `DEPLOY_ENABLED` gate and `deploy` concurrency group apply, so a manual run still queues behind an in-flight automatic one. Its optional `image_tag` input deploys a specific already-built image tag; left blank, it computes `sha-<12>` from the dispatched commit (`main` HEAD unless another ref is picked in the UI).
- Two modes. `BUILD=1` (the default, `make deploy-prod`) builds the image on the box — the bootstrap path, before any registry exists. `BUILD=0` with `APP_IMAGE=ghcr.io/<owner>/<repo>:sha-<12>` (`make deploy-image APP_IMAGE=…`) pulls the image CI already built; this is what CD uses, so the production box never compiles.
- `APP_IMAGE` is the only knob: unset, every service falls back to the locally built `template-app-image:latest`, so `make run` and `make run-dev` behave exactly as before.
- The box needs `docker login ghcr.io` credentials for a private package (`GHCR_USER` / `GHCR_PULL_TOKEN` in the CD workflow). Postgres stays a box-local build — CD ships application code, never the database image.
- `infra/docker-compose.yml` is production-oriented and does not mount host source code into `app`, `worker`, or `scheduler`.
- It also publishes **only** the Nginx port to the host; Postgres/Redis/app stay internal to `app-network`. If you genuinely need a backing port on the host in production, bind it to `127.0.0.1` (or restrict it via a `DOCKER-USER` firewall rule) — never the short `host:container` syntax, which binds `0.0.0.0` and bypasses UFW. See `docs/readme/security.md`.
- Source bind mounts remain only in `infra/docker-compose.override.yml` for local development.
- `infra/nginx/app.conf` sets baseline security headers at the reverse-proxy layer, while the FastAPI app keeps the same headers as a fallback for direct app access and tests. The proxy body itself lives in `infra/nginx/proxy.inc`, shared with the TLS server so the two cannot drift apart. Dev serves the same `app.conf`, only on a different published port.
- TLS terminates at Nginx: copy `infra/nginx/tls.conf.example` over the `app.conf` mount, put the certificate under `infra/nginx/certs/` (git-ignored) and set the real hostname. It redirects plain http to https and leaves the ACME challenge path reachable.
- `Strict-Transport-Security` comes from the application, not from Nginx — one header, one source. It is only appropriate once clients actually reach the site over HTTPS end to end.
- `infra/firewall/` closes the host: UFW for host listeners plus a `DOCKER-USER` chain for container traffic, installed as a systemd unit. Run `sudo bash harden-host.sh` on the server; see `infra/firewall/README.md`.
- `client_max_body_size 20m` is the current default, kept in sync with `S3_MAX_UPLOAD_SIZE_BYTES`. Change both together if the project needs larger uploads. Nginx answers 413, 502 and 504 itself, so `proxy.inc` rewrites those into the same `{"code", "message"}` JSON the app returns.
- Probes: `/live/` has no dependencies and is what the `app` container healthcheck polls. Plain Compose never restarts a container on a failed healthcheck — what an `unhealthy` app used to cost was `depends_on: service_healthy`, which kept nginx from starting, plus a misleading `docker ps`; under an orchestrator or an autoheal sidecar it costs the container. `/ready/` returns 503 while Postgres is unreachable or the connection pool cannot hand out a connection within two seconds — the gate to put in front of a load balancer. `/health/` is the detailed report for monitoring and always answers 200, with `"status": "degraded"` and a per-dependency breakdown, precisely so the body survives the outage it describes. None of them reports to Sentry: they run on a timer, and a single outage would file one event per poll.
- Liveness independence covers a running process, not a restarting one: `on_redis_startup` (`src/core/redis/lifecycle.py`) pings Redis and raises, so a container restarted during a Redis outage never reaches `/live/` at all.
- `worker` and `scheduler` have no HTTP surface, so their healthcheck pings the broker Redis from inside the container. It catches an unreachable broker or a wrong password — the container-level failure that otherwise looks fine. It does **not** prove the consumer is still pulling from the stream: a deadlocked worker with a healthy Redis still reports `healthy`. Catching that needs a heartbeat key the worker itself refreshes.
