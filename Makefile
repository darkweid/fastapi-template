# Docker settings
COMPOSE_BASE = infra/docker-compose.yml
COMPOSE_DEV = infra/docker-compose.override.yml
DOCKER_COMPOSE = docker compose --env-file .env -f $(COMPOSE_BASE)
DOCKER_COMPOSE_DEV = docker compose --env-file .env -f $(COMPOSE_BASE) -f $(COMPOSE_DEV)
DOCKER_COMPOSE_EXEC = $(DOCKER_COMPOSE) exec

# Throwaway PostgreSQL for the integration suite. Every invocation gets its own compose
# project name and an ephemeral host port so it can never touch the dev stack; both are
# built inside the test-integration recipe, which is why there is no shared variable here.
COMPOSE_TEST = infra/docker-compose.test.yml

# Container names
APP_CONTAINER = app

# Requirements management
REQ_DIR = infra/requirements
REQ_NAMES = ansible base dev prod security
REQ_DEV_TXT = $(REQ_DIR)/dev.txt
REQ_PROD_TXT = $(REQ_DIR)/prod.txt
REQ_COMPILE_IMAGE ?= python:3.13-slim-bookworm
REQ_COMPILE_PLATFORM ?= linux/amd64
REQ_COMPILE_USER = $(shell id -u):$(shell id -g)
REQ_COMPILE_FLAGS ?=

# Server provisioning (infra/ansible). Ansible lives in its own virtualenv:
# `make req-sync-dev` would uninstall it from the dev one.
ANSIBLE_DIR = infra/ansible
ANSIBLE_VENV = $(ANSIBLE_DIR)/.venv
ANSIBLE_BIN = $(ANSIBLE_VENV)/bin
ANSIBLE_PLAYBOOK = ANSIBLE_CONFIG=$(ANSIBLE_DIR)/ansible.cfg $(ANSIBLE_BIN)/ansible-playbook -i $(ANSIBLE_DIR)/inventory/$(ENV)
BOOTSTRAP_USER ?= root

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

.PHONY: down
down: ## Stop and remove the containers
	$(DOCKER_COMPOSE) down

.PHONY: shell
shell: ## Open a bash shell inside the app container
	$(DOCKER_COMPOSE_EXEC) $(APP_CONTAINER) /bin/bash

##@ Deploy

.PHONY: deploy-prod
deploy-prod: ## Deploy on the box, building the image locally
	bash infra/deploy/deploy.sh

.PHONY: deploy-image
deploy-image: ## Deploy a registry image: make deploy-image APP_IMAGE=ghcr.io/owner/repo:sha-abc123
	@test -n "$(APP_IMAGE)" || { echo "APP_IMAGE is required"; exit 1; }
	BUILD=0 APP_IMAGE=$(APP_IMAGE) bash infra/deploy/deploy.sh

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

.PHONY: backup
backup: ## Dump the database to backups/<UTC timestamp>.dump
	@mkdir -p backups
	$(DOCKER_COMPOSE_EXEC) -T postgres sh -c 'pg_dump -U $$POSTGRES_USER -Fc $$POSTGRES_DB' > backups/$$(date -u +%Y%m%d-%H%M%S).dump

.PHONY: restore
restore: ## Restore the database from f=<backups/file.dump>
	@test -n "$(f)" || { echo "Usage: make restore f=backups/<file>.dump"; exit 1; }
	$(DOCKER_COMPOSE_EXEC) -T postgres sh -c 'pg_restore --clean --if-exists -U $$POSTGRES_USER -d $$POSTGRES_DB' < $(f)

.PHONY: psql
psql: ## Open psql inside the postgres container
	$(DOCKER_COMPOSE_EXEC) postgres sh -c 'psql -U $$POSTGRES_USER $$POSTGRES_DB'

.PHONY: redis-cli
redis-cli: ## Open redis-cli inside the redis container
	$(DOCKER_COMPOSE_EXEC) redis redis-cli

.PHONY: create-admin
create-admin: ## Bootstrap the first admin (env: ADMIN_EMAIL, ADMIN_PASSWORD)
# docker compose exec does not forward the caller's shell environment on its own; each
# -e without a value re-requests it from the host process running this recipe.
	$(DOCKER_COMPOSE_EXEC) -e ADMIN_EMAIL -e ADMIN_PASSWORD -e ADMIN_FIRST_NAME -e ADMIN_LAST_NAME -e ADMIN_USERNAME -e ADMIN_PHONE $(APP_CONTAINER) python -m scripts.app.create_admin

