# ADR-010: Process roles now, Kubernetes at Phase 19

## Context

Onramp will run several kinds of work with different failure and scaling behavior: request
serving, outbox publication, event consumption, deterministic workflow steps, model-bound
investigation, and policy indexing. Kubernetes is in scope, but YAML for processes that do not
exist yet proves nothing.

## Decision

* One codebase and one container image, with an entrypoint per process role:
  `onramp-api`, `onramp-outbox-publisher`, `onramp-event-consumer`,
  `onramp-workflow-worker`, `onramp-agent-worker`, `onramp-policy-indexer` (a Job), and later
  `onramp-eval-worker` if evaluation needs to scale out.
* Every role handles SIGTERM from its first commit: stop taking new work, finish or release
  in-flight work, exit within a deadline. Every long-running role exposes health and
  readiness.
* Until Phase 19, roles run under Docker Compose.
* Kubernetes (kind locally) is introduced at Phase 19, when all roles exist. Helm versus
  Kustomize is decided then in its own ADR. Stateful dependencies are not run in the cluster
  just to show them there.

## Alternatives

* **Microservices with separate repositories and databases.** No team or scaling boundary
  justifies it. The marketplace simulator is the only separate database, for a reason unrelated
  to deployment (ADR-002).
* **ECS or a single VM for staging.** Possibly simpler. The Phase 19 ADR has to show what
  Kubernetes provides here (independent scaling of agent workers and consumers on their own
  signals, rolling updates with drain, probe-driven recovery) before EKS is chosen.

## Consequences

* Graceful shutdown is tested per role before Kubernetes exists.
* Process roles are not network services; they share code and talk through Postgres, Kafka and
  Temporal.
