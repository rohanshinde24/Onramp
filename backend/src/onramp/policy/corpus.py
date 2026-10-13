"""Parsing the policy corpus (``policies/<policy_id>/v<N>.md``)."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

_SECTION = re.compile(r"^## (\d+\.\d+) (.+)$", re.MULTILINE)


class CorpusError(ValueError):
    pass


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any
    section: str = Field(pattern=r"^\d+\.\d+$")


class FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = Field(pattern=r"^[a-z_]+$")
    domain: str = Field(pattern=r"^[a-z_]+$")
    title: str
    version: int = Field(gt=0)
    effective_from: AwareDatetime
    effective_until: AwareDatetime | None
    parameters: dict[str, Parameter]


@dataclass(frozen=True)
class Passage:
    passage_id: str
    section: str
    heading: str
    text: str


@dataclass(frozen=True)
class PolicyDocument:
    front: FrontMatter
    passages: tuple[Passage, ...]
    sha256: str

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        """Parameters with their section resolved to a passage ID."""
        return {
            name: {"value": p.value, "passage_id": passage_id(self.front, p.section)}
            for name, p in self.front.parameters.items()
        }


def passage_id(front: FrontMatter, section: str) -> str:
    return f"{front.policy_id}_v{front.version}_section_{section.replace('.', '_')}"


def parse(path: Path) -> PolicyDocument:
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("---\n", 2)
    if len(parts) != 3 or parts[0] != "":
        raise CorpusError(f"{path}: missing front matter")
    try:
        front = FrontMatter.model_validate(yaml.safe_load(parts[1]))
    except (yaml.YAMLError, ValidationError) as exc:
        raise CorpusError(f"{path}: invalid front matter: {exc}") from exc
    if path.stem != f"v{front.version}" or path.parent.name != front.policy_id:
        raise CorpusError(f"{path}: file name does not match policy_id and version")

    body = parts[2]
    headers = list(_SECTION.finditer(body))
    if not headers:
        raise CorpusError(f"{path}: no sections")
    passages = []
    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        text = " ".join(body[match.end() : end].split())
        if not text:
            raise CorpusError(f"{path}: section {match.group(1)} is empty")
        passages.append(
            Passage(passage_id(front, match.group(1)), match.group(1), match.group(2).strip(), text)
        )

    sections = [p.section for p in passages]
    if len(set(sections)) != len(sections):
        raise CorpusError(f"{path}: duplicate section numbers")
    for name, parameter in front.parameters.items():
        if parameter.section not in sections:
            raise CorpusError(f"{path}: parameter {name} cites missing section {parameter.section}")
    return PolicyDocument(front, tuple(passages), hashlib.sha256(raw.encode()).hexdigest())


def load_corpus(root: Path) -> list[PolicyDocument]:
    documents = [parse(p) for p in sorted(root.glob("*/v*.md"))]
    if not documents:
        raise CorpusError(f"no policy files under {root}")
    return documents
