from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from karakeep_agent_mcp.config import Settings, is_loopback_host

from .conftest import make_settings


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KARAKEEP_URL", "https://keep.example.com/")
    monkeypatch.setenv("KARAKEEP_API_KEY", "t")
    monkeypatch.setenv("KARAKEEP_MCP_READ_ONLY", "true")
    monkeypatch.setenv("KARAKEEP_MCP_AUTH_MODE", "bearer")
    monkeypatch.setenv("KARAKEEP_MCP_BEARER_TOKENS", " " + "a" * 21 + " , " + "b" * 20 + ",")
    s = Settings()  # type: ignore[call-arg]
    assert s.karakeep_url == "https://keep.example.com"
    assert s.read_only is True
    assert [t.get_secret_value() for t in s.bearer_tokens] == ["a" * 21, "b" * 20]


def test_configuration_errors_hide_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from karakeep_agent_mcp.server import main

    monkeypatch.setenv("KARAKEEP_URL", "http://m")
    monkeypatch.setenv("KARAKEEP_API_KEY", "api-secret-do-not-log")
    monkeypatch.setenv("KARAKEEP_MCP_AUTH_MODE", "oidc")
    monkeypatch.setenv("KARAKEEP_MCP_OIDC_CLIENT_SECRET", "oidc-secret-do-not-log")
    with pytest.raises(SystemExit, match="2"):
        main([])
    error = capsys.readouterr().err
    assert "KARAKEEP_MCP_BASE_URL" in error
    assert "api-secret-do-not-log" not in error
    assert "oidc-secret-do-not-log" not in error
    assert "input_value" not in error


def test_oidc_algorithm_is_explicit_and_asymmetric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KARAKEEP_MCP_OIDC_ALGORITHM", "ES256")
    assert make_settings().oidc_algorithm == "ES256"
    with pytest.raises(ValidationError):
        make_settings(oidc_algorithm="HS256")


def test_generic_env_vars_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: field names must not be read as env vars (PATH, HOST, PORT...)."""
    monkeypatch.setenv("KARAKEEP_URL", "http://m")
    monkeypatch.setenv("KARAKEEP_API_KEY", "t")
    for name, value in {
        "PATH": "/usr/bin:/bin",
        "HOST": "evil",
        "PORT": "1",
        "TRANSPORT": "http",
        "READ_ONLY": "true",
        "AUTH_MODE": "oidc",
    }.items():
        monkeypatch.setenv(name, value)
    s = Settings()  # type: ignore[call-arg]
    assert (s.path, s.host, s.port, s.transport) == ("/mcp", "127.0.0.1", 8000, "stdio")
    assert (s.read_only, s.auth_mode) == (False, "none")


def test_load_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KARAKEEP_URL", "http://m")
    monkeypatch.setenv("KARAKEEP_API_KEY", "t")
    monkeypatch.setenv("KARAKEEP_MCP_PORT", "9000")
    assert Settings.load().port == 9000
    assert Settings.load(port=9100, read_only=True).port == 9100


def test_bearer_requires_tokens() -> None:
    with pytest.raises(ValidationError, match="KARAKEEP_MCP_BEARER_TOKENS"):
        make_settings(auth_mode="bearer")


def test_bearer_rejects_short_tokens() -> None:
    with pytest.raises(ValidationError, match="at least 16"):
        make_settings(auth_mode="bearer", bearer_tokens="short")


def test_oidc_requires_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(auth_mode="oidc", oidc_client_id="x")
    msg = str(exc.value)
    assert "KARAKEEP_MCP_BASE_URL" in msg
    assert "KARAKEEP_MCP_OIDC_CLIENT_SECRET" in msg
    assert "KARAKEEP_MCP_OIDC_CLIENT_ID" not in msg


def test_oidc_issuer_derives_discovery_url() -> None:
    s = make_settings(
        auth_mode="oidc",
        base_url="https://mcp.example.com",
        oidc_issuer="https://id.example.com/",
        oidc_client_id="id",
        oidc_client_secret="secret",
    )
    assert s.oidc_discovery_url == "https://id.example.com/.well-known/openid-configuration"
    assert s.oidc_scopes == ["openid", "profile", "email"]


def test_rejects_non_http_karakeep_url() -> None:
    with pytest.raises(ValidationError):
        make_settings(karakeep_url="file:///etc/passwd")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
        ("example.com", False),
    ],
)
def test_loopback(host: str, expected: bool) -> None:
    assert is_loopback_host(host) is expected


def test_refuses_unauthenticated_remote_bind() -> None:
    s = make_settings(transport="http", host="0.0.0.0")
    with pytest.raises(ValueError, match="refusing"):
        s.check_bind_safety()


def test_unauthenticated_remote_bind_override() -> None:
    make_settings(
        transport="http", host="0.0.0.0", allow_unauthenticated_remote=True
    ).check_bind_safety()


def test_authenticated_remote_bind_ok() -> None:
    make_settings(
        transport="http", host="0.0.0.0", auth_mode="bearer", bearer_tokens="x" * 32
    ).check_bind_safety()
    make_settings(transport="http", host="127.0.0.1").check_bind_safety()
    make_settings(transport="stdio", host="0.0.0.0").check_bind_safety()


def test_karakeep_url_accepts_api_suffix() -> None:
    assert make_settings(karakeep_url="https://keep.example.com/api/v1/").karakeep_url == (
        "https://keep.example.com"
    )


def test_requires_a_karakeep_key() -> None:
    with pytest.raises(ValidationError, match="KARAKEEP_API_KEY is required"):
        make_settings(karakeep_api_key="")


OIDC: dict[str, Any] = {
    "transport": "http",
    "auth_mode": "oidc",
    "base_url": "https://mcp.example.com",
    "oidc_issuer": "https://id.example.com",
    "oidc_client_id": "id",
    "oidc_client_secret": "secret",
    "karakeep_api_key": None,
}


def test_user_keys_from_env_and_file(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    keys.write_text("# subject=key\n\nsub-b = key-b\n", encoding="utf-8")
    s = make_settings(**OIDC, user_keys_raw="sub-a=key-a", user_keys_file=str(keys))
    assert s.user_keys == {"sub-a": "key-a", "sub-b": "key-b"}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"user_keys_raw": "sub-a"}, "not in the form"),
        ({"user_keys_raw": "a=k1,a=k2"}, "more than once"),
        ({"user_keys_raw": "a=k", "karakeep_api_key": "shared"}, "not both"),
        ({"user_keys_raw": "a=k", "auth_mode": "bearer", "bearer_tokens": "x" * 32}, "oidc"),
        ({"user_keys_raw": "a=k", "transport": "stdio"}, "TRANSPORT=http"),
        ({"user_keys_file": "/nonexistent/keys"}, "cannot read"),
    ],
)
def test_user_key_errors(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message) as exc:
        make_settings(**(OIDC | overrides))
    assert "k1" not in str(exc.value) and "k2" not in str(exc.value)


def test_user_key_errors_hide_keys() -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(**OIDC, user_keys_raw="ok=secret-key-1,broken-secret-key-2")
    assert "secret-key" not in str(exc.value)
