# Onramp Architecture

Status: v1 (Phase 2). Decisions are recorded in `docs/adr/`. This document describes how the
pieces fit; the ADRs record why.

## Principles

1. **PostgreSQL owns business truth.** Case state, approvals, actions, audit and the outbox live
   in one database with constraints. Nothing else is authoritative.
2. **Kafka carries facts, not instructions.** An event says something already happened. A
   consumer that needs current state reads it from the system that owns it.
3. **Temporal decides what one case does next.** It owns waits, timers and retries for a single
   case. It does not own state; every step reads and writes through the application.
4. **The agent reasons, inside a budget.** It reads evidence and returns a typed proposal. It
   has no write tools.
5. **The Action Gateway owns authority.** It is the only code path that changes marketplace
   state.
6. **One codebase, several process roles.** A modular monolith with separate entrypoints. No
   network API between modules until a real service boundary exists.

## System context

```
                         Next.js operator console
                                   |
                              HTTPS / REST (JWT: user, tenant, roles)
                                   |
   marketplace ---intake API--->  onramp-api  ----------------------------+
   systems                         |   |   \                              |
      |                            |   |    \ start / signal              |
      |                            |   |     v                            |
      |                            |   |   Temporal  <---- onramp-workflow-worker
      |                            |   |                    |  activities: checks,
      |                            |   |                    |  case transitions, gateway
      |                            |   |                    v
      |                            |   |              onramp-agent-worker
      |                            |   |              (LangGraph, read tools only)
      |                            |   |                    |        \
      |                            |   |                    |         +--> model provider
      |                            |   +--> Elasticsearch <-+  policy passages
      |                            v                        |
      |                    onramp PostgreSQL  <-------------+  (reads via services)
      |                    cases, approvals,
      |                    actions, audit, outbox
      |                            |
      |                     onramp-outbox-publisher
      |                            v
      +---- marketplace -----> Kafka -----> onramp-event-consumer
            outbox relay                     (dedupe, map event to case, signal Temporal)

   Action Gateway (inside workflow worker and API) ---> MarketplaceClient ---> marketplace DB
                                                        (idempotency key on every write)
```

## The marketplace boundary

Onramp is an operations tool for a marketplace it does not own. The marketplace (workers,
tasks, submissions, reviews, payments, verification records) is simulated in this repository
as a separate module with **its own database**. Onramp reaches it only through
`MarketplaceClient`:

* reads return typed records with stable IDs used as evidence references,
* writes take an idempotency key and return the resulting record,
* the client can be wrapped to inject timeouts, errors and lost responses.

Because the two databases are separate, no Onramp transaction can span a marketplace write.
That is deliberate: the lost-response and duplicate-action problems in the Action Gateway are
real, not hidden by a shared transaction. See ADR-002.

The simulator also publishes marketplace events (`payment.settlement_failed`,
`evidence.uploaded`, and so on) to Kafka through its own outbox, so Onramp consumes events from a
producer it does not control.

## Module layout

```
backend/src/onramp/
  config/          typed settings from environment
  db/              engine, sessions, Alembic migrations
  identity/        tenants, users, roles, request principal
  marketplace/     simulator: models, migrations, service, client, fault wrapper
  cases/           case model, state machine, timeline, intake
  policy/          policy versions, passages, applicability, indexing, retrieval
  checks/          deterministic checks per case type
  agent/           read tools, typed state, graph, model adapter, trajectory recorder
  investigations/  runs the investigator on a case and applies its conclusion
  proposals/       proposal content, hashing, creation and decisions
  approvals/       approvals bound to exact proposal content
  actions/         Action Gateway, evidence requests, executed-action records
  audit/           append-only case timeline
  events/          envelope, contracts, outbox, publisher, consumers, dedupe
  workflows/       Temporal workflows and activities
  evaluation/      scenario worlds, case runner, graders, safety invariants, run reports
  telemetry/       OpenTelemetry setup, metrics, log context
  entrypoints/     api, outbox_publisher, event_consumer, workflow_worker,
                   agent_worker, policy_indexer, case_eval, retrieval_eval
eval/              versioned scenario, truth, script and retrieval datasets
frontend/          Next.js console
deploy/            Postgres init, observability config, Kubernetes manifests (deploy/k8s)
```

