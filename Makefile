SHELL := /bin/sh

COMPOSE ?= docker compose
DEV_COMPOSE ?= $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml
SERVICE ?= api
CLI ?= python -m ots.cli

RUN_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
CMD_ARGS = $(if $(ARGS),$(ARGS),$(RUN_ARGS))

.PHONY: help build up down restart logs logs-api logs-worker shell migrate migrate-down db-current worker run lint format format-check pre-commit install-pre-commit dev-up dev-down dev-restart dev-logs dev-logs-api dev-logs-worker dev-shell dev-run

help:
	@printf '%s\n' \
		'Open Terminology Server' \
		'' \
		'Core:' \
		'  make build                         Build Docker image' \
		'  make up                            Start postgres, api, worker' \
		'  make down                          Stop the stack' \
		'  make restart                       Restart the stack' \
		'  make logs                          Follow all logs' \
		'  make logs-api                      Follow API logs' \
		'  make logs-worker                   Follow worker logs' \
		'  make shell                         Open a shell in the API container' \
		'' \
		'Database:' \
		'  make migrate                       alembic upgrade head' \
		'  make migrate-down                  alembic downgrade -1' \
		'  make db-current                    alembic current' \
		'' \
		'CLI:' \
		'  make run                           Show OTS CLI help' \
		'  make run snomed load-packages      Run OTS CLI command' \
		'  make run ARGS="snomed load -- --help"' \
		'' \
		'Quality:' \
		'  make lint                          Run Ruff lint checks' \
		'  make format                        Format Python files with Ruff' \
		'  make format-check                  Check Ruff formatting' \
		'  make pre-commit                    Run all pre-commit hooks' \
		'  make install-pre-commit            Install git pre-commit hook' \
		'' \
		'Dev:' \
		'  make dev-up                        Start stack with source bind mount' \
		'  make dev-down                      Stop dev stack' \
		'  make dev-logs-api                  Follow dev API logs' \
		'  make dev-run ARGS="common --help"  Run OTS CLI in dev container' \
		'' \
		'Variables:' \
		'  SERVICE=api|worker                 Service used by run/shell' \
		'  ARGS="..."                        Explicit args for make run'

build:
	$(COMPOSE) build api worker

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart: down up

logs:
	$(COMPOSE) logs -f

logs-api:
	$(COMPOSE) logs -f api

logs-worker:
	$(COMPOSE) logs -f worker

shell:
	$(COMPOSE) run --rm $(SERVICE) sh

migrate:
	$(COMPOSE) run --rm api alembic upgrade head

migrate-down:
	$(COMPOSE) run --rm api alembic downgrade -1

db-current:
	$(COMPOSE) run --rm api alembic current

worker:
	$(COMPOSE) up -d worker

run:
	$(COMPOSE) run --rm $(SERVICE) $(CLI) $(CMD_ARGS)

lint:
	pipenv run ruff check .

format:
	pipenv run ruff format .

format-check:
	pipenv run ruff format --check .

pre-commit:
	pipenv run pre-commit run --all-files

install-pre-commit:
	pipenv run pre-commit install

dev-up:
	$(DEV_COMPOSE) up -d --build

dev-down:
	$(DEV_COMPOSE) down

dev-restart: dev-down dev-up

dev-logs:
	$(DEV_COMPOSE) logs -f

dev-logs-api:
	$(DEV_COMPOSE) logs -f api

dev-logs-worker:
	$(DEV_COMPOSE) logs -f worker

dev-shell:
	$(DEV_COMPOSE) run --rm $(SERVICE) sh

dev-run:
	$(DEV_COMPOSE) run --rm $(SERVICE) $(CLI) $(CMD_ARGS)

%:
	@:
