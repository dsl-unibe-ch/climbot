import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
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
_ADMIN_CONTACT = os.environ.get("ADMIN_CONTACT", "the administrator")
# Microsoft Graph permissions for Teams membership
_SCOPES = [scope.strip() for scope in os.environ["AZURE_GRAPH_SCOPES"].split(",") if scope.strip()]

_ALLOWED_TEAM_ID = os.environ["AZURE_ALLOWED_TEAM_ID"].lower()


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        _CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
        client_credential=_CLIENT_SECRET or None,
    )


def check_team_membership(graph_token: str) -> bool:
    url = "https://graph.microsoft.com/v1.0/me/joinedTeams"

    with httpx.Client(timeout=10) as client:
        while url:
            response = client.get(
                url,
                headers={"Authorization": f"Bearer {graph_token}"},
            )
            response.raise_for_status()

            data = response.json()

            for team in data.get("value", []):
                if team["id"].lower() == _ALLOWED_TEAM_ID:
                    return True

            url = data.get("@odata.nextLink")


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
            _logo = Path(__file__).parents[1] / "assets" / "images" / "logo_name_white.svg"
            _, col, _ = st.columns([1, 2, 1])
            with col:
                st.image(str(_logo), use_container_width=True)
                upn = st.session_state.get("_access_denied_user", "your account")
                st.error(
                    f"Access denied for **{upn}**.  "
                    f"Please contact {_ADMIN_CONTACT} to request access."
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
        auth_url = _msal_app().get_authorization_request_url(
            scopes=_SCOPES,
            redirect_uri=_REDIRECT_URI,
            state=self._get_state(),
            response_type="code",
        )

        params = parse_qs(urlparse(auth_url).query)

        logger.warning("MSAL authority tenant: {}", _TENANT_ID)
        logger.warning("MSAL requested scopes: {}", params.get("scope"))
        logger.warning("MSAL client ID: {}", params.get("client_id"))

        _logo = Path(__file__).parents[1] / "assets" / "images" / "logo_name_white.svg"
        _, col, _ = st.columns([1, 2, 1])
        with col:
            st.image(str(_logo), use_container_width=True)
            st.markdown(
                "<p style='text-align:center;color:#8ba3c0;margin-top:0.25rem;'>"
                "Climate Change Research Assistant</p>",
                unsafe_allow_html=True,
            )
            st.markdown("<br>", unsafe_allow_html=True)
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
            st.markdown(
                "<p style='text-align:center;color:#4a6a8a;font-size:0.8rem;margin-top:1rem;'>"
                "Access restricted to authorized NCCR CLIM+ members</p>",
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
            graph_token = result["access_token"]

            try:
                is_member = check_team_membership(graph_token)
            except httpx.HTTPStatusError as exc:
                st.query_params.clear()
                st.error(f"Microsoft Graph returned HTTP " f"{exc.response.status_code}")
                return
            except (httpx.RequestError, ValueError) as exc:
                st.query_params.clear()
                logger.warning("Graph membership check failed: {}", exc)
                st.error("Could not verify Teams membership.")
                return

            st.query_params.clear()

            if is_member:
                st.success(
                    "Microsoft Graph consent succeeded. " "You are a member of the project Team."
                )
            else:
                st.warning(
                    "Microsoft Graph consent succeeded, "
                    "but your account is not a member "
                    "of the configured Team."
                )

            # Temporary test only: do not pass a Graph token
            # to the existing FastAPI authentication dependency.
            return

        else:
            st.query_params.clear()
            st.error(f"Login failed: " f"{result.get('error_description', 'Unknown error')}")
            logger.warning("Auth failure: {}", result)

    @staticmethod
    def _get_state() -> str:
        if "_oauth_state" not in st.session_state:
            st.session_state["_oauth_state"] = secrets.token_urlsafe(32)
        return st.session_state["_oauth_state"]
