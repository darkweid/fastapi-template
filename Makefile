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

.PHONY: deploy-prod
deploy-prod:
	make build && make down && make up && make migrate

.PHONY: deploy-dev
deploy-dev:
	make run && make migrate

# Restart containers
.PHONY: restart
restart:
	$(DOCKER_COMPOSE) restart

# Remove volumes and clean up
.PHONY: clean
clean:
	$(DOCKER_COMPOSE) down -v --rmi local --remove-orphans

# Clean up unused Docker resources, keeping build cache and reusable images.
# Safe to run after deployment without affecting performance.
.PHONY: clean-resources
clean-resources:
	docker image prune -f
	docker container prune -f
	docker builder prune -f

# Aggressively clean up all Docker resources, including all unused images and build cache.
# Warning: This will force full rebuilds of all images on next build.
.PHONY: clean-resources-hard
clean-resources-hard:
	docker image prune -a -f
	docker container prune -f
	docker builder prune -a -f

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

.PHONY: test
test:
	pytest

.PHONY: info
info:
	@echo "================== FastAPI Template Info =================="
	@echo "Project based on FastAPI template"
	@echo ""
	@echo "Containers status:"
	@$(DOCKER_COMPOSE) ps
	@echo ""
	@echo "Useful commands:"
	@echo "  make build              # Build all containers"
	@echo "  make up                 # Start containers"
	@echo "  make run                # Build and start containers"
	@echo "  make down               # Stop and remove containers"
	@echo "  make restart            # Restart all running containers"
	@echo "  make clean              # Remove containers, volumes, orphans"
	@echo "  make clean-resources    # Remove all unused Docker resources"
	@echo "  make shell              # Enter bash inside the app container"
	@echo "  make migrate            # Apply Alembic migrations"
	@echo "  make migration          # Create Alembic migration (use: make migration message='msg')"
	@echo "  make celery-worker      # Start Celery worker"
	@echo "  make stop-celery        # Stop Celery worker"
	@echo "  make logs               # Show all logs"
	@echo "  make logs-app           # Show logs from the app container"
	@echo "  make logs-celery        # Show logs from the celery_worker container"
	@echo "  make logs-celery-beat   # Show logs from the celery_beat container"
	@echo "  make logs-postgres      # Show logs from the postgres container"
	@echo ""
	@echo "==========================================================="
