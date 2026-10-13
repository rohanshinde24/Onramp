from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from onramp.policy.applicability import applicable_version
from onramp.policy.corpus import load_corpus, parse
from onramp.policy.loader import ImmutableVersionError, load_documents
from onramp.policy.models import PolicyPassage

CORPUS = Path(__file__).resolve().parents[3] / "policies"


def test_corpus_loads_once_and_reloads_as_unchanged(onramp_session: Session) -> None:
    first = load_documents(onramp_session, load_corpus(CORPUS))
    second = load_documents(onramp_session, load_corpus(CORPUS))

    assert len(first.loaded) == 6
    assert second.loaded == []
    assert len(second.unchanged) == 6
    passages = onramp_session.scalar(select(func.count()).select_from(PolicyPassage))
    assert passages == sum(len(d.passages) for d in load_corpus(CORPUS))


def test_changed_source_for_a_loaded_version_is_refused(
    onramp_session: Session, tmp_path: Path
) -> None:
    load_documents(onramp_session, load_corpus(CORPUS))
    edited_dir = tmp_path / "payout"
    edited_dir.mkdir()
    edited = edited_dir / "v12.md"
    edited.write_text(
        (CORPUS / "payout" / "v12.md").read_text().replace("7 calendar", "3 calendar")
    )

    with pytest.raises(ImmutableVersionError, match="payout v12 changed"):
        load_documents(onramp_session, [parse(edited)])


@pytest.mark.parametrize(
    ("incident", "version", "window"),
    [(datetime(2026, 6, 28, tzinfo=UTC), 11, 5), (datetime(2026, 7, 3, tzinfo=UTC), 12, 7)],
)
def test_parameters_come_from_the_version_in_force(
    onramp_session: Session, incident: datetime, version: int, window: int
) -> None:
    load_documents(onramp_session, load_corpus(CORPUS))

    applied = applicable_version(onramp_session, "payout", incident)

    assert applied.version == version
    assert applied.parameters["settlement_window_days"]["value"] == window
    assert applied.parameters["settlement_window_days"]["passage_id"] == (
        f"payout_v{version}_section_4_2"
    )
