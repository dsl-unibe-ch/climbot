import time
from typing import Any

import httpx
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from loguru import logger

from app.config import get_settings

settings = get_settings()

_bearer = HTTPBearer()

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL = 3600.0  # refresh public keys every hour

_JWKS_URL = f"https://login.microsoftonline.com/{settings.azure_tenant_id}/discovery/v2.0/keys"

# Accept both v1 and v2 token issuers
_VALID_ISSUERS = frozenset(
    {
        f"https://sts.windows.net/{settings.azure_tenant_id}/",
        f"https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0",
    }
)


def _validate_claims(payload: dict) -> None:
    token_aud = payload.get("aud", "")
    api_audience = settings.azure_api_scope.rsplit("/", 1)[0]
    valid_auds = {settings.azure_client_id, api_audience}
    if isinstance(token_aud, str):
        aud_set = {token_aud}
    elif isinstance(token_aud, list) and all(isinstance(aud, str) for aud in token_aud):
        aud_set = set(token_aud)
    else:
        aud_set = set()
    if not aud_set & valid_auds:
        logger.warning("Token has invalid audience")
        raise HTTPException(status_code=401, detail="Invalid token audience")

    if payload.get("iss") not in _VALID_ISSUERS:
        logger.warning("Token has invalid issuer")
        raise HTTPException(status_code=401, detail="Invalid token issuer")

    if payload.get("tid") != settings.azure_tenant_id:
        logger.warning("Token has invalid tenant")
        raise HTTPException(status_code=401, detail="Invalid token tenant")

    required_scope = settings.azure_api_scope.rsplit("/", 1)[-1]
    scope_claim = payload.get("scp")
    scopes = scope_claim.split() if isinstance(scope_claim, str) else []
    if required_scope not in scopes:
        logger.warning("Token is missing required API scope")
        raise HTTPException(status_code=403, detail="Missing required API scope")

    roles = payload.get("roles", [])
    if not isinstance(roles, list) or settings.azure_required_app_role not in roles:
        logger.warning("Token is missing required app role")
        raise HTTPException(status_code=403, detail="Missing required app role")


async def _get_jwks() -> dict:
    now = time.monotonic()
    if _JWKS_CACHE["keys"] is None or now - _JWKS_CACHE["fetched_at"] > _JWKS_TTL:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_JWKS_URL)
            resp.raise_for_status()
            _JWKS_CACHE["keys"] = resp.json()
            _JWKS_CACHE["fetched_at"] = now
            logger.debug("Entra ID JWKS refreshed")
    return _JWKS_CACHE["keys"]


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    token = credentials.credentials
    try:
        jwks = await _get_jwks()
        header = jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if key is None:
            raise HTTPException(status_code=401, detail="Unknown token signing key")

        # Disable library-level aud/iss checks; validate manually below so we can
        # accept both "CLIENT_ID" (v2) and "api://CLIENT_ID" (v1 / some v2 configs)
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_iss": False},
        )

        _validate_claims(payload)
        return payload

    except HTTPException:
        raise
    except JWTError as exc:
        logger.warning("JWT validation failed: {}", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
