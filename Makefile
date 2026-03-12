# Docker settings
COMPOSE_BASE = infra/docker-compose.yml
COMPOSE_DEV = infra/docker-compose.override.yml
DOCKER_COMPOSE = docker compose --env-file .env -f $(COMPOSE_BASE)
DOCKER_COMPOSE_DEV = docker compose --env-file .env -f $(COMPOSE_BASE) -f $(COMPOSE_DEV)
DOCKER_COMPOSE_EXEC = $(DOCKER_COMPOSE) exec

# Container names
APP_CONTAINER = app
CELERY_CONTAINER = celery_worker
CELERY_BEAT_CONTAINER = celery_beat
POSTGRES_CONTAINER = postgres
REDIS_CONTAINER = redis

# Requirements management
REQ_DIR = infra/requirements
REQ_BASE_IN = $(REQ_DIR)/base.in
REQ_DEV_IN = $(REQ_DIR)/dev.in
REQ_PROD_IN = $(REQ_DIR)/prod.in
REQ_BASE_TXT = $(REQ_DIR)/base.txt
REQ_DEV_TXT = $(REQ_DIR)/dev.txt
REQ_PROD_TXT = $(REQ_DIR)/prod.txt

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

# Run Docker containers in development mode with auto-reload
.PHONY: run-dev
run-dev:
	$(DOCKER_COMPOSE_DEV) up --build -d
	docker restart template-nginx

# Stop the Docker containers
.PHONY: down
down:
	$(DOCKER_COMPOSE) down

.PHONY: deploy-prod
deploy-prod:
	make build && make down && make up && make migrate

.PHONY: deploy-dev
deploy-dev:
	make run-dev && make migrate

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
	@read -p "Enter migration message: " MSG; \
	if [ -z "$$MSG" ]; then \
	  echo "Migration message cannot be empty"; exit 1; \
	fi; \
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) alembic revision --autogenerate --message "$$MSG"

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

.PHONY: lint
lint:
	pre-commit run --all-files

.PHONY: req-compile
req-compile:
	python -m pip install --upgrade pip pip-tools
	python scripts/sort_requirements_in.py infra/requirements/base.in infra/requirements/dev.in infra/requirements/prod.in
	cd infra/requirements && python -m piptools compile base.in -o base.txt
	cd infra/requirements && python -m piptools compile dev.in -o dev.txt
	cd infra/requirements && python -m piptools compile prod.in -o prod.txt

.PHONY: req-sync-dev
req-sync-dev:
	python -m piptools sync $(REQ_DEV_TXT)

.PHONY: req-sync-prod
req-sync-prod:
	python -m piptools sync $(REQ_PROD_TXT)

.PHONY: check-lint
check-lint:
	pre-commit run --all-files --hook-stage push --verbose

.PHONY: test
test:
	TESTING=true pytest

.PHONY: test-cov
test-cov:
	TESTING=true pytest --cov=src --cov-report=term-missing --cov-report=xml

.PHONY: check-coverage
check-coverage:
	pytest --cov --cov-report=term-missing

.PHONY: count-code-lines
count-code-lines:
	find . -path './.venv' -prune -o -type f -name '*.py' -print0 | xargs -0 wc -l | tail -1


.PHONY: info
info:
	@echo "╔══════════════════════════════════════════════════════════╗"
	@echo "║                  FastAPI Template Info                   ║"
	@echo "╚══════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "🐋 Container Status:"
	@$(DOCKER_COMPOSE) ps
	@echo ""
	@echo "📦 Environment:"
	@echo "   • Docker Compose: $(DOCKER_COMPOSE)"
	@echo "   • App Container: $(APP_CONTAINER)"
	@echo "   • Database: $(POSTGRES_CONTAINER)"
	@echo "   • Cache: $(REDIS_CONTAINER)"
	@echo "   • Task Queue: $(CELERY_CONTAINER), $(CELERY_BEAT_CONTAINER)"
	@echo ""
	@echo "🚀 Development Commands:"
	@echo "   • make build              # Build all containers"
	@echo "   • make up                 # Start containers"
	@echo "   • make run                # Build and start containers"
	@echo "   • make run-dev            # Build and start containers with auto-reload (development mode)"
	@echo "   • make down               # Stop and remove containers"
	@echo "   • make restart            # Restart all running containers"
	@echo "   • make deploy-dev         # Build, start containers with auto-reload and migrate DB"
	@echo "   • make deploy-prod        # Production deployment sequence"
	@echo "   • make count-code-lines   # Count code lines in Python files (exclude venv)"
	@echo ""
	@echo "🔧 Maintenance Commands:"
	@echo "   • make clean              # Remove containers, volumes, orphans"
	@echo "   • make clean-resources    # Remove unused Docker resources"
	@echo "   • make clean-resources-hard # Aggressively clean all Docker resources"
	@echo ""
	@echo "🛠️  Database Commands:"
	@echo "   • make migrate            # Apply Alembic migrations"
	@echo "   • make migration message='msg' # Create new Alembic migration"
	@echo ""
	@echo "🔍 Debugging & Monitoring:"
	@echo "   • make shell              # Enter bash inside the app container"
	@echo "   • make logs               # Show all logs"
	@echo "   • make logs-app           # Show logs from the app container"
	@echo "   • make logs-celery        # Show logs from the template-celery-worker container"
	@echo "   • make logs-celery-beat   # Show logs from the template-celery-beat container"
	@echo "   • make logs-postgres      # Show logs from the template-postgres container"
	@echo ""
	@echo "⚙️  Task Queue:"
	@echo "   • make celery-worker      # Start Celery worker"
	@echo "   • make stop-celery        # Stop Celery worker"
	@echo ""
	@echo "🧪 Testing & Quality:"
	@echo "   • make test               # Run all tests"
	@echo "   • make check-coverage     # Check coverage report"
	@echo "   • make lint               # Run linting on all files"
	@echo "   • make check-lint         # Check linting during push"
	@echo ""
	@echo "📦 Dependencies:"
	@echo "   • make req-compile        # Compile requirements (*.in -> *.txt)"
	@echo "   • make req-sync-dev       # Sync dev environment with requirements"
	@echo "   • make req-sync-prod      # Sync prod environment with requirements"
	@echo ""
