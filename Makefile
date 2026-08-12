# Docker settings
COMPOSE_BASE = infra/docker-compose.yml
COMPOSE_DEV = infra/docker-compose.override.yml
DOCKER_COMPOSE = docker compose --env-file .env -f $(COMPOSE_BASE)
DOCKER_COMPOSE_DEV = docker compose --env-file .env -f $(COMPOSE_BASE) -f $(COMPOSE_DEV)
DOCKER_COMPOSE_EXEC = $(DOCKER_COMPOSE) exec

# Container names
APP_CONTAINER = app
WORKER_CONTAINER = worker

# Requirements management
REQ_DIR = infra/requirements
REQ_NAMES = base dev prod security
REQ_DEV_TXT = $(REQ_DIR)/dev.txt
REQ_PROD_TXT = $(REQ_DIR)/prod.txt
REQ_COMPILE_IMAGE ?= python:3.13-slim-bookworm
REQ_COMPILE_PLATFORM ?= linux/amd64
REQ_COMPILE_USER = $(shell id -u):$(shell id -g)

.DEFAULT_GOAL := help

##@ Stack

.PHONY: build
build: ## Build the images
	$(DOCKER_COMPOSE) build

.PHONY: up
up: ## Start the containers
	$(DOCKER_COMPOSE) up -d

.PHONY: run
run: ## Build and start the prod-like stack
	$(DOCKER_COMPOSE) up --build -d

.PHONY: run-dev
run-dev: ## Build and start the dev stack with autoreload
	$(DOCKER_COMPOSE_DEV) up --build -d
# nginx resolves the app upstream once at startup, so a rebuilt app container leaves it serving 502 until nginx restarts
	docker restart template-nginx

.PHONY: down
down: ## Stop and remove the containers
	$(DOCKER_COMPOSE) down

.PHONY: shell
shell: ## Open a bash shell inside the app container
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) /bin/bash

##@ Deploy

.PHONY: deploy-prod
deploy-prod: ## Build, swap the containers and migrate
	$(MAKE) build
	$(MAKE) down
	$(MAKE) up
	$(MAKE) migrate

##@ Cleanup

.PHONY: clean
clean: ## Remove the stack together with its volumes, local images and orphans
	$(DOCKER_COMPOSE) down -v --rmi local --remove-orphans

.PHONY: clean-resources
clean-resources: ## Remove unused Docker resources, keeping build cache and reusable images
	docker image prune -f
	docker container prune -f
	docker builder prune -f

.PHONY: clean-resources-hard
clean-resources-hard: ## Remove all unused images and build cache, forcing full rebuilds next time
	docker image prune -a -f
	docker container prune -f
	docker builder prune -a -f

##@ Database

.PHONY: migrate
migrate: ## Apply Alembic migrations
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) alembic upgrade head

.PHONY: migration
migration: ## Create an Alembic revision: make migration m="add users table"
	@MSG="$(m)"; \
	if [ -z "$$MSG" ]; then printf 'Enter migration message: '; read -r MSG; fi; \
	if [ -z "$$MSG" ]; then echo "Migration message cannot be empty"; exit 1; fi; \
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) alembic revision --autogenerate --message "$$MSG"

##@ Worker

.PHONY: worker
worker: ## Start the task worker
	$(DOCKER_COMPOSE) up -d $(WORKER_CONTAINER)

.PHONY: stop-worker
stop-worker: ## Stop the task worker
	$(DOCKER_COMPOSE) stop $(WORKER_CONTAINER)

##@ Logs

.PHONY: logs
logs: ## Follow logs for every service, or one: make logs s=app
	$(DOCKER_COMPOSE) logs -f $(s)

##@ Quality

.PHONY: lint
lint: ## Run every pre-commit hook
	pre-commit run --all-files

.PHONY: check-lint
check-lint: ## Run the pre-commit hooks of the push stage
	pre-commit run --all-files --hook-stage push --verbose

.PHONY: test
test: ## Run the tests
	TESTING=true pytest

.PHONY: test-cov
test-cov: ## Run the tests with a coverage report
	TESTING=true pytest --cov=src --cov-report=term-missing --cov-report=xml

.PHONY: count-code-lines
count-code-lines: ## Count Python lines, excluding the virtualenv
	find . -path './.venv' -prune -o -type f -name '*.py' -print0 | xargs -0 wc -l | tail -1

##@ Dependencies

.PHONY: req-compile
req-compile: ## Recompile the lockfiles inside a Linux container
	docker run --rm --platform=$(REQ_COMPILE_PLATFORM) \
		-e HOME=/tmp \
		-u $(REQ_COMPILE_USER) \
		-v $(CURDIR):/app \
		-w /app \
		$(REQ_COMPILE_IMAGE) \
		sh -lc 'set -e; python -m pip install --user --no-cache-dir --upgrade pip pip-tools && python scripts/sort_requirements_in.py $(addprefix $(REQ_DIR)/,$(addsuffix .in,$(REQ_NAMES))) && cd $(REQ_DIR) && for name in $(REQ_NAMES); do python -m piptools compile "$${name}.in" -o "$${name}.txt"; done'

.PHONY: req-sync-dev
req-sync-dev: ## Install the dev lockfile into the active environment
	python -m piptools sync $(REQ_DEV_TXT)

.PHONY: req-sync-prod
req-sync-prod: ## Install the prod lockfile into the active environment
	python -m piptools sync $(REQ_PROD_TXT)

##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
