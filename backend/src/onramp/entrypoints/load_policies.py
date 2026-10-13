"""Load the policy corpus into PostgreSQL: ``onramp-load-policies [corpus_dir]``."""

import sys
from pathlib import Path

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory, transaction
from onramp.policy.corpus import CorpusError, load_corpus
from onramp.policy.loader import ImmutableVersionError, load_documents

DEFAULT_CORPUS = Path(__file__).resolve().parents[4] / "policies"


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS
    settings = get_settings()
    sessions = make_session_factory(make_engine(str(settings.database_url)))
    try:
        with transaction(sessions) as session:
            result = load_documents(session, load_corpus(root))
    except (CorpusError, ImmutableVersionError) as exc:
        sys.exit(f"policy load failed: {exc}")
    print(f"loaded: {', '.join(result.loaded) or 'none'}")
    print(f"unchanged: {', '.join(result.unchanged) or 'none'}")
    print(f"closed: {', '.join(result.closed) or 'none'}")


if __name__ == "__main__":
    main()
