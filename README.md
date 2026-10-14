# Onramp

Onramp is an operations platform for task marketplaces. Support specialists use it to
investigate and resolve worker onboarding, quality-review and payout cases.

Deterministic code owns case state, identity, authorization and every account or financial
change. A bounded model investigates only the cases that checks cannot settle, and proposes a
cited resolution. Consequential actions go through a guarded gateway and, by risk, a human
approver.

## Status

Phases 0 through 15 are complete, and the Phase 16 tooling for evaluating real models is built.
What works today:

* tenants, users and roles, with bearer-token authentication whose role is always read from
  the database
* a simulated marketplace in its own database, with tenant-scoped reads and idempotent writes
* case intake that derives the worker and incident time from marketplace records, attaches
  repeat reports to an open case, and is safe under concurrent submissions
* a checked case lifecycle with an append-only audit timeline
* operator proposals, agent-proposal acceptance, escalation, and human approval bound to the
  exact proposal content
* transactional outbox events, Kafka publishing and consumption, deduplication, DLQ replay,
  and marketplace event relaying
* effective-dated policies in PostgreSQL, Elasticsearch retrieval, and a reproducible retrieval
  benchmark that selected dense search
* deterministic checks for payout, onboarding, and quality-review cases
* a bounded LangGraph investigator with case-bound read-only tools, deterministic conclusion
  validation, stored trajectories, and a fake-client-tested Anthropic adapter
* guarded action execution that rechecks identity, policy, evidence, approval, payout amount,
  and authorization before an idempotent marketplace write and verified postcondition
* one durable Temporal workflow per case, with retry-safe activities, settlement and evidence
  timers, duplicate-safe direct and Kafka fallback signals, and missed-start reconciliation
* independently scalable workflow and agent workers; model credentials exist only in the
  opt-in agent worker role
* whole-application case evaluation: 21 versioned scenarios in isolated synthetic worlds, run
  through the real services with scripted investigations, graded deterministically against
  separate truth, with safety invariants checked against the marketplace's own write log
* adversarial evaluation: 15 attacks (prompt injection, namesakes, another tenant's records,
  stale policy, malformed and catalogue-violating conclusions, approval and replay attacks),
  each run against a scripted investigator or reviewer that goes along with it
* fault-injection evaluation: 15 cases recovered through lost and timed-out marketplace
  responses, model and search outages, crashes after commit, and duplicate or late events
* live-model evaluation tooling: an 82-case judgment suite, versioned prompts, a required
  spending cap, recorded answers replayed for free, half-price batch runs, and DuckDB run
  comparison with a regression gate (no live result published yet)

Not built yet: model and prompt regression with
real models, the real operator console, full observability, Kubernetes packaging, final scale
benchmarks, and AWS staging. No live model call has been made; the published case-evaluation
run uses scripted investigations and measures the application, not a model.

## Documents

* [Product specification](docs/PRODUCT_SPEC.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Architecture decision records](docs/adr/README.md)
* [Case evaluation](docs/EVALUATION.md) and [benchmarks](docs/BENCHMARKS.md)
* [Implementation drift and follow-ups](docs/IMPLEMENTATION_DRIFT.md)

## Running locally

Requirements: Python 3.13, Node 22, Docker.

```sh
cp .env.example .env  # then set AUTH_JWT_SECRET
make install          # backend virtualenv and dev dependencies
make up               # PostgreSQL with onramp and marketplace databases
make migrate          # apply both migration chains
make seed             # demo tenants, users, marketplace history and open cases
make check            # lint, import boundaries, type check, unit tests
make test-integration # tests against PostgreSQL
make case-eval        # whole-application case evaluation, artifacts in artifacts/case-eval/
make adversarial-eval # the same runner against attacks
make fault-eval       # the same runner with injected faults
```

Call the API as a seeded user:

```sh
docker compose --profile app up -d --wait api
TOKEN=$(backend/.venv/bin/onramp-dev-token acme acme_operator)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/cases
```

OpenAPI docs are at `localhost:8000/docs`.

Frontend:

```sh
cd frontend && npm ci && npm run dev
```
