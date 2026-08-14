"""Acquire an access token via MSAL device-code flow and print it to stdout.

Usage:
    uv run python scripts/get_token.py
    export TOKEN=$(uv run python scripts/get_token.py 2>/dev/null)
"""

import os
import sys
from pathlib import Path

import msal
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "frontend/.env")

_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
_TENANT_ID = os.environ["AZURE_TENANT_ID"]
_SCOPE = os.environ.get("AZURE_API_SCOPE", f"api://{_CLIENT_ID}/ClimeBot.Access")

app = msal.PublicClientApplication(
    _CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
)

flow = app.initiate_device_flow(scopes=[_SCOPE])
if "user_code" not in flow:
    print(f"Failed to create device flow: {flow}", file=sys.stderr)
    sys.exit(1)

# Device code instruction goes to stderr so token can be captured from stdout
print(flow["message"], file=sys.stderr, flush=True)

result = app.acquire_token_by_device_flow(flow)
if "access_token" in result:
    token = result["access_token"]
    Path(".token").write_text(token)
    print(token)
    print("\n✅  Token saved to .token", file=sys.stderr)
else:
    print(f"Auth failed: {result.get('error_description', result)}", file=sys.stderr)
    sys.exit(1)
