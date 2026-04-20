# FastAPI Template

![CI](https://github.com/darkweid/fastapi-template/actions/workflows/ci.yml/badge.svg?branch=main)
![Coverage](https://coveralls.io/repos/github/darkweid/fastapi-template/badge.svg?branch=main)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Mypy](https://img.shields.io/badge/mypy-strict-success)
![License](https://img.shields.io/github/license/darkweid/fastapi-template)


Production-ready FastAPI template with modular architecture, async stack, Celery, and full Docker setup.

## Key Features
- Async FastAPI with modular domain structure.
- DB via SQLAlchemy async, repositories + Unit of Work for transactional work.
- Caching: Redis cache layer (`src/core/redis/*`) with tags, decorators, lifecycle helpers, and deterministic ETag support for route responses.
- Rate limiting: limiter package (`src/core/limiter`) with FastAPI dependencies (both IP and user-based).
- Messaging: Celery workers/beat + RabbitMQ broker.
- Edge: Nginx reverse proxy with WebSocket upgrade headers.
- Email service: templated mailer with Celery tasks for async sending.
- Auth & JWT: user module with auth usecases, tokens, permissions.
- Storage: async S3 adapter (`src/core/storage/s3`) with presign support.
- Observability/resilience: structured logging (loggers), retry utils, health route.
- Type safety: mypy in strict mode; strict settings (no implicit Optional, no untyped defs, disallow Any in generics) keep interfaces honest and catch regressions early.
- Tooling: pre-commit/ruff/black/mypy, pytest (asyncio), Alembic migrations.

## Rate Limiting Notes
- Primary rate limiting uses Redis-backed `RateLimiter` dependencies from `src/core/limiter`.
- If Redis is temporarily unavailable, the limiter falls back to an in-memory per-process window so protection still works in degraded mode.
- Be careful in multi-instance deployments: this fallback is not distributed, so each instance enforces its own local counter and the effective global limit becomes higher than the configured value.
- Even with that limitation, the fallback is still useful because requests remain best-effort rate-limited instead of becoming completely unlimited during a Redis outage.

## Tooling
![Ruff](https://img.shields.io/badge/ruff-lint-2C2C2C?logo=ruff&logoColor=white)
![Black](https://img.shields.io/badge/black-formatter-000000?logo=black&logoColor=white)
![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)

## Security Checks
- CI runs a dedicated security job in `.github/workflows/ci.yml`.
- `bandit` scans application, migration, and script code for insecure patterns.
- `pip-audit` checks pinned files `infra/requirements/base.txt`, `infra/requirements/dev.txt`, and `infra/requirements/prod.txt` for known vulnerable packages.
- `gitleaks` scans the repository for committed secrets.
- `gitleaks` keeps history scanning enabled and uses a repo allowlist only for known example/test placeholders.
- `pip-audit` currently ignores `CVE-2026-4539` explicitly because `pygments==2.19.2` is present in the dev graph and no fix version is reported yet.
- These checks are intended to fail the pipeline on real findings, so dependency updates should keep the pinned requirement files current.

## Quick Start
- Install Docker and Docker Compose, Python 3.13 (for local scripts/hooks).
- Copy env: `cp .env.example .env` and fill required values. For tests you can also use `.env.test` (picked up when `TESTING=true` in env).
- Dev with reload: `make run-dev` (Nginx on 8000, app on 8001).
- Prod-like: `make run`.
- Stop: `make down`; logs: `make logs`; tests: `make test` / `make test-cov`; lint: `make lint`.

## Testing Layout
- Application tests mirror `src/` under `tests/unit/src/`.
- Shared test infrastructure lives in `tests/conftest.py`, `tests/helpers/`, `tests/fakes/`, and `tests/factories/`.
- Reserve `tests/integration/src/` for integration coverage when a scenario requires more than unit-level wiring.
- Run a focused file with `TESTING=true pytest tests/unit/src/<module>/test_<name>.py`.

## Ports
- Nginx: 8000 → app:8001
- App direct: 8001
- Postgres: 5432
- Redis: 6379
- RabbitMQ: 5672 (AMQP), 15672 (UI)

## Common Services
- API docs: http://localhost:8000/docs (or http://localhost:8001/docs directly)
- Health: http://localhost:8001/health/

## Useful Make Targets
- `make run-dev` — build+up with override (reload)
- `make run` — build+up prod-like
- `make migrate` / `make migration` — apply/create Alembic revisions
- `make logs` / `make logs-app` — view logs
- `make clean` — remove containers/volumes/images/orphans
- `make lint` / `make test` — quality checks
- `make test-cov` — tests with coverage report

## Pre-commit Hooks
- Install dev deps: `pip install -r infra/requirements/dev.txt`
- Update hooks: `pre-commit autoupdate` (and commit `.pre-commit-config.yaml` changes)
- Clean hook envs if needed: `pre-commit clean`
- Run all hooks locally: `pre-commit run --all-files` or `make lint`

## Optional Local Security Runs
- Install tools: `pip install bandit pip-audit`
- Static scan: `bandit -r src scripts migrations -q`
- Dependency audit: `pip-audit --ignore-vuln CVE-2026-4539 -r infra/requirements/base.txt -r infra/requirements/dev.txt -r infra/requirements/prod.txt`
- Secret scan: `gitleaks detect --source .`

## Dependencies (pip-tools)
- Source files: `infra/requirements/*.in` contain direct dependencies (typically without pins).
- Lockfiles: `infra/requirements/*.txt` are generated by `pip-compile`.
- Update lockfiles: `make req-compile`
- Sync environment: `make req-sync-dev` / `make req-sync-prod`
- When needed, add pins or ranges in `.in` (e.g. `fastapi>=0.110,<1`) and recompile.
- `make req-compile` runs `pip-compile` inside `python:3.13-slim-bookworm` with `linux/amd64` by default to keep lockfiles close to production.
- Override the target platform when needed, for example `make req-compile REQ_COMPILE_PLATFORM=linux/arm64`.

## Documentation
- Architecture & structure: [docs/readme/architecture.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/architecture.md)
- Infrastructure & ops: [docs/readme/infra.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/infra.md)
- Contributing & CI/CD: [docs/readme/contributing.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/contributing.md)
