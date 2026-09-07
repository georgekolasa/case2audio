"""Check AWS access before extraction and refresh browser sessions when needed."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from typing import Any

import boto3
from botocore.exceptions import (
    ClientError,
    LoginRefreshRequired,
    LoginTokenLoadError,
    SSOTokenLoadError,
    UnauthorizedSSOTokenError,
)

from .errors import Case2AudioError


def prepare_session(*, profile: str | None, region: str | None) -> Any:
    """Refresh an expired login once, then return a verified SDK session."""

    session = None
    try:
        # Login tokens refresh in the profile's region; the Polly bucket may be elsewhere.
        session = boto3.Session(profile_name=profile)
        # Resolve the effective profile once so recovery never falls back to another account.
        profile = session.profile_name
        print(f"Checking AWS session (profile: {profile})...", flush=True)
        session.client("sts", region_name=region).get_caller_identity()
    except Exception as exc:
        login_kind = _login_kind(exc, session)
        if login_kind is None:
            raise Case2AudioError(f"AWS access check failed before extraction: {exc}") from exc

        command = ["aws", *login_kind, "--profile", profile]
        recovery = shlex.join(command)
        # Scheduled jobs must fail with an actionable command instead of waiting for a browser.
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise Case2AudioError(
                f"AWS session expired for profile {profile!r}. Run: {recovery}, "
                "then rerun your command."
            ) from exc
        if shutil.which("aws") is None:
            raise Case2AudioError(
                f"AWS session expired. Install AWS CLI v2, then run: {recovery}"
            ) from exc

        print(
            f"AWS session expired. Opening browser login for profile {profile!r}.\n"
            "Finish signing in; audio creation will continue automatically.",
            flush=True,
        )
        try:
            # Pass an argument list so profile names never become shell commands.
            subprocess.run(command, check=True)
            # The old SDK session may retain expired credentials in memory.
            session = boto3.Session(profile_name=profile)
            session.client("sts", region_name=region).get_caller_identity()
        except (OSError, subprocess.CalledProcessError) as login_exc:
            raise Case2AudioError(f"AWS login did not finish. Retry: {recovery}") from login_exc
        except Exception as login_exc:
            raise Case2AudioError(
                f"AWS access still failed after login for profile {profile!r}: {login_exc}"
            ) from login_exc

    print("AWS session ready.", flush=True)
    return session


def _login_kind(exc: Exception, session: Any) -> list[str] | None:
    """Only renew recognized browser credentials; other failures need their real error."""

    if isinstance(exc, (LoginRefreshRequired, LoginTokenLoadError)):
        return ["login"]
    if isinstance(exc, (UnauthorizedSSOTokenError, SSOTokenLoadError)):
        return ["sso", "login"]
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        message = error.get("Message", "").casefold()
        # Botocore only translates some refresh failures into LoginRefreshRequired.
        # AWS also returns this raw token-exchange error when the grant is no longer usable.
        if (
            exc.operation_name == "CreateOAuth2Token"
            and error.get("Code") == "ValidationException"
            and "authorization grant" in message
            and any(word in message for word in ("invalid", "expired", "revoked", "malformed"))
        ):
            return ["login"]
    if isinstance(exc, ClientError) and exc.response["Error"]["Code"] in {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
    }:
        # Expired static keys cannot be repaired with browser login.
        try:
            method = session.get_credentials().method
        except Exception:
            return None
        if method == "login":
            return ["login"]
        if method == "sso":
            return ["sso", "login"]
    return None
