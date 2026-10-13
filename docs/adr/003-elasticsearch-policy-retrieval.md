# ADR-003: Elasticsearch for effective-dated policy retrieval

## Context

Investigations need the policy passages that applied **on the incident date**. Policies are
versioned with `effective_from` and `effective_until`. A relevant passage from the wrong version
is a wrong answer. Passage counts are small (hundreds to low thousands), but retrieval needs
lexical ranking, metadata filters by domain and date, and possibly a semantic component.

## Decision

* Policy text and versions are authoritative in PostgreSQL. Elasticsearch 8 holds a
  rebuildable passage index built by `onramp-policy-indexer`.
* Every query filters on `domain` and on `effective_from <= incident_date < effective_until`
  before ranking. Version correctness is therefore a filter, not a hope about ranking.
* The baseline ranker is BM25. Hybrid (BM25 plus dense vectors with reciprocal rank fusion) and
  dense-only are evaluated in Phase 8 on Recall@k, MRR and version correctness. The simplest
  method within noise of the best is kept, and the comparison is published.
* Retrieval sits behind a `PolicyRetriever` interface so the index can be swapped.

## Alternatives

* **Postgres full-text search plus pgvector.** Fewer moving parts and would work at this
  corpus size. Rejected narrowly: Elasticsearch gives BM25 with field boosts, analyzers and
  hybrid fusion in one place, and it is the retrieval engine I want to defend in depth. If
  Phase 8 shows Postgres FTS within noise, I will record that result.
* **OpenSearch.** API-compatible for this use. Chosen against for now because local
  Elasticsearch 8 is free and the claim history names it. Revisit at Phase 21, where AWS
  OpenSearch Service is the likely managed option.
* **A vector database.** Not needed; Elasticsearch covers dense retrieval if it earns its place.

## Tradeoffs

An extra stateful service with a JVM heap in local development. Mitigated by a Compose profile
and a 512 MB heap.

## Consequences

* The index can always be rebuilt from Postgres; it is never backed up.
* A stale-version passage in results is counted as a retrieval failure in the benchmark.