Dependency direction: `entrypoints -> workflows -> {agent, actions, checks, cases} ->
{policy, marketplace client, events} -> db`. `agent` may not import `actions` executors. An
import-linter contract in CI enforces both rules.

## Request and case flow

1. A marketplace system or an operator creates a case through the API. The API resolves the
   tenant and subject records server-side, writes the case and a `case.created` outbox row in
   one transaction, then starts the Temporal workflow `case-{case_id}`.
2. The workflow runs `INITIAL_CHECKS` as an activity. If checks are conclusive, it proposes
   the resolution directly.
3. Otherwise it runs the investigation activity on the agent task queue. The agent worker runs
   the graph with read tools and returns a typed proposal. The proposal and full trajectory are
   persisted.
4. The proposal is validated by the Action Gateway's pre-approval checks. Invalid proposals are
   recorded and the case escalates.
5. If approval is required, the workflow waits for an `approval_decided` signal. The API
   persists the approval with an `approval.granted` outbox row, then signals directly for low
   latency. The event consumer also signals from the outbox event, so a crash between commit
   and signal cannot strand the case; the duplicate is ignored.
6. The gateway executes through `MarketplaceClient` with an idempotency key derived from the
   proposal, verifies the postcondition, and writes the action record, audit event and
   `action.executed` outbox row in one transaction.
7. Waiting for evidence works the same way: the event consumer turns `evidence.uploaded` into
   an `evidence_arrived` signal carrying only the event ID; the workflow re-reads state.

## Operator console

The console in `frontend/` is a Next.js app whose server renders every page from the API and
runs every write as a server action against it. The bearer token stays in an httpOnly cookie on
the console's server; the browser never calls the API. Nothing is cached, and every write
carries the case version or proposal content hash the page showed, so a stale page is refused
rather than acted on. Controls follow role and state, but enforcement stays in the API. The
evaluation view reads run artifacts from disk, never through the API. See ADR-012.

## Data ownership

| Store | Owns | Never used for |
|---|---|---|
| onramp PostgreSQL | tenants, users, cases, evidence links, investigations, proposals, approvals, actions, audit, outbox, consumer dedupe | n/a |
| marketplace PostgreSQL | simulated marketplace records and its own outbox | Onramp state |
| Elasticsearch | a rebuildable index of policy passages | source of truth for policy text (that is Postgres) |
| Temporal | workflow execution history | business state queries |
| Kafka | ordered, replayable event logs per partition | commands, case state |
| Eval artifacts (JSONL, Parquet) and DuckDB | evaluation results | anything the application reads |

## Trust boundaries

| Boundary | Untrusted side | Enforcement |
|---|---|---|
| Browser to API | everything in the request body | JWT gives user, tenant and roles; tenant never taken from the body |
| Marketplace to intake API | payload fields | service credential scoped to a tenant; subject IDs resolved against the marketplace |
| Evidence and worker text to model | all of it | delimited as data in prompts; model has no write tools; outputs validated |
| Model to application | the whole output | schema validation, citation and evidence-ref checks, amount recomputed server-side |
| Kafka to consumers | every event | envelope validation, unsupported versions parked, dedupe by `event_id`, state re-read from source |
| Onramp to marketplace writes | n/a (Onramp is the caller) | only the Action Gateway holds a write-capable client |

Model provider credentials live only in the agent worker's environment and never enter a
prompt.

## Failure model

