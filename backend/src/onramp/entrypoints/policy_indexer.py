"""Rebuild the policy passage index, with embeddings (ADR-011): ``onramp-policy-indexer``.

Runs as a one-off job after policies are loaded. Safe to run at any time.
"""

import sys
from datetime import UTC, datetime

from elasticsearch import Elasticsearch

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.policy.embeddings import FastEmbedder
from onramp.policy.search import rebuild_index


def main() -> None:
    settings = get_settings()
    if not settings.elasticsearch_url:
        sys.exit("ELASTICSEARCH_URL is not set")
    client = Elasticsearch(settings.elasticsearch_url)
    sessions = make_session_factory(make_engine(str(settings.database_url)))
    count = rebuild_index(
        client, sessions, settings.policy_index_alias, datetime.now(UTC), FastEmbedder()
    )
    print(f"indexed {count} passages behind alias {settings.policy_index_alias}")


if __name__ == "__main__":
    main()
