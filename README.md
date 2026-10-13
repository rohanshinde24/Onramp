# Onramp

Onramp is an operations platform for task marketplaces. Support specialists use it to
investigate and resolve worker onboarding, quality-review and payout cases.

Deterministic code owns case state, identity, authorization and every account or financial
change. A bounded model investigates only the cases that checks cannot settle, and proposes a
cited resolution. Consequential actions go through a guarded gateway and, by risk, a human
approver.

## Status

Early. What works today, without any model:

* tenants, users and roles, with bearer-token authentication whose role is always read from
  the database
* a simulated marketplace in its own database, with tenant-scoped reads and idempotent writes
* case intake that derives the worker and incident time from marketplace records, attaches
  repeat reports to an open case, and is safe under concurrent submissions
* a checked case lifecycle with an append-only audit timeline
* operator proposals, agent-proposal acceptance, escalation, and human approval bound to the
  exact proposal content

Not built yet: action execution, the event backbone, policy retrieval, deterministic checks,
the agent, durable workflows and evaluation. This README only describes what exists.

## Documents

* [Product specification](docs/PRODUCT_SPEC.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Architecture decision records](docs/adr/README.md)

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
