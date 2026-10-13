# Implementation Drift and Follow-ups

This log records material differences between the original phased implementation plan and the
system that was actually built. It is not a defect list. Some changes closed correctness gaps;
others are explicit local-development compromises or deferred production work.

Last reviewed: 2026-10-13 at commit `cb0013e`.

## Correctness changes discovered during implementation

### Signals must not race with workflow reads

The first workflow wait loop could lose a wake-up when a signal arrived during a database read.
The workflow then recorded the already-incremented signal generation as its baseline and waited
forever. The implementation now snapshots the wake generation before each read. The failure was
visible in CI at `84067d5` and fixed at `41639d8`.

### Consumer-driven Temporal signals must occur after commit

The original event-consumer shape made it tempting to signal Temporal from inside the database
transaction. A workflow could then wake and read state that had not committed. The consumer now
runs the signal hook only after the transaction commits. If that external signal fails, the Kafka
offset remains uncommitted; redelivery retries the hook even when the durable deduplication claim
already exists.

### Direct and fallback signals need one stable identity

Direct API signaling originally risked creating a different logical wake from the outbox/Kafka
fallback. Proposal and approval paths now pass the exact outbox event ID to both paths. Duplicate
delivery remains harmless, but both paths represent the same fact.

### Proposal decisions need durable events

The initial event inventory did not include proposal acceptance and rejection. A process crash
after a direct-only decision could strand a workflow waiting for that decision. The implementation
adds `proposal.accepted` and `proposal.rejected` outbox events as durable fallback signals.

### Evidence fallback must identify the exact case

Resolving the currently open case again after commit could target a newer case for the same
subject. Evidence fallback now uses the committed audit row's causation ID to recover the exact
case that changed.

### Overturned quality reviews remain payout eligible

Action Gateway review exposed an edge case where an overturned review could incorrectly suppress
payout eligibility. The deterministic payout computation was corrected in Phase 11 so overturned
reviews count correctly.

## Architecture and dependency adaptations

### Temporal SDK version

The project uses `temporalio` 1.32.x. Versions 1.33 and 1.34 considered during Phase 12 were yanked
at the time of implementation, so the dependency was pinned to the current stable line instead of
the initially expected newest version.

### Local Temporal topology

The formerly common Docker Compose auto-setup repository and image path was archived or
deprecated. Local development therefore uses the Temporal CLI development server with persisted
SQLite. This is intentionally not the production topology. Production packaging remains Phase 19
work.

### Synchronous application with an asynchronous Temporal client

The existing FastAPI, SQLAlchemy, and Kafka paths are synchronous. Converting them to asynchronous
I/O during workflow work would have been unrelated architectural churn. A synchronous coordinator
now owns a dedicated asyncio loop thread for Temporal client calls.

### Separate workflow and agent queues

The broad worker-role plan became a workflow queue plus an isolated agent queue. The agent worker
is an opt-in Compose profile because it is the only role that needs model credentials. No live
model call was made. The shared image still includes embedding and model dependencies for all
roles and is about 785 MB; role-specific images remain Phase 19 work.

### Dense retrieval was selected by evidence

The architecture allowed multiple retrieval strategies. The Phase 8 benchmark selected dense
retrieval rather than hybrid retrieval, and the agent worker uses that selected retriever.

## Explicit assumptions and deferred production work

### Evidence wait timeout

The product and ADRs required a durable evidence timeout but did not specify its duration. The
workflow uses seven days as a named, tested constant. This is a provisional product assumption
and can later become policy-driven or configurable if operating requirements differ.

### Direct Temporal client reconnection

The API attempts its direct Temporal connection once at startup. If Temporal is unavailable, the
API remains usable and the durable Kafka fallback still works, but the low-latency direct client
does not reconnect until the process restarts.

### Workflow reconciliation scope

The reconciler runs when the workflow worker starts and scans all nonterminal cases. It is not yet
paginated and does not run periodically. This is acceptable for the current synthetic scale and
must be measured before adding machinery.

### Retry behavior for unknown marketplace outcomes

`OutcomeUnknownError` remains retryable by Temporal instead of being converted into a terminal
business failure. The Action Gateway reuses the same idempotency key and verifies the
postcondition, which is the required safety behavior.

### Shared integration databases

The integration and Kafka suites recreate shared test databases. Running them concurrently causes
interference, so repository gates run them sequentially. Temporal time-skipping tests also start a
local test server and can require host process/network permission in restricted environments.

## Evaluation sequencing changes

Phase 13 started with schemas and graders before the whole-application runner. This lets tests
demonstrate that deliberately bad outputs fail before the evaluator is trusted to score a system
run. Truth is loaded from a separate artifact and is excluded from investigator inputs; it is
hidden from the system under evaluation, not secret from repository readers.

At the current pause point, scenario/truth schemas, deterministic graders, bad-output tests, and a
stored-record observation adapter exist. The committed scenario dataset, isolated synthetic-world
fixtures, whole-application runner, artifact manifest/JSONL output, and `make case-eval` command do
not exist yet. No case-evaluation benchmark claim has been made.

## Next implementation steps

1. Add a small versioned `eval/cases/scenarios.yaml` and separate truth file grounded in the
   deterministic synthetic marketplace world.
2. Build isolated world fixtures so each scenario has reproducible state without leaking state to
   another scenario.
3. Run scenarios through the real case, check, investigation, proposal, approval, and Action
   Gateway services, using scripted models only.
4. Write a manifest, per-case JSONL records, and a summary with commit, dataset version, run ID,
   dirty-tree state, and operational metrics.
5. Add and test a one-command `make case-eval` entrypoint.
6. Prove a known-good run passes and seeded bad variants fail before expanding the dataset.
7. Continue to avoid live model credentials or calls without explicit user approval.