| Failure | Expected behavior |
|---|---|
| API crash after commit, before workflow start | Reconciler starts workflows for cases with no execution (workflow ID is deterministic, so a double start is rejected by Temporal) |
| Outbox publisher crash after Kafka ack, before marking published | Event republished; consumers dedupe on `event_id` |
| Kafka unavailable | Outbox backlog grows and is alerted on; API keeps working |
| Consumer crash mid-batch | Offsets not committed; redelivery; dedupe makes it harmless |
| Workflow worker crash | Temporal retries the activity on another worker; activities are idempotent |
| Agent timeout or malformed output | Bounded retries, then escalate with reason |
| Elasticsearch down | Investigation activity fails and retries; case escalates after the retry budget |
| Marketplace write succeeds, response lost | Retry with the same idempotency key returns the original result; postcondition check confirms |
| API crash after approval commit, before signaling | The approval transaction also wrote an `approval.granted` outbox event; the case-signal consumer delivers the signal from it |
| Event for a case that does not exist yet | Consumer records it processed and skips it; case intake reads current marketplace state, so nothing is lost |
| Gateway crash after recording an action, before the marketplace call | Activity retry finds the action by idempotency key and resumes the call with the same key |
| Duplicate approval or signal | Approval is unique per proposal; workflow ignores already-applied signal IDs |

## Observability

Every process role exports OpenTelemetry traces, metrics and logs to one collector. Trace
context is stored with each outbox row, carried in Kafka headers and restored by consumers, and
crosses Temporal through its tracing interceptor, so one trace follows a fact from the request
that caused it through the publisher, consumers, workflow, activities and gateway. Every log
line carries `tenant_id`, `case_id`, `workflow_id`, `event_id`, `correlation_id` and
`causation_id` where they exist, plus the trace ID. Metrics, the dashboard and a debugging
walkthrough are in `docs/OPERATIONS.md`.

## Deployment

Docker Compose with profiles for local work: the default profile is Postgres only; `events`,
`search`, `workflow` and `observability` profiles add Kafka, Elasticsearch, Temporal and the
telemetry stack, and `app` runs the roles too.

The roles also run on Kubernetes (kind locally, ADR-013), packaged with Kustomize under
`deploy/k8s/`: a base with every role, an opt-in agent worker component, a KEDA autoscaling
component and a kind overlay that holds the only dependency addresses and secrets. Postgres,
Kafka, Elasticsearch, Temporal and the telemetry backends stay outside the cluster. Two images
serve every role: `core`, and `ml` with the embedding model, model SDK and policy corpus for
the policy indexer and agent worker.

| Role | Image | Replicas | Probes | Scales on |
|---|---|---|---|---|
| onramp-api | core | 2, PodDisruptionBudget | `/healthz`, `/readyz` (both databases) on 8000 | n/a |
| onramp-outbox-publisher, onramp-marketplace-relay | core | 1 | loop heartbeat on 8081 | n/a |
| onramp-event-consumer, onramp-case-signal-consumer | core | 1 to 3 | loop heartbeat on 8081 | consumer group lag (KEDA) |
| onramp-workflow-worker | core | 1 | worker heartbeat on 8081 | n/a |
| onramp-agent-worker (opt-in) | ml | 0 to 3 | worker heartbeat on 8081 | agent task queue backlog (KEDA) |
| onramp-setup Job | core, ml | runs before each rollout | n/a | n/a |

See ADR-010 and ADR-013, and `docs/OPERATIONS.md` for running it.

## Decision index

| ADR | Decision |
|---|---|
| 001 | PostgreSQL is the system of record |
| 002 | Marketplace is a separate simulated system with its own database |
| 003 | Elasticsearch for effective-dated policy retrieval |
| 004 | Kafka topics, keys and ordering |
| 005 | Transactional outbox, at-least-once delivery, idempotent consumers |
| 006 | Temporal for per-case durable workflows |
| 007 | LangGraph for the investigator, without checkpointing |
| 008 | Deterministic Action Gateway |
| 009 | Evaluation artifacts: JSONL canonical, Parquet for DuckDB |
| 010 | Process roles now, Kubernetes at Phase 19 |
| 011 | Dense retrieval as the default policy retriever |
| 012 | Operator console as a server-rendered client of the API |
| 013 | Kubernetes packaging on kind with Kustomize, KEDA on lag and backlog |
