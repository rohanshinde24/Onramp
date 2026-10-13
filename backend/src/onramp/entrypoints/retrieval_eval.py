"""Run the retrieval benchmark: ``onramp-retrieval-eval``.

Loads the policy corpus, builds a dedicated index, evaluates each retrieval method on
``eval/retrieval/queries.yaml``, and writes ``artifacts/retrieval/<run_id>/`` with a manifest,
per-query JSONL and a summary.
"""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from elasticsearch import Elasticsearch

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory, transaction
from onramp.evaluation.retrieval import (
    RandomInForceRetriever,
    evaluate,
    load_queries,
    summarize,
    to_record,
    validate_labels,
)
from onramp.policy.corpus import load_corpus
from onramp.policy.embeddings import Embedder, FastEmbedder
from onramp.policy.loader import load_documents
from onramp.policy.search import (
    DensePolicyRetriever,
    ElasticsearchPolicyRetriever,
    HybridPolicyRetriever,
    PolicyRetriever,
    rebuild_index,
)

ROOT = Path(__file__).resolve().parents[4]
QUERIES = ROOT / "eval" / "retrieval" / "queries.yaml"
ALIAS = "retrieval-eval"


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        return ""
    result = subprocess.run(  # noqa: S603 - resolved git binary with fixed arguments
        [git, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def _embedder() -> Embedder | None:
    try:
        return FastEmbedder()
    except ImportError:
        print("embeddings extra not installed: evaluating BM25 only")
        return None


def main() -> None:
    settings = get_settings()
    if not settings.elasticsearch_url:
        sys.exit("ELASTICSEARCH_URL is not set")
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "artifacts" / "retrieval" / run_id
    out.mkdir(parents=True)

    sessions = make_session_factory(make_engine(str(settings.database_url)))
    client = Elasticsearch(settings.elasticsearch_url)
    with transaction(sessions) as session:
        load_documents(session, load_corpus(ROOT / "policies"))
    embedder = _embedder()
    rebuild_index(client, sessions, ALIAS, started, embedder)
    query_set = load_queries(QUERIES)

    bm25 = ElasticsearchPolicyRetriever(client, ALIAS)
    methods: dict[str, PolicyRetriever] = {
        "bm25": bm25,
        "bm25_without_version_filter": ElasticsearchPolicyRetriever(
            client, ALIAS, version_filter=False
        ),
    }
    if embedder is not None:
        dense = DensePolicyRetriever(client, ALIAS, embedder)
        methods["dense"] = dense
        methods["hybrid_rrf"] = HybridPolicyRetriever(bm25, dense)
    summaries = {}
    with sessions() as session, (out / "results.jsonl").open("w") as results_file:
        validate_labels(session, query_set)
        methods = {"random_in_force": RandomInForceRetriever(session), **methods}
        for name, retriever in methods.items():
            results = evaluate(session, query_set, name, retriever)
            for result in results:
                results_file.write(json.dumps(to_record(result)) + "\n")
            summaries[name] = summarize(results)

    commit, dirty = _git("rev-parse", "HEAD"), bool(_git("status", "--porcelain"))
    manifest = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "query_set_version": query_set.version,
        "queries": len(query_set.queries),
        "methods": list(methods),
        "embedding_model": "BAAI/bge-small-en-v1.5" if embedder else None,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "summary.json").write_text(json.dumps(summaries, indent=2))

    columns = ["recall@1", "recall@3", "recall@5", "mrr@10", "top1_stale_rate"]
    print(f"run {run_id}  commit {commit[:8]}  dirty={dirty}")
    print(f"{'method':32}" + "".join(f"{c:>17}" for c in columns))
    for name, summary in summaries.items():
        print(f"{name:32}" + "".join(f"{summary[c]:>17.3f}" for c in columns))
    print(f"artifacts: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