##@ Logs

.PHONY: logs
logs: ## Follow logs for every service, or one: make logs s=app
	$(DOCKER_COMPOSE) logs -f $(s)

##@ Quality

.PHONY: lint
lint: ## Run every pre-commit hook
	pre-commit run --all-files

.PHONY: test
test: ## Run the tests, excluding the integration suite (no Docker needed)
	TESTING=true pytest

.PHONY: test-cov
test-cov: ## Run the tests with a coverage report
	TESTING=true pytest --cov=src --cov-report=term-missing --cov-report=xml

.PHONY: test-integration
test-integration: ## Run the integration suite against a throwaway PostgreSQL container
	@set -e; \
	COMPOSE="docker compose -p template-test-$$$$ --env-file .env.test -f $(COMPOSE_TEST)"; \
	export POSTGRES_PORT=; \
	trap "$$COMPOSE down -v --remove-orphans >/dev/null 2>&1" EXIT INT TERM; \
	$$COMPOSE up -d --wait --wait-timeout 120; \
	PG_PORT="$$($$COMPOSE port postgres 5432 | sed 's/.*://')"; \
	TESTING=true \
	POSTGRES_HOST=127.0.0.1 \
	POSTGRES_PORT="$$PG_PORT" \
	pytest tests/integration -m integration -v

.PHONY: test-all
test-all: test test-integration ## Run the unit suite and then the integration suite

.PHONY: count-code-lines
count-code-lines: SHELL := /bin/bash
count-code-lines: ## Count lines in tracked Python files, per top-level directory
	@set -o pipefail; git ls-files -z -- '*.py' | python3 scripts/ops/count_code_lines.py

##@ Dependencies

.PHONY: req-compile
req-compile: ## Recompile the lockfiles inside a Linux container
	docker run --rm --platform=$(REQ_COMPILE_PLATFORM) \
		-e HOME=/tmp \
		-u $(REQ_COMPILE_USER) \
		-v $(CURDIR):/app \
		-w /app \
		$(REQ_COMPILE_IMAGE) \
		sh -lc 'set -e; python -m pip install --user --no-cache-dir --upgrade pip pip-tools && python scripts/ops/sort_requirements_in.py $(addprefix $(REQ_DIR)/,$(addsuffix .in,$(REQ_NAMES))) && cd $(REQ_DIR) && for name in $(REQ_NAMES); do python -m piptools compile $(REQ_COMPILE_FLAGS) "$${name}.in" -o "$${name}.txt"; done'

.PHONY: req-upgrade
req-upgrade: ## Recompile the lockfiles, bumping every pin to its newest allowed release
	$(MAKE) req-compile REQ_COMPILE_FLAGS=--upgrade

.PHONY: req-sync-dev
req-sync-dev: ## Install the dev lockfile into the active environment
	python -m piptools sync $(REQ_DEV_TXT)

.PHONY: req-sync-prod
req-sync-prod: ## Install the prod lockfile into the active environment
	python -m piptools sync $(REQ_PROD_TXT)

##@ Server

.PHONY: ansible-deps
ansible-deps: ## Create the Ansible virtualenv and install the pinned collections
	python3 -m venv $(ANSIBLE_VENV)
	$(ANSIBLE_BIN)/python -m pip install --quiet --upgrade pip
	$(ANSIBLE_BIN)/python -m pip install --quiet -r $(REQ_DIR)/ansible.txt
	cd $(ANSIBLE_DIR) && .venv/bin/ansible-galaxy collection install -r requirements.yml

.PHONY: ansible-lint
ansible-lint: ## Lint the playbooks and roles (profile: production)
	cd $(ANSIBLE_DIR) && PATH="$$PWD/.venv/bin:$$PATH" ansible-lint

.PHONY: server-provision
server-provision: ## Converge a box: make server-provision ENV=production [CHECK=1]
	@test -n "$(ENV)" || { echo "ENV is required: make server-provision ENV=production"; exit 1; }
	@test -d $(ANSIBLE_DIR)/inventory/$(ENV) || { echo "No inventory at $(ANSIBLE_DIR)/inventory/$(ENV)"; exit 1; }
	$(ANSIBLE_PLAYBOOK) $(ANSIBLE_DIR)/playbooks/site.yml $(if $(CHECK),--check --diff)

##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
