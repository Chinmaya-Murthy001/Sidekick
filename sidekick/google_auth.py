"""Sign in with Google (OAuth for an installed/local web app) and keep the token.

Setup once: in Google Cloud Console create an OAuth client of type "Web
application" with redirect URI http://localhost:8000/auth/google/callback,
enable the Google Calendar API and Gmail API, and save the downloaded JSON as
data/google_client_secret.json (or point GOOGLE_CLIENT_SECRET_FILE at it).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

DATA_DIR = Path(os.getenv("SIDEKICK_DATA") or Path(__file__).resolve().parent.parent / "data")
TOKEN_FILE = DATA_DIR / "google_token.json"
ACCOUNT_FILE = DATA_DIR / "google_account.json"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/gmail.send",
]

# The callback runs on http://localhost; oauthlib refuses plain http otherwise.
# Google may also return the scopes in a different order.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_pending: dict[str, Flow] = {}


def client_secret_file() -> Path:
    return Path(os.getenv("GOOGLE_CLIENT_SECRET_FILE") or DATA_DIR / "google_client_secret.json")


def configured() -> bool:
    return client_secret_file().is_file()


def start(redirect_uri: str) -> str:
    """Begin sign-in; returns the Google consent URL to send the user to."""
    flow = Flow.from_client_secrets_file(str(client_secret_file()), scopes=SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    _pending[state] = flow
    return url


def finish(state: str, callback_url: str) -> str:
    """Complete sign-in from Google's redirect; saves the token, returns the account email."""
    flow = _pending.pop(state, None)
    if flow is None:
        raise ValueError("Sign-in expired or was started elsewhere. Try connecting again.")
    flow.fetch_token(authorization_response=callback_url)
    creds = flow.credentials
    email = _email_from_id_token(creds.id_token) if creds.id_token else ""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    ACCOUNT_FILE.write_text(json.dumps({"email": email}), encoding="utf-8")
    return email


def load() -> tuple[Credentials, str] | None:
    """Saved credentials (refreshed if needed) and the account email, or None."""
    if not TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        else:
            return None
    email = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8")).get("email", "") if ACCOUNT_FILE.exists() else ""
    return creds, email


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
    ACCOUNT_FILE.unlink(missing_ok=True)


def _email_from_id_token(token: str) -> str:
    # The token came straight from Google's token endpoint over TLS; we only
    # read the email claim for display, so no signature check is needed here.
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
