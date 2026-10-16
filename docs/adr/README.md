# Architecture Decision Records

Each record has: context, decision, alternatives, tradeoffs, consequences. A record is
superseded by a new record, never edited to reverse its decision.

| ADR | Title | Status |
|---|---|---|
| [001](001-postgres-system-of-record.md) | PostgreSQL as system of record | Accepted |
| [002](002-marketplace-boundary.md) | Simulated marketplace behind a client boundary | Accepted |
| [003](003-elasticsearch-policy-retrieval.md) | Elasticsearch for effective-dated policy retrieval | Accepted; method chosen in 011 |
| [004](004-kafka-topics-and-ordering.md) | Kafka topics, keys and ordering | Accepted |
| [005](005-outbox-and-idempotent-consumers.md) | Transactional outbox and idempotent consumers | Accepted |
| [006](006-temporal-case-workflows.md) | Temporal for per-case durable workflows | Accepted |
| [007](007-langgraph-investigator.md) | LangGraph investigator without checkpointing | Accepted; revisited in Phase 10, kept |
| [008](008-action-gateway.md) | Deterministic Action Gateway | Accepted |
| [009](009-eval-artifacts.md) | Evaluation artifact format | Accepted |
| [010](010-process-roles-and-kubernetes.md) | Process roles now, Kubernetes at Phase 19 | Accepted; images split in 013 |
| [011](011-dense-policy-retrieval.md) | Dense retrieval as the default policy retriever | Accepted |
| [012](012-operator-console.md) | Operator console as a server-rendered client of the API | Accepted |
| [013](013-kubernetes-packaging.md) | Kubernetes packaging on kind with Kustomize | Accepted; refines the single image of 010 |
