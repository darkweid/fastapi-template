# FastAPI Template

Production-ready FastAPI template with modular architecture, async stack, Celery, and full Docker setup.

## Key Features
- Async FastAPI with modular domain structure.
- DB via SQLAlchemy async, repositories + Unit of Work for transactional work.
- Caching: Redis cache layer (`src/core/redis/*`) with tags, decorators, lifecycle helpers.
- Rate limiting: limiter package (`src/core/limiter`) with FastAPI dependencies (both IP and user-based).
- Messaging: Celery workers/beat + RabbitMQ broker, Flower monitoring UI.
- Edge: Nginx reverse proxy with WebSocket upgrade headers.
- Email service: templated mailer with Celery tasks for async sending.
- Auth & JWT: user module with auth usecases, tokens, permissions.
- Storage: async S3 adapter (`src/core/storage/s3`) with presign support.
- Observability/resilience: structured logging (loggers), retry utils, health route.
- Type safety: mypy in strict mode; strict settings (no implicit Optional, no untyped defs, disallow Any in generics) keep interfaces honest and catch regressions early.
- Tooling: pre-commit/ruff/black/mypy, pytest (asyncio), Alembic migrations.

## Quick Start
- Install Docker and Docker Compose, Python 3.13 (for local scripts/hooks).
- Copy env: `cp .env.example .env` and fill required values. For tests you can also use `.env.test` (picked up when `TESTING=true` in env).
- Dev with reload: `make run-dev` (Nginx on 8000, app on 8001).
- Prod-like: `make run`.
- Stop: `make down`; logs: `make logs`; tests: `make test`; lint: `make lint`.

## Ports
- Nginx: 8000 → app:8001
- App direct: 8001
- Postgres: 5432
- Redis: 6379
- RabbitMQ: 5672 (AMQP), 15672 (UI)
- Flower: 5555

## Common Services
- API docs: http://localhost:8000/docs (or http://localhost:8001/docs directly)
- Flower: http://localhost:5555
- Health: http://localhost:8001/health/

## Useful Make Targets
- `make run-dev` — build+up with override (reload)
- `make run` — build+up prod-like
- `make migrate` / `make migration` — apply/create Alembic revisions
- `make logs` / `make logs-app` — view logs
- `make clean` — remove containers/volumes/images/orphans
- `make lint` / `make test` — quality checks

## Pre-commit Hooks
- Install dev deps: `pip install -r infra/requirements/dev.txt`
- Update hooks: `pre-commit autoupdate` (and commit `.pre-commit-config.yaml` changes)
- Clean hook envs if needed: `pre-commit clean`
- Run all hooks locally: `pre-commit run --all-files` or `make lint`

## Documentation
- Architecture & structure: [docs/readme/architecture.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/architecture.md)
- Infrastructure & ops: [docs/readme/infra.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/infra.md)
- Contributing & CI/CD: [docs/readme/contributing.md](https://github.com/darkweid/fastapi-template/blob/main/docs/readme/contributing.md)
