BACKEND := backend
PY := $(BACKEND)/.venv/bin

.PHONY: install lint format typecheck test test-integration test-kafka test-search check up down api-image migrate seed dlq-replay load-policies retrieval-eval case-eval adversarial-eval fault-eval compare baseline judgment-eval judgment-dataset promptfoo-view e2e e2e-up e2e-down observe observe-down

install:
	python3.13 -m venv $(BACKEND)/.venv
	$(PY)/pip install -e '$(BACKEND)[dev,embeddings,anthropic,eval]'

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

# Whole-application case evaluation with scripted investigations. Needs PostgreSQL only.
case-eval:
	$(PY)/onramp-case-eval

# The same runner over attacks, with scripted investigators that fall for them.
adversarial-eval:
	$(PY)/onramp-case-eval --suite adversarial-eval --scenarios eval/adversarial/scenarios.yaml \
		--truth eval/adversarial/truth.yaml --scripts eval/adversarial/investigations.yaml \
		--reviewers eval/adversarial/reviewers.yaml

# The same runner with injected faults: lost and timed-out writes, model outages, crashes after
# commit, duplicate and late events.
fault-eval:
	$(PY)/onramp-case-eval --suite fault-eval --scenarios eval/faults/scenarios.yaml \
		--truth eval/faults/truth.yaml --scripts eval/faults/investigations.yaml \
		--faults eval/faults/faults.yaml

# Compare two evaluation runs, or a run against a stored baseline; fails on a regression.
# make compare BASE=eval/baselines/case-eval/<name> CANDIDATE=artifacts/case-eval/<run_id>
compare:
	$(PY)/onramp-case-compare $(BASE) $(CANDIDATE) $(ARGS)

# Store a run as a baseline that later runs are compared against.
# make baseline RUN=artifacts/case-eval/<run_id> NAME=<name>
baseline:
	$(PY)/onramp-case-compare --store-baseline $(RUN) $(NAME)

# The judgment suite needs a live model and always a spending cap; every case reaches the
# investigator, so accuracy below 1.0 is expected and does not fail the run.
# make judgment-eval MODEL=claude-haiku-5-5 MAX_COST=1.00 [EFFORT=medium] [PROMPT=investigator-v1] [BATCH=--batch]
judgment-eval:
	$(PY)/onramp-case-eval --suite judgment-eval --scenarios eval/judgment/scenarios.yaml \
		--truth eval/judgment/truth.yaml --model $(MODEL) --max-cost $(MAX_COST) \
		--effort $(or $(EFFORT),medium) --prompt $(or $(PROMPT),investigator-v1) \
		--min-outcome-accuracy 0 $(BATCH)

# Regenerate the judgment suite's YAML from its label rules.
judgment-dataset:
	$(PY)/python -c "from pathlib import Path; from onramp.evaluation.judgment import render; \
	s, t = render(); Path('eval/judgment/scenarios.yaml').write_text(s); \
	Path('eval/judgment/truth.yaml').write_text(t)"

# A Promptfoo model-by-prompt view of recorded runs. It replays stored outcomes and makes no
# model call. Promptfoo needs Node 22: the version in .nvmrc, taken from nvm when installed.
# make promptfoo-view RUNS="artifacts/judgment-eval/<run_id> ..."
NODE_BIN := $(shell . "$$HOME/.nvm/nvm.sh" >/dev/null 2>&1 && nvm which $$(cat .nvmrc) 2>/dev/null | xargs dirname 2>/dev/null)
promptfoo-view:
	$(PY)/onramp-promptfoo-export $(RUNS)
	PATH="$(NODE_BIN):$$PATH" PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 \
		npx -y promptfoo@0.123.1 eval -c artifacts/promptfoo/promptfooconfig.yaml --no-cache \
		-o artifacts/promptfoo/view.json \
		|| test $$? -eq 100  # 100: some cases were answered wrong, which the view shows

# Browser end-to-end test of the console against its own stack: an API, workflow worker and
# in-memory Temporal on fresh onramp_e2e and marketplace_e2e databases, seeded each run. The dev
# databases and dev Temporal are never touched. The stack stays up afterwards for debugging;
# `make e2e-down` removes it. Needs Docker and the Playwright browser (`npx playwright install
# chromium` in frontend/).
E2E_COMPOSE := docker compose --profile e2e
E2E_SERVICES := e2e-setup temporal-e2e api-e2e workflow-worker-e2e
e2e-up:
	docker compose up -d --wait postgres
	$(E2E_COMPOSE) rm -sf $(E2E_SERVICES)
	for db in onramp_e2e marketplace_e2e; do \
		docker compose exec -T postgres psql -U onramp -d postgres -q -v ON_ERROR_STOP=1 \
			-c "DROP DATABASE IF EXISTS $$db WITH (FORCE)" -c "CREATE DATABASE $$db" || exit 1; \
	done
	$(E2E_COMPOSE) up -d --build --wait temporal-e2e api-e2e workflow-worker-e2e

e2e: e2e-up
	cd frontend && PATH="$(NODE_BIN):$$PATH" npm run test:e2e

e2e-down:
	$(E2E_COMPOSE) rm -sf $(E2E_SERVICES)

# The whole application with telemetry: traces, metrics and logs from every container go to one
# collector, and Grafana at http://localhost:3300 has the Onramp dashboard and Tempo and Loki.
# Host processes export there too with OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318.
# The role containers are recreated explicitly: Compose builds a separate image per service and
# did not always replace a running container after a rebuild. Kafka is left alone; it has no
# volume, so recreating it would drop messages not yet consumed.
OBSERVE_COMPOSE := CONTAINER_OTLP_ENDPOINT=http://observability:4318 docker compose \
	--profile observability --profile app
ONRAMP_ROLES := api workflow-worker outbox-publisher event-consumer case-signal-consumer \
	marketplace-relay
observe:
	$(OBSERVE_COMPOSE) up -d --build --wait
	$(OBSERVE_COMPOSE) up -d --force-recreate --no-deps --wait $(ONRAMP_ROLES)
	$(PY)/onramp-kafka-topics

observe-down:
	docker compose --profile observability --profile app stop
