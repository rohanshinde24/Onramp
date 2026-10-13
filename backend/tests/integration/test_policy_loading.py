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
OCT_13 = datetime(2026, 10, 13, tzinfo=UTC)
OCT_16 = datetime(2026, 10, 16, tzinfo=UTC)


def test_corpus_loads_once_and_reloads_as_unchanged(onramp_session: Session) -> None:
    first = load_documents(onramp_session, load_corpus(CORPUS))
    second = load_documents(onramp_session, load_corpus(CORPUS))

    assert len(first.loaded) == len(load_corpus(CORPUS))
    assert second.loaded == []
    assert len(second.unchanged) == len(load_corpus(CORPUS))
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


def _open_v6(tmp_path: Path, text: str | None = None) -> Path:
    """Quality review v6 as it was published, before v7 ended it."""
    path = tmp_path / "quality_review" / "v6.md"
    path.parent.mkdir(exist_ok=True)
    source = (CORPUS / "quality_review" / "v6.md").read_text()
    closed = "effective_until: 2026-10-15T00:00:00Z"
    assert closed in source
    path.write_text((text or source).replace(closed, "effective_until: null"))
    return path


def test_publishing_a_new_version_closes_the_open_one(
    onramp_session: Session, tmp_path: Path
) -> None:
    before = [
        d
        for d in load_corpus(CORPUS)
        if (d.front.policy_id, d.front.version)
        not in {("quality_review", 6), ("quality_review", 7)}
    ]
    load_documents(onramp_session, [*before, parse(_open_v6(tmp_path))])

    result = load_documents(onramp_session, load_corpus(CORPUS), now=OCT_13)

    assert result.closed == ["quality_review v6"]
    assert result.loaded == ["quality_review v7"]
    assert applicable_version(onramp_session, "quality_review", OCT_13).version == 6
    assert applicable_version(onramp_session, "quality_review", OCT_16).version == 7


def test_closing_is_refused_when_text_changes_or_the_end_is_in_the_past(
    onramp_session: Session, tmp_path: Path
) -> None:
    load_documents(onramp_session, [parse(_open_v6(tmp_path))])
    corpus = {(d.front.policy_id, d.front.version): d for d in load_corpus(CORPUS)}
    closing = corpus[("quality_review", 6)]
    edited = tmp_path / "edited" / "quality_review" / "v6.md"
    edited.parent.mkdir(parents=True)
    edited.write_text(
        (CORPUS / "quality_review" / "v6.md").read_text().replace("or when the", "and when the")
    )

    with pytest.raises(ImmutableVersionError, match="changed after it was loaded"):
        load_documents(onramp_session, [parse(edited)], now=OCT_13)
    with pytest.raises(ImmutableVersionError, match="cannot be ended in the past"):
        load_documents(onramp_session, [closing], now=OCT_16)


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
