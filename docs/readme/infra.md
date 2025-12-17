# Infrastructure and Operations

## Services and Ports
- Nginx: 8000 (proxies to app)
- App: 8001 (FastAPI)
- Postgres: 5432
- Redis: 6379
- RabbitMQ: 5672 (AMQP), 15672 (UI)
- Flower: 5555

Configs live in `infra/` (compose, nginx, dockerfiles, redis/postgres, requirements).

## Containers
- **Postgres:** `infra/postgres/Dockerfile-postgis`, stores data in volume.
- **App:** Uvicorn/Gunicorn serving FastAPI.
- **Celery_worker:** Background tasks.
- **Celery_beat:** Schedules periodic tasks.
- **Flower:** Celery monitoring UI.
- **Nginx:** Reverse proxy to app.
- **Redis:** Cache/result backend with password.
- **RabbitMQ:** Broker with management UI.

## Prerequisites
- Python 3.13 (for local scripts/hooks)
- Docker
- Docker Compose

## Quick Start
```bash
cp .env.example .env   # main env
cp .env.test .env.test # optional test env (used when TESTING=true)
make run-dev          # dev images + autoreload, exposes 8000 via nginx
# or:
make run              # prod-like build
```

Open:
- App via Nginx: http://localhost:8000
- Docs: http://localhost:8000/docs
- Direct app (bypass Nginx): http://localhost:8001/docs
- Flower: http://localhost:5555

## Common Commands
```bash
make run-dev          # build+up with override (reload)
make run              # build+up prod-like
make logs             # tail all services
make logs-app         # app logs
make migrate          # alembic upgrade head
make migration        # create alembic revision
make test             # pytest
make lint             # pre-commit hooks
make down             # stop stack
make clean            # remove stack + volumes/images/orphans
```

## Troubleshooting
- Ensure Docker/Compose are installed.
- `.env` must be filled (ports, DB/Redis/RabbitMQ credentials). `.env.test` used for local test runs `make test`.
- Use `make logs` or service-specific logs to inspect errors.
- If migrations fail, check Postgres health first.
