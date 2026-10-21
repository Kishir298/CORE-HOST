"""
Deprecated compatibility shim.

Canonical configuration lives in ``core.configuration.models.Configuration``
(dot-path, nested dict). This flat-key store was superseded in v0.2.

The shim remains so ``from core.configuration.configuration import Configuration``
continues to import without breaking, but emits a DeprecationWarning.
Environment-variable loading is now canonical — see
``core.configuration.models.Configuration.load_environment`` and
``core.configuration.manager.ConfigurationManager.load_environment``.
"""

from __future__ import annotations

import warnings

from .models import Configuration as _CanonicalConfiguration

warnings.warn(
    "core.configuration.configuration.Configuration is deprecated; "
    "use core.configuration.models.Configuration (or "
    "from core.configuration import Configuration).",
    DeprecationWarning,
    stacklevel=2,
)

Configuration = _CanonicalConfiguration


class ConfigurationError(Exception):
    """Base exception for C.O.R.E. configuration errors (legacy shim)."""


class ConfigurationKeyError(ConfigurationError):
    """Raised when a requested configuration key does not exist (legacy shim)."""


__all__ = [
    "Configuration",
    "ConfigurationError",
    "ConfigurationKeyError",
]
