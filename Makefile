BACKEND := backend
PY := $(BACKEND)/.venv/bin

.PHONY: install lint format typecheck test test-integration test-kafka test-search check up down api-image migrate seed dlq-replay load-policies retrieval-eval case-eval adversarial-eval fault-eval compare baseline judgment-eval judgment-dataset promptfoo-view

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
