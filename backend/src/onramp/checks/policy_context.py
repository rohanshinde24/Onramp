from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from onramp.policy.corpus import PolicyDocument


class PolicyGapError(LookupError):
    """The applicable version has no section or parameter for this situation."""


@dataclass(frozen=True)
class PolicyContext:
    """The policy version in force for a case, as checks need it."""

    policy_id: str
    version: int
    parameters: dict[str, dict[str, Any]]
    # section number -> passage ID
    passages: dict[str, str]

    def value(self, name: str) -> Any:
        if name not in self.parameters:
            raise PolicyGapError(f"{self.policy_id} v{self.version} has no parameter {name}")
        return self.parameters[name]["value"]

    def parameter_passage(self, name: str) -> str:
        if name not in self.parameters:
            raise PolicyGapError(f"{self.policy_id} v{self.version} has no parameter {name}")
        return str(self.parameters[name]["passage_id"])

    def passage(self, section: str) -> str:
        if section not in self.passages:
            raise PolicyGapError(f"{self.policy_id} v{self.version} has no section {section}")
        return self.passages[section]

    def has_section(self, section: str) -> bool:
        return section in self.passages


def from_document(document: "PolicyDocument") -> PolicyContext:
    return PolicyContext(
        policy_id=document.front.policy_id,
        version=document.front.version,
        parameters=document.parameters,
        passages={p.section: p.passage_id for p in document.passages},
    )
