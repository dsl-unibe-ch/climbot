import os

import pytest
from fastapi import HTTPException

os.environ["AZURE_TENANT_ID"] = "unibe-tenant"
os.environ["AZURE_CLIENT_ID"] = "climebot-client"
os.environ["AZURE_API_SCOPE"] = "api://climebot-client/ClimeBot.Access"
os.environ["AZURE_REQUIRED_APP_ROLE"] = "Climebot.Users"
os.environ["OPENAI_API_KEY"] = "test-key"

from app.auth.azure_auth import _validate_claims


def _claims(**overrides: object) -> dict:
    claims = {
        "aud": "api://climebot-client",
        "iss": "https://login.microsoftonline.com/unibe-tenant/v2.0",
        "tid": "unibe-tenant",
        "scp": "ClimeBot.Access",
        "roles": ["Climebot.Users"],
    }
    claims.update(overrides)
    return claims


def test_accepts_required_tenant_scope_and_role() -> None:
    _validate_claims(_claims())


@pytest.mark.parametrize(
    ("claim", "value", "detail"),
    [
        ("tid", "partner-tenant", "Invalid token tenant"),
        ("scp", "Other.Scope", "Missing required API scope"),
        ("roles", ["Other.Role"], "Missing required app role"),
    ],
)
def test_rejects_missing_authorization_claim(claim: str, value: object, detail: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_claims(_claims(**{claim: value}))

    assert exc_info.value.detail == detail
