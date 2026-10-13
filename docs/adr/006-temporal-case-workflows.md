# ADR-006: Temporal for per-case durable workflows

## Context

A case can run for seconds or for days. It may wait for a worker to upload a document, for an
approval, or for a payout settlement window to close, and it must give up and escalate when a
wait times out. Steps need retries with backoff, crashes must not lose progress, and duplicate
or stale signals must be harmless.

## Decision

Each case has one Temporal workflow with ID `case-{case_id}`.

* The workflow is the orchestrator: it decides which step runs next, holds timers, and waits
  for signals (`evidence_arrived`, `approval_decided`, `operator_escalated`).
* It holds **no authoritative state**. Each step is an activity that reads and transitions the
  case through the application's case service, which enforces the state machine in Postgres.
* Signals carry IDs only. The workflow records applied signal IDs and ignores repeats; the
  activity re-reads the database, so a stale signal is re-evaluated against current state.
* Investigation runs on a separate task queue served by `onramp-agent-worker`, so model latency
  and rate limits do not block deterministic steps.
* Workflow ID uniqueness prevents duplicate executions for one case. A reconciler starts
  workflows for cases committed without one.
* Workflow code changes use Temporal's versioning APIs once any workflow could be in flight
  across a deploy.

## Alternatives

* **Database-driven scheduler.** A `next_wake_at` column, a poller, and the case state machine.
  Simpler to run and enough for single waits. It becomes hand-built Temporal once a case waits
  on "evidence OR timeout OR escalation" with per-step retry policies and needs visibility into
  why it is stuck. That is the normal case here, not an edge case.
* **Kafka as the workflow engine.** Choreography across topics makes the lifecycle of one case
  implicit and hard to debug. Kafka stays the event log.
* **LangGraph checkpoints as durability.** Ties case durability to the agent framework and
  would make checkpoints a second source of truth.

## Tradeoffs

Temporal is another stateful dependency, and workflow code must be deterministic, which adds
discipline around time, randomness and I/O. Tests use the Temporal Python SDK's time-skipping
test environment so timeouts of days run in milliseconds.

## Consequences

* The case state machine stays in Postgres and is tested without Temporal.
* Every activity is idempotent, because Temporal retries it.
