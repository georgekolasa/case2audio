import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, LoginRefreshRequired, UnauthorizedSSOTokenError

from case2audio import auth
from case2audio.errors import Case2AudioError


@pytest.fixture
def aws_login(monkeypatch):
    old_session = Mock(profile_name="case2audio")
    fresh_session = Mock(profile_name="case2audio")
    factory = Mock(side_effect=[old_session, fresh_session])
    login = Mock()
    monkeypatch.setattr(auth.boto3, "Session", factory)
    monkeypatch.setattr(auth.subprocess, "run", login)
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/local/bin/aws")
    monkeypatch.setattr(
        auth,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True),
        ),
    )
    return old_session, fresh_session, factory, login


def test_valid_session_never_starts_login(aws_login):
    old, _, factory, login = aws_login
    assert auth.prepare_session(profile="case2audio", region="us-east-1") is old
    factory.assert_called_once_with(profile_name="case2audio")
    old.client.assert_called_once_with("sts", region_name="us-east-1")
    login.assert_not_called()


@pytest.mark.parametrize(
    ("error", "subcommand"),
    [
        (LoginRefreshRequired(), ["login"]),
        (UnauthorizedSSOTokenError(), ["sso", "login"]),
        (
            ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": (
                            "The provided authorization grant is invalid, expired, revoked, "
                            "or malformed"
                        ),
                    }
                },
                "CreateOAuth2Token",
            ),
            ["login"],
        ),
    ],
)
def test_expired_session_logs_into_same_profile_and_rechecks(aws_login, error, subcommand):
    old, fresh, factory, login = aws_login
    old.client.return_value.get_caller_identity.side_effect = error

    assert auth.prepare_session(profile="case2audio", region="us-east-1") is fresh

    login.assert_called_once_with(["aws", *subcommand, "--profile", "case2audio"], check=True)
    assert factory.call_count == 2
    assert all(call.kwargs == {"profile_name": "case2audio"} for call in factory.call_args_list)
    fresh.client.assert_called_once_with("sts", region_name="us-east-1")
    fresh.client.return_value.get_caller_identity.assert_called_once()


def test_login_uses_effective_profile_when_no_flag_is_given(aws_login):
    old, _, _, login = aws_login
    old.profile_name = "school"
    old.client.return_value.get_caller_identity.side_effect = LoginRefreshRequired()
    auth.prepare_session(profile=None, region=None)
    login.assert_called_once_with(["aws", "login", "--profile", "school"], check=True)


def test_noninteractive_run_prints_named_recovery_without_login(aws_login, monkeypatch):
    old, _, _, login = aws_login
    old.client.return_value.get_caller_identity.side_effect = LoginRefreshRequired()
    monkeypatch.setattr(auth.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(Case2AudioError, match="aws login --profile case2audio"):
        auth.prepare_session(profile="case2audio", region=None)
    login.assert_not_called()


@pytest.mark.parametrize("code", ["AccessDenied", "ExpiredToken"])
def test_unrelated_failure_does_not_attempt_browser_login(aws_login, code):
    old, _, _, login = aws_login
    old.get_credentials.return_value.method = "shared-credentials-file"
    old.client.return_value.get_caller_identity.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "test failure"}}, "GetCallerIdentity"
    )
    with pytest.raises(Case2AudioError, match="test failure"):
        auth.prepare_session(profile="case2audio", region=None)
    login.assert_not_called()


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("CreateOAuth2Token", "Missing required parameter: clientId"),
        ("GetCallerIdentity", "The provided authorization grant is invalid"),
    ],
)
def test_other_validation_errors_do_not_trigger_login(aws_login, operation, message):
    old, _, _, login = aws_login
    old.client.return_value.get_caller_identity.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": message}}, operation
    )
    with pytest.raises(Case2AudioError, match="AWS access check failed"):
        auth.prepare_session(profile="case2audio", region=None)
    login.assert_not_called()


def test_failed_login_stops_without_retry_loop(aws_login):
    old, _, factory, login = aws_login
    old.client.return_value.get_caller_identity.side_effect = LoginRefreshRequired()
    login.side_effect = subprocess.CalledProcessError(1, "aws")
    with pytest.raises(Case2AudioError, match="AWS login did not finish"):
        auth.prepare_session(profile="case2audio", region=None)
    assert factory.call_count == 1
    assert login.call_count == 1


def test_failed_post_login_check_stops_without_retry_loop(aws_login):
    old, fresh, _, login = aws_login
    old.client.return_value.get_caller_identity.side_effect = LoginRefreshRequired()
    fresh.client.return_value.get_caller_identity.side_effect = LoginRefreshRequired()
    with pytest.raises(Case2AudioError, match="AWS access still failed after login"):
        auth.prepare_session(profile="case2audio", region=None)
    assert login.call_count == 1
