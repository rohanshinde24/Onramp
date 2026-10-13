BACKEND := backend
PY := $(BACKEND)/.venv/bin

.PHONY: install lint format typecheck test test-integration check up down api-image

install:
	python3.13 -m venv $(BACKEND)/.venv
	$(PY)/pip install -e '$(BACKEND)[dev]'

lint:
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	cd $(BACKEND) && .venv/bin/ruff format . && .venv/bin/ruff check --fix .

typecheck:
	cd $(BACKEND) && .venv/bin/mypy

test:
	cd $(BACKEND) && .venv/bin/pytest -m "not integration"

test-integration:
	cd $(BACKEND) && .venv/bin/pytest -m integration

check: lint typecheck test

up:
	docker compose up -d --wait

down:
	docker compose down

api-image:
	docker build -t onramp:dev $(BACKEND)
