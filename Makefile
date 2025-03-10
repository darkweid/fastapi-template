# Docker settings
DOCKER_COMPOSE = docker compose
DOCKER_COMPOSE_EXEC = $(DOCKER_COMPOSE) exec

# Container names
APP_CONTAINER = app
CELERY_CONTAINER = celery_worker
CELERY_BEAT_CONTAINER = celery_beat
POSTGRES_CONTAINER = postgres
REDIS_CONTAINER = redis

# Build Docker containers
.PHONY: build
build:
	$(DOCKER_COMPOSE) build

# Up Docker containers
.PHONY: up
up:
	$(DOCKER_COMPOSE) up -d

# Run Docker containers
.PHONY: run
run:
	$(DOCKER_COMPOSE) up --build -d

# Stop the Docker containers
.PHONY: down
down:
	$(DOCKER_COMPOSE) down

# Restart containers
.PHONY: restart
restart:
	$(DOCKER_COMPOSE) restart

# Remove volumes and clean up
.PHONY: clean
clean:
	$(DOCKER_COMPOSE) down -v --rmi local --remove-orphans

# Alembic: Create a new migration
.PHONY: migration
migration:
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) alembic revision --autogenerate --message "$(message)"

# Alembic: Apply migrations
.PHONY: migrate
migrate:
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) alembic upgrade head

# Start the Celery worker
.PHONY: celery-worker
celery-worker:
	$(DOCKER_COMPOSE) up -d $(CELERY_CONTAINER)

# Stop Celery worker
.PHONY: stop-celery
stop-celery:
	$(DOCKER_COMPOSE) stop $(CELERY_CONTAINER)

# Execute a bash shell inside the app container
.PHONY: shell
shell:
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) /bin/bash

# View logs for all services
.PHONY: logs
logs:
	$(DOCKER_COMPOSE) logs -f

# View logs for a specific service
.PHONY: logs-app
logs-app:
	$(DOCKER_COMPOSE) logs -f $(APP_CONTAINER)

.PHONY: logs-celery
logs-celery:
	$(DOCKER_COMPOSE) logs -f $(CELERY_CONTAINER)

.PHONY: logs-celery-beat
logs-celery-beat:
	$(DOCKER_COMPOSE) logs -f $(CELERY_BEAT_CONTAINER)

.PHONY: logs-postgres
logs-postgres:
	$(DOCKER_COMPOSE) logs -f $(POSTGRES_CONTAINER)
