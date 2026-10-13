from datetime import UTC, datetime, timedelta

import jwt
import pytest

from onramp.identity.tokens import InvalidTokenError, issue_token, verify_token

SECRET = "unit-test-signing-key-0123456789abcdef"
NOW = datetime.now(UTC)


def _token(**overrides: object) -> str:
    args: dict[str, object] = {
        "secret": SECRET,
        "issuer": "onramp",
        "audience": "onramp-api",
        "user_id": "usr_1",
        "tenant_id": "acme",
        "now": NOW,
    }
    args.update(overrides)
    return issue_token(**args)  # type: ignore[arg-type]


def _verify(token: str) -> object:
    return verify_token(token, secret=SECRET, issuer="onramp", audience="onramp-api")


def test_round_trip() -> None:
    claims = verify_token(_token(), secret=SECRET, issuer="onramp", audience="onramp-api")

    assert (claims.user_id, claims.tenant_id) == ("usr_1", "acme")


@pytest.mark.parametrize(
    "token",
    [
        _token(now=NOW - timedelta(hours=9)),  # expired
        _token(audience="other-api"),
        _token(issuer="someone-else"),
        _token(secret="a-different-signing-key-0123456789abcdef"),
    ],
    ids=["expired", "audience", "issuer", "signature"],
)
def test_invalid_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        _verify(token)


def test_unsigned_token_is_rejected() -> None:
    payload = {
        "iss": "onramp",
        "aud": "onramp-api",
        "sub": "usr_1",
        "tid": "acme",
        "iat": NOW,
        "exp": NOW + timedelta(hours=1),
    }
    unsigned = jwt.encode(payload, key=None, algorithm="none")

    with pytest.raises(InvalidTokenError):
        _verify(unsigned)


def test_token_without_tenant_is_rejected() -> None:
    payload = {
        "iss": "onramp",
        "aud": "onramp-api",
        "sub": "usr_1",
        "iat": NOW,
        "exp": NOW + timedelta(hours=1),
    }

    with pytest.raises(InvalidTokenError):
        _verify(jwt.encode(payload, SECRET, algorithm="HS256"))


def test_role_claim_is_not_part_of_the_token() -> None:
    payload = jwt.decode(_token(), SECRET, algorithms=["HS256"], audience="onramp-api")

    assert "role" not in payload
