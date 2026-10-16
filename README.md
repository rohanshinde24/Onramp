# Onramp

Onramp is an operations platform for task marketplaces. Support specialists use it to
investigate and resolve worker onboarding, quality-review and payout cases.

Deterministic code owns case state, identity, authorization and every account or financial
change. A bounded model investigates only the cases that checks cannot settle, and proposes a
cited resolution. Consequential actions go through a guarded gateway and, by risk, a human
approver.

## Status

Phases 0 through 17 are complete. What works today:

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
* live-model evaluation: Haiku, Sonnet and Opus 5.5 compared across three prompt versions on a
  92-case judgment suite, with a required spending cap, recorded answers replayed for free,
  optional half-price batch runs, DuckDB comparison with a regression gate against stored
  baselines, and a Promptfoo matrix view (results in `docs/BENCHMARKS.md`)
* an operator console: an inbox split by what each case needs from a person, and a case page
  with the decision, investigation trajectory, checks, the policy version in force with every
  cited passage, the marketplace evidence, approvals, gateway results and the audit timeline;
  plus a view of stored evaluation runs, and a browser end-to-end test of its decisions

Not built yet: full observability, Kubernetes packaging, final scale
benchmarks, and AWS staging. The reference, adversarial and fault suites use
scripted investigations and measure the application; the judgment suite measures real models.

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

Operator console (Node 22 from `.nvmrc`; the API on port 8000, and Temporal plus the workflow
worker if cases should move on their own):

```sh
docker compose --profile workflow up -d --build --wait   # Temporal and the workflow worker
docker compose --profile app up -d --build --wait api
cd frontend && nvm use && npm ci && npm run dev
```

Open `localhost:3000` and sign in with a token from
`backend/.venv/bin/onramp-dev-token acme acme_operator` (or `acme_lead` to approve payouts).
Set `ONRAMP_API_URL` if the API is elsewhere. `npm test`, `npm run lint`, `npm run typecheck`
and `npm run build` are the console's checks.

Browser end-to-end test of the console (Playwright, Chromium):

```sh
cd frontend && nvm use && npm ci && npx playwright install chromium && cd ..
make e2e        # fresh e2e stack, then npm run test:e2e
make e2e-down   # remove the e2e stack
```

`make e2e` drops and recreates the `onramp_e2e` and `marketplace_e2e` databases on the Compose
PostgreSQL, then starts the `e2e` Compose profile: a one-off container that migrates, loads the
policy corpus and seeds them, an in-memory Temporal server, and an API (port 8100) and workflow
worker over those databases. The dev databases, the dev Temporal server and a running dev
console are never touched, so it can run next to `npm run dev`. Playwright builds the console
and serves it on port 3100 against that API, and signs in with tokens from
`onramp-dev-token`. The tests cover an operator accepting a payout proposal, the operator
being refused the high-risk approval in the console and by the API, a lead approving it and
the workflow and Action Gateway resolving the case; a manual proposal from a stale page refused
with the "case changed" message and no proposal written; and another tenant's case rendering
as not found. Each run needs the fresh world `make e2e` seeds; `npm run test:e2e` alone reruns
the tests against the stack as it is, and fails on cases a previous run already decided.
