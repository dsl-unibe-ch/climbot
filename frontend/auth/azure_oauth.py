import secrets
import time
from pathlib import Path

import msal
import streamlit as st
from dotenv import load_dotenv
from loguru import logger

# Load .env from project root when running locally (no-op in Docker)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import os  # noqa: E402 — after load_dotenv

_TENANT_ID = os.environ["AZURE_TENANT_ID"]
_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
_REDIRECT_URI = os.environ.get("AZURE_REDIRECT_URI", "http://localhost:8501")

# Falls back to a sensible default; override via AZURE_API_SCOPE in .env
_SCOPES = [os.environ.get("AZURE_API_SCOPE", f"api://{_CLIENT_ID}/ClimeBot.Access")]

# Comma-separated list of allowed UPNs/emails; empty means allow all authenticated users
_ALLOWED_USERS: set[str] = {
    u.strip().lower() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()
}


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        _CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
        client_credential=_CLIENT_SECRET or None,
    )


class AzureAuth:
    # ── Public interface ────────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        if not st.session_state.get("_token"):
            return False
        # Reauthenticate if token is within 60 s of expiry
        return time.time() < st.session_state.get("_token_expiry", 0) - 60

    def get_access_token(self) -> str:
        return st.session_state.get("_token", "")

    def get_user_info(self) -> dict:
        return st.session_state.get("_user", {})

    def logout(self) -> None:
        for key in ("_token", "_token_expiry", "_user", "_oauth_state", "messages"):
            st.session_state.pop(key, None)

    def handle_auth_flow(self) -> None:
        """Render login page or complete OAuth callback."""
        params = st.query_params.to_dict()

        if "code" in params:
            self._complete_callback(params["code"], params.get("state", ""))
            return

        if st.session_state.get("_access_denied"):
            upn = st.session_state.get("_access_denied_user", "your account")
            st.error(
                f"Access denied for **{upn}**.  "
                "Please contact [Sukanya Nath](mailto:sukanya.nath@unibe.ch) to request access."
            )
            if st.button("← Back to login"):
                st.session_state.pop("_access_denied", None)
                st.session_state.pop("_access_denied_user", None)
                st.rerun()
            return

        if "error" in params:
            st.error(f"Authentication error: {params.get('error_description', params['error'])}")
            st.query_params.clear()

        self._render_login_page()

    # ── Private helpers ─────────────────────────────────────────────────

    def _render_login_page(self) -> None:
        st.title("🌍 ClimeBot")
        st.subheader("Climate Change Research Assistant")
        st.info("Sign in with your UniBE Campus Account to continue.")

        auth_url = _msal_app().get_authorization_request_url(
            scopes=_SCOPES,
            redirect_uri=_REDIRECT_URI,
            state=self._get_state(),
            response_type="code",
        )
        # target="_self" keeps the redirect in the same tab so the callback
        # arrives back in this Streamlit session rather than a new tab
        st.markdown(
            f'<a href="{auth_url}" target="_self" '
            'style="display:block;text-align:center;padding:0.75rem 1rem;'
            "background:#0078d4;color:white;border-radius:0.5rem;"
            'text-decoration:none;font-weight:600;font-size:1rem;">'
            "\U0001f510\u00a0\u00a0Sign in with Microsoft</a>",
            unsafe_allow_html=True,
        )

    def _complete_callback(self, code: str, state: str) -> None:
        # _oauth_state is set in the same Streamlit session that built the login URL,
        # but Microsoft's redirect creates a NEW session (old WebSocket is gone).
        # Only block when we actually have a stored state to compare against.
        stored_state = st.session_state.get("_oauth_state")
        if stored_state is not None and state != stored_state:
            st.error("Invalid OAuth state — possible CSRF. Please try again.")
            st.query_params.clear()
            return

        result = _msal_app().acquire_token_by_authorization_code(
            code=code,
            scopes=_SCOPES,
            redirect_uri=_REDIRECT_URI,
        )

        if "access_token" in result:
            claims = result.get("id_token_claims", {})
            upn = (claims.get("preferred_username") or "").lower()
            if _ALLOWED_USERS and upn not in _ALLOWED_USERS:
                st.query_params.clear()
                st.session_state["_access_denied"] = True
                st.session_state["_access_denied_user"] = upn
                logger.warning("Access denied for unlisted user: {}", upn)
                st.rerun()
                return
            st.session_state["_token"] = result["access_token"]
            st.session_state["_token_expiry"] = time.time() + result.get("expires_in", 3600)
            st.session_state["_user"] = claims
            st.query_params.clear()
            logger.info("User authenticated: {}", claims.get("name"))
            st.rerun()
        else:
            st.query_params.clear()
            st.error(f"Login failed: {result.get('error_description', 'Unknown error')}")
            logger.warning("Auth failure: {}", result)

    @staticmethod
    def _get_state() -> str:
        if "_oauth_state" not in st.session_state:
            st.session_state["_oauth_state"] = secrets.token_urlsafe(32)
        return st.session_state["_oauth_state"]
