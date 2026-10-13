from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.service import CaseService


@dataclass(frozen=True)
class AuthConfig:
    secret: str
    issuer: str
    audience: str


@dataclass(frozen=True)
class AppServices:
    """Everything request handlers need, built once per process."""

    sessions: sessionmaker[Session]
    auth: AuthConfig
    cases: CaseService
