# ADR-011: Dense retrieval as the default policy retriever

## Context

ADR-003 chose Elasticsearch, made the effective-date filter mandatory, started with BM25, and
committed to keeping the simplest method within noise of the best after measuring BM25,
dense and hybrid retrieval.

The benchmark (`make retrieval-eval`, 41 labeled queries, run `20261013T161319Z` at commit
`3b1a85f`) measured:

| Method | Recall@1 | Recall@5 | MRR@10 |
|---|---|---|---|
| Random ranking of passages in force | 0.146 | 0.659 | 0.374 |
| BM25 | 0.561 | 0.902 | 0.742 |
| Hybrid (BM25 + dense, reciprocal rank fusion) | 0.683 | 0.988 | 0.837 |
| Dense (`BAAI/bge-small-en-v1.5`) | 0.817 | 1.000 | 0.939 |

Paired bootstrap on MRR: dense minus BM25 is +0.197 (95% CI +0.098 to +0.303); dense minus
hybrid is +0.102 (95% CI +0.030 to +0.177). BM25 is not within noise of dense.

BM25's misses were vocabulary gaps ("ID check" against "identity verification", "money never
showed up" against "missing") and generic scope sections outranking specific ones.

## Decision

Dense retrieval over passage embeddings, with the same in-force filter applied inside the
kNN search, is the default `PolicyRetriever`. The embedding model runs locally on CPU via
ONNX; no text leaves the process.

BM25 and hybrid retrievers stay in the codebase as benchmark comparisons.

## Alternatives

* **BM25 only.** Simplest, no model. Measurably worse at ranking the right passage first,
  which matters because the agent cites what it reads first.
* **Hybrid.** Better than BM25, worse than dense here: BM25's errors pull good dense results
  down in the fusion. Revisit if the corpus grows to include exact-term lookups (codes, IDs)
  where lexical matching wins.
* **Hosted embedding API.** Adds a network dependency, cost and data egress for no measured
  gain at this corpus size.

## Tradeoffs

* The indexer and the agent worker load a ~130 MB model. The API does not.
* Results depend on one small model; the benchmark records the model name in every run.
* The query set is small and author-labeled. The decision rests on a large, consistent gap,
  not on a fine margin.

## Consequences

* The image installs the `embeddings` extra and bakes the model in at build time, so no
  process downloads a model at runtime.
* `onramp-policy-indexer` always writes embeddings.
* Any change of embedding model is a benchmark run and a new ADR.
