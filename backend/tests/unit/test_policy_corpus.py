from itertools import pairwise
from pathlib import Path

import pytest

from onramp.policy.corpus import CorpusError, FrontMatter, load_corpus, parse

CORPUS = Path(__file__).resolve().parents[3] / "policies"

VALID = """---
policy_id: payout
domain: payout
title: T
version: 1
effective_from: 2026-01-01T00:00:00Z
effective_until: null
parameters:
  window: {value: 5, section: "1.1"}
---
## 1.1 Window

Five days.
"""


def _write(tmp_path: Path, body: str, name: str = "v1.md") -> Path:
    folder = tmp_path / "payout"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(body)
    return path


def test_real_corpus_parses_with_stable_passage_ids() -> None:
    documents = load_corpus(CORPUS)

    assert {(d.front.policy_id, d.front.version) for d in documents} == {
        ("payout", 11),
        ("payout", 12),
        ("onboarding", 3),
        ("onboarding", 4),
        ("quality_review", 5),
        ("quality_review", 6),
        ("quality_review", 7),
    }
    v12 = next(d for d in documents if d.front.version == 12)
    assert "payout_v12_section_4_3" in {p.passage_id for p in v12.passages}
    assert v12.parameters["settlement_window_days"] == {
        "value": 7,
        "passage_id": "payout_v12_section_4_2",
    }


def test_real_corpus_versions_are_contiguous_and_end_open() -> None:
    by_policy: dict[str, list[FrontMatter]] = {}
    for doc in load_corpus(CORPUS):
        by_policy.setdefault(doc.front.policy_id, []).append(doc.front)
    for versions in by_policy.values():
        versions.sort(key=lambda f: f.version)
        for older, newer in pairwise(versions):
            assert older.effective_until == newer.effective_from
        assert versions[-1].effective_until is None


def test_versions_differ_where_the_policy_changed() -> None:
    params = {(d.front.policy_id, d.front.version): d.parameters for d in load_corpus(CORPUS)}

    assert params[("payout", 11)]["settlement_window_days"]["value"] == 5
    assert params[("payout", 12)]["settlement_window_days"]["value"] == 7
    assert params[("onboarding", 3)]["tax_form_required_countries"]["value"] == []
    assert params[("onboarding", 4)]["tax_form_required_countries"]["value"] == ["US"]
    assert params[("quality_review", 6)]["dispute_window_days"]["value"] == 14


def test_parameter_citing_a_missing_section_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID.replace('section: "1.1"', 'section: "9.9"'))

    with pytest.raises(CorpusError, match=r"missing section 9\.9"):
        parse(path)


def test_file_name_must_match_version(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="file name"):
        parse(_write(tmp_path, VALID, name="v2.md"))


def test_duplicate_sections_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="duplicate"):
        parse(_write(tmp_path, VALID + "\n## 1.1 Again\n\nMore.\n"))


def test_unknown_front_matter_field_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="front matter"):
        parse(_write(tmp_path, VALID.replace("title: T", "title: T\nlatest: true")))


def test_naive_effective_date_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="front matter"):
        parse(_write(tmp_path, VALID.replace("2026-01-01T00:00:00Z", "2026-01-01T00:00:00")))
