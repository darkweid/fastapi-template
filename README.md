# FastAPI Template

![CI](https://github.com/darkweid/fastapi-template/actions/workflows/ci.yml/badge.svg?branch=main)
![Coverage](https://coveralls.io/repos/github/darkweid/fastapi-template/badge.svg?branch=main)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Mypy](https://img.shields.io/badge/mypy-strict-success)
![License](https://img.shields.io/github/license/darkweid/fastapi-template)


Production-ready FastAPI template with modular architecture, async stack, and full Docker setup.

## Key Features
- Async FastAPI with modular domain structure.
- DB via SQLAlchemy async, repositories + Unit of Work for transactional work.
- Caching: explicit cache layer (`src/core/cache/*`) - `Cache` protocol, per-domain key builders, version-counter invalidation by namespace or by cross-namespace tag, and opt-in route caching with ETag/304 support.
- Rate limiting: limiter package (`src/core/limiter`) with FastAPI dependencies (both IP and user-based).
- Messaging: taskiq worker/scheduler over Redis Streams with a transactional outbox (atomic enqueue with the DB transaction, worker-side dedup, delayed retries). Background tasks are enqueued via `TaskDispatcher` (`enqueue` for fire-and-forget, `enqueue_transactional` to enqueue inside a UnitOfWork transaction).
- Edge: Nginx reverse proxy with WebSocket upgrade headers.
- Email service: templated mailer with async tasks for sending.
- Auth & JWT: user module with auth usecases, tokens, permissions.
- Storage: async S3 adapter (`src/core/storage/s3`) with presign support.
- Observability/resilience: structured logging (loggers), retry utils, health route.
- Type safety: mypy in strict mode; strict settings (no implicit Optional, no untyped defs, disallow Any in generics) keep interfaces honest and catch regressions early.
- Tooling: pre-commit/ruff/black/mypy, pytest (asyncio), Alembic migrations.

## Email Links
Verification and password-reset emails link to your front-end, never to the API, and
never to a host taken from the request — a forged `Host` on `POST /password/reset`
would otherwise send the victim a genuine email pointing at the attacker's domain.
Three settings govern the link (`src/main/config.py`, `AppConfig`):

- `PUBLIC_BASE_URL` — **required, no default.** Absolute origin of the front-end,
  e.g. `https://app.example.com`.
- `EMAIL_VERIFY_PATH` (default `/verify-email`) and `PASSWORD_RESET_PATH`
  (default `/reset-password`) — the pages that receive `?token=...`.

Those pages read the token out of the query string and call the API themselves:
`GET /v1/users/auth/verify?token=...` and `PUT /v1/users/auth/password/reset/confirm`.

`scripts/check_env.py` (run by the deploy workflow) rejects a `PUBLIC_BASE_URL`
pointing at localhost, so the example value cannot reach a deploy unnoticed.

Upgrading an existing fork: the email tasks no longer take `base_url` and the
path as arguments, so any message already sitting in the outbox or the broker
carries a payload the new signature cannot bind. Drain the queue before rolling
out, or expect those specific emails to fail their retries and be dropped.

## Auth Cookie & CSRF Configuration
The refresh token is delivered as an httponly cookie by default, with a stateless
signed double-submit CSRF check on the refresh route; native clients that want the
refresh token in the response body instead send `X-Token-Transport: body`. See
[docs/src/user/auth/REFRESH_TOKEN_IMPLEMENTATION.md](docs/src/user/auth/REFRESH_TOKEN_IMPLEMENTATION.md)
for the full contract. Four settings in `.env` govern this (`src/main/config.py`, `CookieConfig`):

- `CSRF_SECRET_KEY` — **required, no default.** The app will not start without it, so
  put a long random value in `.env`. Rotating it invalidates every outstanding CSRF
  token immediately.
- `COOKIE_SECURE` (default `true`) — set to `false` only for local plain-http
  development (`.env.test` does this, since the ASGI test client talks http and an
  httpx2 cookie jar refuses to store a `Secure` cookie received over http). Never
  ship `false` to a real environment.
- `COOKIE_SAMESITE` (default `lax`) — set to `none` for a cross-origin SPA; the CSRF
  check is what makes `none` survivable against CSRF (cookie injection from a
  sibling subdomain is explicitly out of scope for this design). Two prerequisites,
  both mandatory:
  - `COOKIE_SECURE=true`. Browsers discard a `SameSite=None` cookie that is not
    `Secure`, and they do so silently, so the app would look healthy while every
    client lost its session. `CookieConfig` refuses to start on that combination.
  - An explicit CORS origin allowlist. `CORS_ALLOWED_ORIGINS` defaults to `[]` and
    `AppConfig` refuses to start on `["*"]` together with
    `CORS_ALLOWED_CREDENTIALS=true`, because Starlette's `CORSMiddleware` then echoes
    back whatever origin asks — list the real front-end origins. If
    `CORS_ALLOWED_HEADERS` was ever narrowed from `["*"]`, it must list
    `X-CSRF-Token` and `X-Token-Transport`.
- `COOKIE_DOMAIN` (default unset/blank) — leave blank unless the auth cookies must
  be shared across subdomains.

The refresh cookie is scoped to the refresh route (`/v1/users/auth/login/refresh`);
the readable `csrf_token` cookie is set at `path=/` so that a same-origin SPA can
read it from `document.cookie`. A cross-origin SPA cannot read an API-origin cookie
at all — it takes the value from the `csrf_token` field of the login/refresh response
body instead. Both sources carry the same token; either one is echoed back in the
`X-CSRF-Token` header on the next refresh.

## Rate Limiting Notes
- Primary rate limiting uses Redis-backed `RateLimiter` dependencies from `src/core/limiter`.
- If Redis is temporarily unavailable, the limiter falls back to an in-memory per-process window so protection still works in degraded mode.
- Be careful in multi-instance deployments: this fallback is not distributed, so each instance enforces its own local counter and the effective global limit becomes higher than the configured value.
- Even with that limitation, the fallback is still useful because requests remain best-effort rate-limited instead of becoming completely unlimited during a Redis outage.

## Response Caching
`GET /v1/users/{user_id}` (requires the `VIEW_USERS` permission) is the template's
one live example of route caching (`@cached_route`, `src/core/cache/decorators.py`).
A client should expect and can rely on:
- `Cache-Control: public, max-age=60` — the response is safe for a shared cache to
  store, because the body is identical for every permitted viewer; see
  `docs/readme/security.md` → *Cache Architecture* for what that implies if you put
  a CDN or shared proxy in front of the API.
- `Vary: Authorization` — the response is tied to the credential that produced it,
  so a shared cache must not replay it to a caller presenting different
  credentials, or none.
- `ETag` — a weak validator computed from the cached payload. Send it back as
  `If-None-Match` on the next request; a match returns `304 Not Modified` with no
  body.
- `X-Cache-Status: HIT` or `MISS` — whether the response came from the cache or was
  computed and stored on this call.

`PATCH /v1/users/me` invalidates the cached summary for the updated user as part of
the same transaction, so a `GET` that starts after the `PATCH` has returned normally
observes the new body. Two gaps are left open on purpose, both bounded by the TTL:
a `GET` that missed the cache *before* the `PATCH` and is still computing writes its
already-stale body after the invalidation; and if Redis is unreachable at that
moment, the version bump is swallowed (the cache never fails a request) while the
transaction commits, so a value cached before the outage keeps serving until it
expires.

That invalidation names one user. A write that touches many at once — a bulk
import, a role migration — instead flushes them all through the tag every user key
carries (`USER_CACHE_TAG`, `src/user/cache_keys.py`):
`await cache.invalidate_tags(USER_CACHE_TAG)`. A tag is an extra invalidation unit
declared on the key itself, so it cuts across namespaces, and clearing it costs one
Redis increment however many entries carry it.

## Tooling
![Ruff](https://img.shields.io/badge/ruff-lint-2C2C2C?logo=ruff&logoColor=white)
![Black](https://img.shields.io/badge/black-formatter-000000?logo=black&logoColor=white)
![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)

## Security Checks
- CI runs dedicated security jobs in `.github/workflows/ci.yml`.
- `bandit` scans application, migration, and script code for insecure patterns.
- `pip-audit` checks pinned files `infra/requirements/base.txt`, `infra/requirements/dev.txt`, and `infra/requirements/prod.txt` for known vulnerable packages. Advisories that cannot be fixed yet are ignored explicitly via `--ignore-vuln`, with the reason documented next to the flag in the workflow.
- `gitleaks` scans the repository for committed secrets.
- `gitleaks` keeps history scanning enabled and uses a repo allowlist only for known example/test placeholders.
- These checks are intended to fail the pipeline on real findings, so dependency updates should keep the pinned requirement files current.

## Quick Start
- Install Docker and Docker Compose, Python 3.13 (for local scripts/hooks).
- Create and activate a local virtualenv:
  ```bash
  python3.13 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip pip-tools
  make req-sync-dev
  ```
- Copy env: `cp .env.example .env` and fill required values. For tests you can also use `.env.test` (picked up when `TESTING=true` in env).
- Dev with reload: `make run-dev` (Nginx on 8000, app on 8001).
- Prod-like: `make run`.
- Stop: `make down`; logs: `make logs`; tests: `make test` / `make test-cov`; lint: `make lint`.
- Run `make` with no target to see every command the Makefile offers.

## Testing Layout
- Application tests mirror `src/` under `tests/unit/src/`.
- Shared test infrastructure lives in `tests/conftest.py`, `tests/helpers/`, `tests/fakes/`, and `tests/factories/`.
- Reserve `tests/integration/src/` for integration coverage when a scenario requires more than unit-level wiring.
- Run a focused file with `TESTING=true pytest tests/unit/src/<module>/test_<name>.py`.

## Ports
Only Nginx is published to the host; the rest stay internal to `app-network`.
Backing ports are re-exposed on `127.0.0.1` in dev (`make run-dev`) only — see
`docs/readme/security.md` → *Host Port Exposure (Docker & UFW)*.

- Nginx: 80 / 443 → app:8001 — **public** (`0.0.0.0`); dev publishes 8000 instead
- App direct: 8001 — internal (dev: `127.0.0.1`)
- Postgres: 5432 — internal (dev: `127.0.0.1`)
- Redis: 6379 — internal (dev: `127.0.0.1`)

On a server, close everything else with `infra/firewall/` (UFW plus a
`DOCKER-USER` chain, since Docker-published ports bypass UFW):

```bash
scp -r infra/firewall <host>:/tmp/firewall
ssh <host> 'sudo bash /tmp/firewall/harden-host.sh'
```

TLS terminates at Nginx — `infra/nginx/tls.conf.example` is a drop-in replacement
for `app.conf` once the certificate is in place.

## Common Services
- API docs: http://localhost:8000/docs (direct app http://localhost:8001/docs — dev only).
  `/docs`, `/redoc` and `/openapi.json` are open only while `DEBUG=true`. Otherwise they
  are served behind HTTP Basic using `DOCS_USERNAME` / `DOCS_PASSWORD`, and are not
  published at all while either of the two is blank. The password takes the same
  32-character minimum as the other secrets, must be ASCII, and the three routes are
  rate limited — Basic auth has no lockout of its own.
- Health: http://localhost:8000/health/ (direct app http://localhost:8001/health/ — dev only)

## Useful Make Targets
- `make` (or `make help`) — list every target with its description
- `make run-dev` — build+up with override (reload)
- `make run` — build+up prod-like
- `make migrate` / `make migration m="add users table"` — apply/create Alembic revisions
- `make logs` — tail all services; `make logs s=app` — a single one
- `make clean` — remove containers/volumes/images/orphans
- `make lint` / `make test` — quality checks
- `make test-cov` — tests with coverage report

## Pre-commit Hooks
- Install dev deps: `make req-sync-dev`
- Update hooks: `pre-commit autoupdate` (and commit `.pre-commit-config.yaml` changes)
- Clean hook envs if needed: `pre-commit clean`
- Run all hooks locally: `pre-commit run --all-files` or `make lint`

## Optional Local Security Runs
- Install tools: `pip install bandit pip-audit`
- Static scan: `bandit -r src scripts migrations -q`
- Dependency audit: `pip-audit -r infra/requirements/base.txt -r infra/requirements/dev.txt -r infra/requirements/prod.txt`
- Secret scan: `gitleaks detect --source .`

## Dependencies (pip-tools)
- Source files: `infra/requirements/*.in` contain direct dependencies (typically without pins).
- Lockfiles: `infra/requirements/*.txt` are generated by `pip-compile`.
- Update lockfiles: `make req-compile` (resolves only what changed in the `.in` files)
- Bump every pin to its newest allowed release: `make req-upgrade`
- Sync environment: `make req-sync-dev` / `make req-sync-prod`
- When needed, add pins or ranges in `.in` (e.g. `fastapi>=0.110,<1`) and recompile.
- `make req-compile` runs `pip-compile` inside `python:3.13-slim-bookworm` with `linux/amd64` by default to keep lockfiles close to production.
- Override the target platform when needed, for example `make req-compile REQ_COMPILE_PLATFORM=linux/arm64`.

## Documentation
- Architecture & structure: [docs/readme/architecture.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/architecture.md)
- Infrastructure & ops: [docs/readme/infra.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/infra.md)
- Security mechanisms: [docs/readme/security.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/security.md)
- Contributing & CI/CD: [docs/readme/contributing.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/contributing.md)
