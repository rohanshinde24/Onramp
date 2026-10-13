BACKEND := backend
PY := $(BACKEND)/.venv/bin

.PHONY: install lint format typecheck test test-integration test-kafka test-search check up down api-image migrate seed dlq-replay load-policies retrieval-eval

install:
	python3.13 -m venv $(BACKEND)/.venv
	$(PY)/pip install -e '$(BACKEND)[dev,embeddings]'

lint:
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/lint-imports

format:
	cd $(BACKEND) && .venv/bin/ruff format . && .venv/bin/ruff check --fix .

typecheck:
	cd $(BACKEND) && .venv/bin/mypy

test:
	cd $(BACKEND) && .venv/bin/pytest -m "not integration and not kafka and not search"

test-integration:
	cd $(BACKEND) && .venv/bin/pytest -m "integration and not kafka and not search"

test-search:
	cd $(BACKEND) && .venv/bin/pytest -m search

test-kafka:
	cd $(BACKEND) && .venv/bin/pytest -m kafka

check: lint typecheck test

up:
	docker compose --profile events --profile search up -d --wait
	$(PY)/onramp-kafka-topics

down:
	docker compose down

api-image:
	docker build -t onramp:dev $(BACKEND)

migrate:
	$(PY)/onramp-migrate

seed:
	$(PY)/onramp-seed

# make dlq-replay TOPIC=marketplace.workers GROUP=onramp-case-evidence
dlq-replay:
	$(PY)/onramp-dlq-replay $(TOPIC) $(GROUP)

load-policies:
	$(PY)/onramp-load-policies

retrieval-eval:
	$(PY)/onramp-retrieval-eval
