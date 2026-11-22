"""CoreApplication wiring for security.mode (embedded | daemon)."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.application import CoreApplication
from core.security import (
    DaemonAuthorizationPDP,
    EmbeddedAuthorizationPDP,
)
from core.security.provider import ExistenceAuthenticationProvider


def make_app(tmp_path: Path) -> CoreApplication:
    """CoreApplication with a minimal loaded configuration."""
    cfg = tmp_path / "core.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "core": {"name": "C.O.R.E.", "version": "0.6.0"},
                "environment": "development",
                "network": {"enabled": False},
                "communication": {"enabled": False},
                "components": {},
            }
        ),
        encoding="utf-8",
    )
    app = CoreApplication(config_path=cfg, environment="development")
    app.configuration.load(str(cfg), environment="development")
    return app


def test_default_mode_attaches_embedded_pdp(tmp_path: Path):
    app = make_app(tmp_path)
    app._apply_security_policy()
    assert isinstance(app.security.pdp, EmbeddedAuthorizationPDP)


def test_explicit_embedded_mode(tmp_path: Path):
    app = make_app(tmp_path)
    app.configuration.set("security.mode", "embedded")
    app._apply_security_policy()
    assert isinstance(app.security.pdp, EmbeddedAuthorizationPDP)


def test_daemon_mode_attaches_daemon_pdp(tmp_path: Path):
    app = make_app(tmp_path)
    app.configuration.set("security.mode", "daemon")
    app.configuration.set("security.pdp_socket", "/tmp/custom-pdp.sock")
    app.configuration.set("security.pdp_timeout_s", 2.5)
    app._apply_security_policy()
    pdp = app.security.pdp
    assert isinstance(pdp, DaemonAuthorizationPDP)
    assert pdp.socket_path == "/tmp/custom-pdp.sock"
    assert pdp.timeout == 2.5


def test_invalid_mode_fails_closed_at_startup(tmp_path: Path):
    app = make_app(tmp_path)
    app.configuration.set("security.mode", "banana")
    try:
        app._apply_security_policy()
    except ValueError as exc:
        assert "security.mode" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_non_positive_timeout_rejected(tmp_path: Path):
    app = make_app(tmp_path)
    app.configuration.set("security.mode", "daemon")
    app.configuration.set("security.pdp_timeout_s", 0)
    try:
        app._apply_security_policy()
    except ValueError as exc:
        assert "pdp_timeout_s" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_apply_security_policy_keeps_provider_behaviour(tmp_path: Path):
    app = make_app(tmp_path)
    app.configuration.set("security.enforce_authorization", True)
    app._apply_security_policy()
    assert app.security_policy.enforced is True
    # PDP wiring must not disturb provider configuration.
    assert isinstance(app.security.provider, ExistenceAuthenticationProvider)
