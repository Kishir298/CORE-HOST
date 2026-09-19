from pathlib import Path

from core.communication import LocalCommunication
from core.communication.devices import DeviceRegistry
from core.configuration import ConfigurationManager
from core.dependencies import DependencyManager
from core.events import (
    AGENT_STARTED,
    AGENT_STOPPED,
    COMPONENT_STARTED,
    DEVICE_CONNECTED,
    DEVICE_DISCONNECTED,
    EventBus,
    HEALTH_CHANGED,
    MESSAGE_SENT,
    RESOURCE_REGISTERED,
    RESOURCE_REMOVED,
    SYSTEM_STARTED,
    SYSTEM_STOPPED,
)
from core.health import HealthMonitor, HealthResult, HealthStatus
from core.logging import CoreLogger
from core.organization import OrganizationEngine
from core.rescs import (
    FileRescsAdapter,
    HttpRescsAdapter,
    InMemoryRescsAdapter,
    RescsAdapter,
)
from core.resources import Resource, ResourceRegistry
from core.routing import Router
from core.runtime import EntityType, Runtime, RuntimeHistory, RuntimeState
from core.scheduler import AgentScheduler
from core.security import (
    Permission,
    SecurityManager,
    SecurityPolicy,
)
from core.services import (
    Service,
    ServiceDispatcher,
    ServiceManager,
)
from core.version import (
    __version__ as CORE_VERSION,
    LEGACY_VERSIONS,
    SUPPORTED_VERSIONS,
    is_legacy,
    negotiate,
)

DEFAULT_CONFIG_PATH = Path("config/core.yaml")


class CoreApplication:
    """
    Top-level C.O.R.E. application.

    CoreApplication owns the canonical runtime component graph and
    orchestrates genuine startup and shutdown of every C.O.R.E. subsystem.
    Startup initializes and activates components in dependency order and
    shutdown deactivates them in reverse order.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        environment: str = "development",
        rescs_adapter: RescsAdapter | None = None,
    ) -> None:
        self._config_path = config_path
        self._environment = environment

        self.runtime = Runtime()

        self.configuration = ConfigurationManager()
        self.logger = CoreLogger()
        self._injected_rescs_adapter = rescs_adapter

        self.events = EventBus()
        self.communication = LocalCommunication(
            on_delivery=self._emit_message_sent
        )
        self.resources = ResourceRegistry()
        self.organization = OrganizationEngine(registry=self.resources)
        self.resources.attach_organization(self.organization)
        self.health = HealthMonitor(on_change=self._emit_health_changed)
        self.dependencies = DependencyManager()
        self.security = SecurityManager()
        self.security_policy = SecurityPolicy()
        self.services = ServiceManager()
        self.routing = Router(self.communication)
        # Authoritative device registry shared by routing and (once a TCP
        # transport is selected) the network layer. Backed by the resource
        # registry so device state stays single-sourced.
        self.device_registry = DeviceRegistry(resource_registry=self.resources)
        self.routing.set_device_registry(self.device_registry)
        # Data organization layer: coordinates DATA_REQUEST handling over
        # R.E.S.C.S. through the existing device communication system.
        # The reader factory resolves the current R.E.S.C.S. adapter at
        # call time so config-driven adapter swaps keep working.
        from core.data.organizer import DataOrganizer

        self.data_organizer = DataOrganizer(
            reader_factory=self._data_reader_factory,
            security_manager=self.security,
            device_registry=self.device_registry,
            event_bus=self.events,
        )
        self.dispatcher = ServiceDispatcher(
            self.services,
            security=self.security,
            policy=self.security_policy,
            emitter=self._emit_service_event,
        )
        self.runtime_history = RuntimeHistory()
        self.scheduler = AgentScheduler()
        # Default to InMemory unless an adapter is injected. Config-driven
        # selection happens in _apply_rescs_policy after configuration loads.
        self.rescs: RescsAdapter = rescs_adapter or InMemoryRescsAdapter()
        # Device persistence starts against the initial adapter; the
        # R.E.S.C.S. policy re-attaches it if the adapter changes.
        try:
            self.device_registry.set_device_store(self.rescs)
        except Exception:
            pass

        self._initialized = False
        self._disabled_components: set[str] = set()

        self._register_runtime_components()
        self._register_dependencies()
        self._register_internal_services()
        self._register_security_policy()

        self._initialized = True

    def _resolve_config_path(self) -> Path | None:
        """Resolve the configuration file to load, if any."""

        if self._config_path is not None:
            return Path(self._config_path)

        default = DEFAULT_CONFIG_PATH

        if default.exists():
            return default

        return None

    def _register_runtime_components(self) -> None:
        """
        Register all C.O.R.E. subsystems with the runtime.

        Runtime dependencies mirror the dependency graph maintained by
        DependencyManager so lifecycle orchestration follows the same
        architecture used by the rest of the application.
        """

        self.runtime.register_component(
            "configuration",
            self._initialize_configuration,
            self._shutdown_configuration,
        )

        self.runtime.register_component(
            "logging",
            self._initialize_logging,
            self._shutdown_logging,
            dependencies=["configuration"],
        )

        self.runtime.register_component(
            "security",
            self._initialize_security,
            self._shutdown_security,
            dependencies=["configuration"],
        )

        self.runtime.register_component(
            "resources",
            self._initialize_resources,
            self._shutdown_resources,
            dependencies=["configuration"],
        )

        self.runtime.register_component(
            "organization",
            self._initialize_organization,
            self._shutdown_organization,
            dependencies=["resources"],
        )

        self.runtime.register_component(
            "events",
            self._initialize_events,
            self._shutdown_events,
            dependencies=["configuration"],
        )

        self.runtime.register_component(
            "communication",
            self._initialize_communication,
            self._shutdown_communication,
            dependencies=["events", "security"],
        )

        self.runtime.register_component(
            "routing",
            self._initialize_routing,
            self._shutdown_routing,
            dependencies=["communication"],
        )

        self.runtime.register_component(
            "health",
            self._initialize_health,
            self._shutdown_health,
            dependencies=["events"],
        )

        self.runtime.register_component(
            "rescs",
            self._initialize_rescs,
            self._shutdown_rescs,
            dependencies=["configuration", "events"],
        )

        self.runtime.register_component(
            "dependencies",
            self._initialize_dependencies,
            self._shutdown_dependencies,
        )

        self.runtime.register_component(
            "services",
            self._initialize_services,
            self._shutdown_services,
            dependencies=["dependencies", "health"],
        )

        self.runtime.register_component(
            "core",
            self._initialize,
            self._shutdown,
            dependencies=[
                "configuration",
                "logging",
                "security",
                "resources",
                "organization",
                "events",
                "communication",
                "routing",
                "health",
                "rescs",
                "dependencies",
                "services",
            ],
        )

    def _register_dependencies(self) -> None:
        dependencies = {
            "configuration": [],
            "logging": ["configuration"],
            "security": ["configuration"],
            "resources": ["configuration"],
            "organization": ["resources"],
            "events": ["configuration"],
            "communication": ["events", "security"],
            "routing": ["communication"],
            "health": ["events"],
            "rescs": ["configuration", "events"],
            "dependencies": [],
            "services": ["dependencies", "health"],
        }

        for component_id, component_dependencies in dependencies.items():
            self.dependencies.register(
                component_id,
                component_dependencies,
            )

        self.dependencies.register(
            "core",
            list(dependencies.keys()),
        )

        self.dependencies.validate()

    def _register_internal_services(self) -> None:
        internal_services = [
            Service(
                service_id="communication",
                name="Communication",
                version="0.1.0",
            ),
            Service(
                service_id="events",
                name="Event System",
                version="0.1.0",
            ),
            Service(
                service_id="resources",
                name="Resource Manager",
                version="0.1.0",
            ),
            Service(
                service_id="organization",
                name="Organization Engine",
                version="0.1.0",
                dependencies=["resources"],
            ),
            Service(
                service_id="routing",
                name="Data Router",
                version="0.1.0",
                dependencies=["communication"],
            ),
            Service(
                service_id="health",
                name="Health Monitor",
                version="0.1.0",
                dependencies=["events"],
            ),
            Service(
                service_id="runtime",
                name="Runtime History",
                version="0.1.0",
            ),
            Service(
                service_id="rescs",
                name="R.E.S.C.S. Adapter",
                version="0.1.0",
            ),
            Service(
                service_id="agent",
                name="Agent Scheduler",
                version="0.1.0",
            ),
        ]

        for service in internal_services:
            self.services.register(service)

    def _register_security_policy(self) -> None:
        """
        Declare the permission required for each internal service operation.

        Read-only operations require READ; mutations require WRITE.
        Operations without a registered requirement remain open.
        """

        read_operations = {
            "resources": ["list", "get", "discover", "category"],
            "organization": ["list", "by_category"],
            "routing": ["routes"],
            "health": ["status", "overall"],
            "communication": ["status"],
            "events": ["status"],
            "runtime": ["history", "active", "completed", "get", "version"],
            "rescs": ["list", "get", "health", "runtimes"],
            "agent": ["profiles", "assignments", "list", "get", "status"],
        }

        write_operations = {
            "resources": ["register", "update", "remove"],
            "agent": ["assign", "release"],
        }

        for service_id, operations in read_operations.items():
            for operation in operations:
                self.security_policy.grant(
                    service_id,
                    operation,
                    Permission.READ,
                )

        for service_id, operations in write_operations.items():
            for operation in operations:
                self.security_policy.grant(
                    service_id,
                    operation,
                    Permission.WRITE,
                )

    def _load_configuration(self) -> None:
        """
        Load configuration and apply the component policy.

        Configuration is loaded before the runtime starts so enable/disable
        controls are in effect from the very first component initialization.
        Environment variables (CORE_*) override file values.
        """

        self.configuration.start()

        path = self._resolve_config_path()

        if path is None:
            self.logger.info(
                "No configuration file found; using defaults."
            )
            # Still allow environment overrides to drive policy
            try:
                env_loaded = self.configuration.load_environment()
                if env_loaded:
                    self.logger.info(
                        "Configuration overrides from environment: "
                        + ", ".join(sorted(env_loaded.keys()))
                    )
            except Exception:
                pass
            self._apply_component_policy()
            self._deactivate_disabled_components()
            self._apply_security_policy()
            self._apply_transport_policy()
            self._apply_rescs_policy()
            self._restore_persisted_devices()
            return

        self.configuration.load(path, environment=self._environment)

        self.logger.info(
            f"Configuration loaded: {path}"
        )

        # Environment overrides (CORE_*) take precedence over file values.
        try:
            env_loaded = self.configuration.load_environment()
            if env_loaded:
                self.logger.info(
                    "Configuration overrides from environment: "
                    + ", ".join(sorted(env_loaded.keys()))
                )
        except Exception as exc:
            self.logger.warning(f"Failed to load environment overrides: {exc}")

        self._apply_component_policy()
        self._deactivate_disabled_components()
        # Transport first: it pins loopback to 127.0.0.1, so the security
        # policy below sees the effective bind address (a loopback
        # transport can never be judged external).
        self._apply_transport_policy()
        self._apply_security_policy()
        self._apply_rescs_policy()
        self._restore_persisted_devices()

    def _apply_security_policy(self) -> None:
        """
        Activate authorization enforcement based on configuration.

        Enforcement is opt-in: it is active only when explicitly enabled so
        the internal trusted routing spine is unchanged until a security
        boundary is configured. The authentication provider is also
        config-driven (existence|token) for Windows co-hosted deployments.
        """

        enabled = self.configuration.get(
            "security.enforce_authorization",
            False,
        )

        self.security_policy.set_enforced(bool(enabled))

        # Configure authentication provider if specified.
        provider_name = self.configuration.get("security.provider", None)
        if provider_name is None:
            # Also support security.authentication.provider for legacy docs
            provider_name = self.configuration.get(
                "security.authentication.provider",
                None,
            )

        # External-device boundary: 0.0.0.0 + external TCP transport must
        # never use existence-only authentication. Fail closed.
        external = self._is_external_device_config()
        if external and provider_name is None:
            from core.security.provider import (
                TokenAuthenticationProvider,
            )

            self.security.set_provider(TokenAuthenticationProvider())
            self.logger.info(
                "External-device configuration: using TokenAuthenticationProvider."
            )
            return

        if external and isinstance(provider_name, str):
            normalized = provider_name.strip().lower()
            if normalized in ("existence", "allow", "default", "none"):
                raise ValueError(
                    "External-device configuration (0.0.0.0) cannot use "
                    "existence-only authentication; configure "
                    "security.provider: token."
                )
            if normalized not in ("token", "credential", "bearer"):
                raise ValueError(
                    "External-device configuration (0.0.0.0) requires "
                    f"security.provider: token; got '{provider_name}'."
                )

        if isinstance(provider_name, str):
            normalized = provider_name.strip().lower()
            try:
                if normalized in ("token", "credential", "bearer"):
                    from core.security.provider import (
                        TokenAuthenticationProvider,
                    )

                    self.security.set_provider(TokenAuthenticationProvider())
                    self.logger.info(
                        "Security provider switched to TokenAuthenticationProvider."
                    )
                elif normalized in ("existence", "allow", "default", "none"):
                    from core.security.provider import (
                        ExistenceAuthenticationProvider,
                    )

                    self.security.set_provider(
                        ExistenceAuthenticationProvider()
                    )
            except Exception as exc:
                self.logger.warning(
                    f"Failed to configure security provider '{provider_name}': {exc}"
                )

    def _is_external_device_config(self) -> bool:
        """Return whether config resolves to external-device networking.

        External means network.enabled=true AND an external TCP transport
        AND communication.host=0.0.0.0. Any lookup failure is treated as
        non-external (localhost legacy behavior preserved).
        """
        try:
            if not self.configuration.is_running:
                return False
            network_enabled = self.configuration.get("network.enabled", False)
            transport = self.configuration.get("communication.transport", "local")
            host = self.configuration.get("communication.host", "127.0.0.1")
        except Exception:
            return False
        if network_enabled is not True:
            return False
        if not isinstance(transport, str) or transport.strip().lower() not in (
            "tcp",
            "network",
            "external",
            "loopback",
        ):
            return False
        return isinstance(host, str) and host.strip() == "0.0.0.0"

    def _apply_transport_policy(self) -> None:
        """
        Select the communication transport based on configuration.

        Defaults to LocalTransport. When network.enabled is true and
        communication.transport requests an external transport (tcp/network/
        external), TcpTransport is instantiated on Windows.

        External LAN binding: set ``communication.host=0.0.0.0`` with
        ``network.enabled=true`` to listen on all interfaces. This
        requires a Windows firewall exception (see docs/windows-firewall.md).
        Loopback (127.0.0.1) remains the safe default.
        """

        if not self.configuration.is_running:
            return

        # If communication is disabled, no transport swap is needed.
        if "communication" in self._disabled_components:
            return

        transport_name = self.configuration.get(
            "communication.transport",
            "local",
        )
        network_enabled = self.configuration.get(
            "network.enabled",
            False,
        )

        # Normalize transport name
        if isinstance(transport_name, str):
            transport_name = transport_name.strip().lower()
        else:
            transport_name = "local"

        if transport_name == "loopback":
            # The loopback transport must never bind externally: pin it to
            # 127.0.0.1 even if communication.host says otherwise. External
            # enforcement therefore keys off the effective bind address.
            try:
                current_host = self.configuration.get(
                    "communication.host", "127.0.0.1"
                )
            except Exception:
                current_host = "127.0.0.1"
            if (
                isinstance(current_host, str)
                and current_host.strip()
                and current_host.strip() != "127.0.0.1"
            ):
                self.logger.warning(
                    "communication.transport=loopback never binds externally; "
                    f"overriding host {current_host!r} with 127.0.0.1."
                )
                try:
                    self.configuration.set("communication.host", "127.0.0.1")
                except Exception:
                    pass

        wants_external = network_enabled is True and transport_name in (
            "tcp",
            "network",
            "external",
            "loopback",
        )

        if not wants_external:
            # LocalTransport is already the default; ensure router points to it.
            if transport_name not in ("local", "memory", "inprocess", ""):
                self.logger.warning(
                    f"Unknown communication.transport '{transport_name}'; using local."
                )
            return

        # Attempt to switch to TcpTransport for Windows co-hosted deployment.
        try:
            from core.communication.tcp import TcpTransport  # type: ignore

            host = self.configuration.get("communication.host", "127.0.0.1")
            port = self.configuration.get("communication.port", 0)

            # Coerce port to int if needed
            if isinstance(port, str):
                try:
                    port = int(port)
                except ValueError:
                    port = 0

            # Guard 0.0.0.0 without network.enabled — downgrade to loopback
            raw_host = str(host).strip() if host else "127.0.0.1"
            if raw_host == "0.0.0.0" and network_enabled is not True:
                self.logger.warning(
                    "communication.host=0.0.0.0 requires network.enabled=true; "
                    "falling back to 127.0.0.1 for safety."
                )
                raw_host = "127.0.0.1"

            # Validate port range
            port_int = int(port) if isinstance(port, int) else 0
            if port_int < 0 or port_int > 65535:
                self.logger.warning(
                    f"communication.port {port_int} out of range (0-65535); using 0."
                )
                port_int = 0

            # TLS — localhost-only plaintext legacy fallback; external fails closed (see tcp.py:122-134)
            tls_enabled = self.configuration.get("communication.tls.enabled", False)
            if not isinstance(tls_enabled, bool):
                tls_enabled = bool(tls_enabled) if isinstance(tls_enabled, (str, int)) else False
            # Also support network.tls.enabled for legacy docs
            if not tls_enabled:
                alt = self.configuration.get("network.tls.enabled", None)
                if isinstance(alt, bool) and alt:
                    tls_enabled = True
            tls_cert = self.configuration.get("communication.tls.certfile", None)
            tls_key = self.configuration.get("communication.tls.keyfile", None)
            tls_ca = self.configuration.get("communication.tls.cafile", None)
            tls_require = self.configuration.get("communication.tls.require_client_cert", False)
            if not isinstance(tls_require, bool):
                tls_require = False
            # Normalize paths
            if tls_cert is not None and not isinstance(tls_cert, str):
                tls_cert = str(tls_cert)
            if tls_key is not None and not isinstance(tls_key, str):
                tls_key = str(tls_key)
            if tls_ca is not None and not isinstance(tls_ca, str):
                tls_ca = str(tls_ca)

            new_transport = TcpTransport(
                host=raw_host,
                port=port_int,
                on_delivery=self._emit_message_sent,
                use_tls=bool(tls_enabled),
                certfile=tls_cert,
                keyfile=tls_key,
                cafile=tls_ca,
                require_client_cert=bool(tls_require),
                security_manager=self.security,
                device_registry=self.device_registry,
                event_bus=self.events,
                data_organizer=self.data_organizer,
                connection_lease_seconds=self.configuration.get(
                    "communication.connection_lease_seconds", None
                ),
                log_external_tokens=bool(
                    self.configuration.get(
                        "security.log_external_device_tokens", False
                    )
                    is True
                ),
                logger=self.logger,
            )

            # Preserve any already-registered endpoints by migrating them.
            # For v0.2, just swap the reference; endpoints will be
            # re-registered via _register_service_endpoints during
            # _initialize_services. Keep existing communication state.
            self.communication = new_transport  # type: ignore[assignment]
            self.routing.set_transport(new_transport)
            tls_note = " +TLS" if bool(tls_enabled) else ""
            if raw_host == "0.0.0.0":
                self.logger.warning(
                    f"Communication transport switched to TcpTransport "
                    f"({raw_host}:{port_int}{tls_note}) for network.enabled=true. "
                    "External binding requires Windows firewall exception "
                    "(see docs/windows-firewall.md)."
                )
            else:
                self.logger.info(
                    f"Communication transport switched to TcpTransport "
                    f"({raw_host}:{port_int}{tls_note}) for network.enabled=true."
                )
            if bool(tls_enabled) and not getattr(new_transport, "is_tls", False):
                self.logger.warning(
                    "TLS requested but certfile missing or invalid — falling back to plaintext "
                    "(localhost legacy compatibility only; external 0.0.0.0 fails closed)."
                )
        except ImportError:
            self.logger.warning(
                "TcpTransport not available; falling back to LocalTransport."
            )
        except Exception as exc:
            # Fail closed for external binding: never silently fall back to
            # LocalTransport when 0.0.0.0 + network.enabled was requested.
            try:
                _raw = str(
                    self.configuration.get(
                        "communication.host", "127.0.0.1"
                    )
                ).strip()
                _net = self.configuration.get("network.enabled", False)
            except Exception:
                _raw, _net = "127.0.0.1", False
            if _raw == "0.0.0.0" and _net is True:
                raise
            self.logger.warning(
                f"Failed to switch to TcpTransport ({exc}); using LocalTransport."
            )

    def _apply_rescs_policy(self) -> None:
        """
        Select the R.E.S.C.S. adapter based on configuration.

        For Windows laptop co-hosting, supports:
        - memory (default, deterministic)
        - file   (persistent JSON at var/rescs.json or custom rescs.path)
        - http   (stub, delegates to R.E.S.C.S. HTTP when available)
        """

        if not self.configuration.is_running:
            return

        # Injected adapter takes precedence unless config overrides explicitly.
        # If an adapter was injected programmatically, respect it unless
        # configuration explicitly sets rescs.adapter.
        if self._injected_rescs_adapter is not None and not self.configuration.has(
            "rescs.adapter"
        ):
            return

        if self.configuration.has("rescs.enabled"):
            enabled = self.configuration.get("rescs.enabled")
            if isinstance(enabled, bool) and not enabled:
                # Keep current adapter but log disabled
                self.logger.info("R.E.S.C.S. persistence disabled by configuration.")
                return

        adapter_name = self.configuration.get("rescs.adapter", "memory")
        if not isinstance(adapter_name, str):
            adapter_name = "memory"
        adapter_name = adapter_name.strip().lower()

        try:
            if adapter_name in ("memory", "inmemory", "default"):
                # Only swap if not already InMemory
                if not isinstance(self.rescs, InMemoryRescsAdapter):
                    self.rescs = InMemoryRescsAdapter()
                    self.logger.info("R.E.S.C.S. adapter switched to InMemory.")
            elif adapter_name in ("file", "json", "persistent"):
                rescs_path = self.configuration.get("rescs.path", None)
                if rescs_path is None:
                    rescs_path = self.configuration.get("rescs.file", None)
                # Windows-friendly default
                target_path = Path(str(rescs_path)) if rescs_path else Path("var") / "rescs.json"
                self.rescs = FileRescsAdapter(path=target_path)
                self.logger.info(
                    f"R.E.S.C.S. adapter switched to File ({target_path})."
                )
            elif adapter_name in ("http", "remote", "network"):
                endpoint = self.configuration.get(
                    "rescs.endpoint", "http://localhost:8081"
                )
                if not isinstance(endpoint, str):
                    endpoint = "http://localhost:8081"
                timeout = self.configuration.get("rescs.timeout", 2.0)
                if isinstance(timeout, str):
                    try:
                        timeout = float(timeout)
                    except ValueError:
                        timeout = 2.0
                fallback = self.configuration.get("rescs.fallback", True)
                if not isinstance(fallback, bool):
                    fallback = True
                self.rescs = HttpRescsAdapter(
                    endpoint=endpoint, timeout=float(timeout), fallback=bool(fallback)
                )
                self.logger.info(
                    f"R.E.S.C.S. adapter switched to Http ({endpoint}, timeout={timeout})."
                )
            else:
                self.logger.warning(
                    f"Unknown rescs.adapter '{adapter_name}'; using memory."
                )
        except Exception as exc:
            self.logger.warning(
                f"Failed to switch R.E.S.C.S. adapter to '{adapter_name}': {exc}; using memory."
            )
            try:
                self.rescs = InMemoryRescsAdapter()
            except Exception:
                pass

    def _restore_persisted_devices(self) -> None:
        """Restore persisted device identities (offline) + credentials.

        Runs after the R.E.S.C.S. policy so the active adapter is known.
        Restored devices start offline with no connection; only a fresh
        authentication + DEVICE_REGISTER brings them online. Security
        identities are re-provisioned (without overwriting existing ones)
        so restored devices can authenticate on reconnect.
        """
        try:
            self.device_registry.set_device_store(self.rescs)
        except Exception:
            pass
        try:
            list_devices = getattr(self.rescs, "list_devices", None)
            stored = list_devices() if callable(list_devices) else []
        except Exception:
            stored = []
        if not stored:
            return
        try:
            restored = self.device_registry.restore_all(stored)
        except Exception as exc:
            self.logger.warning(f"Failed to restore persisted devices: {exc}")
            return
        reprovisioned = 0
        for item in stored:
            try:
                if not isinstance(item, dict) or not item.get("device_id"):
                    continue
                device_id = item["device_id"]
                try:
                    self.security.get_identity(device_id)
                    continue  # operator-provisioned identity wins
                except Exception:
                    pass
                from core.security.models import Identity, IdentityType

                permissions = set()
                for name in item.get("permissions", []) or []:
                    try:
                        permissions.add(Permission(str(name)))
                    except Exception:
                        continue
                if not permissions:
                    permissions.add(Permission.READ)
                token = item.get("token")
                metadata = {"token": token} if isinstance(token, str) and token else {}
                self.security.register_identity(
                    Identity(
                        identity_id=device_id,
                        name=item.get("device_name") or device_id,
                        identity_type=IdentityType.DEVICE,
                        permissions=frozenset(permissions),
                        metadata=metadata,
                    )
                )
                reprovisioned += 1
            except Exception:
                continue
        self.logger.info(
            f"Restored {restored} persisted device(s); "
            f"re-provisioned {reprovisioned} identit(ies)."
        )

    def provision_device(
        self,
        *,
        device_id: str,
        token: str,
        device_name: str | None = None,
        device_type: str = "generic",
        platform: str = "unknown",
        capabilities: list[str] | None = None,
        protocol_version: str = "0.3.0",
        permissions: list[str] | None = None,
    ) -> dict:
        """Pre-provision an external-device identity (operator use).

        Writes a sanitized persisted identity (no ``connection_id``, no live
        status) to the active R.E.S.C.S. adapter and registers the matching
        in-memory security identity so the device's first ``CORE_HANDSHAKE``
        can authenticate. Re-provisioning overwrites the stored credential.
        The token is never logged.
        """
        from core.communication.devices import DeviceRecord
        from core.communication.protocol import validate_registration_payload
        from core.security.models import Identity, IdentityType, Permission

        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id cannot be empty.")
        if not isinstance(token, str) or not token:
            raise ValueError("A non-empty token is required.")
        device_id = device_id.strip()
        payload = {
            "device_id": device_id,
            "device_name": device_name or device_id,
            "device_type": device_type,
            "platform": platform,
            "capabilities": list(capabilities or []),
            "protocol_version": protocol_version,
        }
        code, message = validate_registration_payload(payload)
        if code is not None:
            raise ValueError(message or "Invalid device registration fields.")
        permission_names = list(permissions) if permissions else ["read"]
        granted: set[Permission] = set()
        for name in permission_names:
            try:
                granted.add(Permission(str(name).strip().lower()))
            except Exception as exc:
                raise ValueError(f"Invalid permission: {name!r}.") from exc
        if not granted:
            granted.add(Permission.READ)

        if not self.configuration.is_running:
            self._load_configuration()

        record = DeviceRecord(
            device_id=device_id,
            device_name=payload["device_name"],
            device_type=device_type,
            platform=platform,
            capabilities=list(capabilities or []),
            status="offline",
            identity_id=device_id,
            connection_id=None,
            protocol_version=protocol_version,
        )
        snapshot = record.to_persistent_dict(
            token=token, permissions=sorted(p.value for p in granted)
        )
        try:
            self.rescs.persist_device(snapshot)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to persist device {device_id!r} via R.E.S.C.S.: {exc}"
            ) from exc
        try:
            try:
                self.security.unregister_identity(device_id)
            except Exception:
                pass
            self.security.register_identity(
                Identity(
                    identity_id=device_id,
                    name=payload["device_name"],
                    identity_type=IdentityType.DEVICE,
                    permissions=frozenset(granted),
                    metadata={"token": token},
                )
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to register security identity for {device_id!r}: {exc}"
            ) from exc
        self.logger.info(f"Provisioned device identity: {device_id}")
        return snapshot

    def _apply_component_policy(self) -> None:
        """
        Apply the component enable/disable policy to the runtime.

        Disabling a component also disables every enabled component that
        transitively depends on it, so a valid configuration never leaves an
        enabled component depending on a disabled dependency.
        """

        requested_disabled = {
            component
            for component in self.runtime.component_ids()
            if not self._component_enabled(component)
        }

        disabled = set(requested_disabled)

        changed = True

        while changed:
            changed = False

            for component in self.runtime.component_ids():
                if component in disabled:
                    continue

                dependencies = self.runtime.get_dependencies(component)

                if any(dep in disabled for dep in dependencies):
                    disabled.add(component)
                    changed = True

        for component in self.runtime.component_ids():
            self.runtime.set_enabled(
                component,
                component not in disabled,
            )

        self._disabled_components = disabled

        if disabled:
            self.logger.warning(
                "Components disabled by configuration: "
                + ", ".join(sorted(disabled))
            )

    def _component_enabled(self, component_id: str) -> bool:
        """Return whether a component is enabled by configuration."""

        if self.configuration.has(
            f"components.{component_id}.enabled"
        ):
            value = self.configuration.get(
                f"components.{component_id}.enabled"
            )

            if isinstance(value, bool):
                return value

        if self.configuration.has(f"{component_id}.enabled"):
            value = self.configuration.get(f"{component_id}.enabled")

            if isinstance(value, bool):
                return value

        return True

    def _deactivate_disabled_components(self) -> None:
        """Stop subsystems whose components are disabled by configuration."""

        if "communication" in self._disabled_components:
            self.communication.stop()

        if "events" in self._disabled_components:
            self.events.stop()

        if "security" in self._disabled_components:
            self.security.stop()

    def _service_enabled(self, service_id: str) -> bool:
        """Return whether a service's subsystem is enabled."""

        if service_id in self.runtime.component_ids():
            return self.runtime.is_enabled(service_id)

        return True

    def _initialize_configuration(self) -> None:
        """Activate the configuration manager."""

        self.configuration.start()

    def _initialize_logging(self) -> None:
        """Apply configuration-controlled logging behavior."""

        if not self.configuration.is_running:
            return

        if not self.configuration.has("logging.level"):
            return

        level = self.configuration.get("logging.level")

        mapping = {
            "DEBUG": 10,
            "INFO": 20,
            "WARNING": 30,
            "ERROR": 40,
            "CRITICAL": 50,
        }

        resolved = mapping.get(str(level).upper())

        if resolved is None:
            self.logger.warning(
                f"Unknown logging level: {level}"
            )
            return

        self.logger.set_level(resolved)

    def _initialize_security(self) -> None:
        """Activate the security infrastructure and bridge its events."""

        self.security.start()

        self.security.register_event_handler(
            self._bridge_security_event
        )

    def _initialize_resources(self) -> None:
        """
        Activate the resource infrastructure.

        Resources are intentionally empty at startup — they are populated
        via the RESOURCES.REGISTER service or the ResourceRegistry API.
        This explicit no-op ensures lifecycle symmetry with
        _shutdown_resources which clears on stop, while preserving the
        registry→organization attachment across restarts.
        """

        self.logger.info("Resources ready — registry empty, awaiting registrations.")

    def _initialize_organization(self) -> None:
        """
        Activate the organization infrastructure.

        Organization entries are derived from registered resources via
        ResourceRegistry auto-categorization. No pre-population is required;
        entries are created on ResourceRegistry.register and cleared on
        _shutdown_organization. Attachment to the registry survives restarts.
        """

        # Ensure attachment survives any prior clear/restart cycle.
        try:
            self.resources.attach_organization(self.organization)
        except Exception:
            pass
        self.logger.info("Organization ready — awaiting resource categorizations.")

    def _initialize_events(self) -> None:
        """Activate the event subsystem and attach event consumers."""

        self.events.start()
        self._attach_event_consumers()

    def _initialize_communication(self) -> None:
        """Activate the communication subsystem."""

        self.communication.start()

    def _initialize_routing(self) -> None:
        """Activate the routing subsystem and register service routes."""

        self.routing.start()
        self._register_service_routes()

    def _initialize_health(self) -> None:
        """Activate health monitoring and register component checks."""

        self.health.start()
        self._register_health_checks()

    def _initialize_rescs(self) -> None:
        """Activate the R.E.S.C.S. persistence adapter."""

        # Adapter selection already happened in _apply_rescs_policy during
        # _load_configuration. Here we just verify health and log.
        try:
            health = self.rescs.health()
            adapter = health.get("adapter", "unknown")
            self.logger.info(f"R.E.S.C.S. adapter ready: {adapter}")
        except Exception as exc:
            self.logger.warning(f"R.E.S.C.S. health check failed: {exc}")

    def _initialize_dependencies(self) -> None:
        """Mark the dependency registry as active."""

        self.dependencies.validate()

    def _initialize_services(self) -> None:
        """Start C.O.R.E. internal services and expose their operations."""

        self._register_service_capabilities()
        self._register_service_endpoints()

        for service in self.services.list():
            if not self._service_enabled(service.service_id):
                continue

            try:
                self.services.start(service.service_id)
            except Exception as exc:
                self.logger.error(
                    f"Failed to start internal service "
                    f"'{service.service_id}': {exc}"
                )
                raise

    def _register_service_capabilities(self) -> None:
        """Register real operation handlers for internal services."""

        self.services.register_handler(
            "resources",
            "list",
            lambda: {
                "resources": [
                    {
                        "id": resource.resource_id,
                        "name": resource.name,
                        "type": resource.resource_type,
                        "status": resource.status,
                    }
                    for resource in self.resources
                ]
            },
        )

        self.services.register_handler(
            "resources",
            "get",
            lambda resource_id: {
                "resource": self._resource_snapshot(
                    self.resources.get(resource_id)
                )
            },
        )

        self.services.register_handler(
            "resources",
            "register",
            lambda resource_id, name, resource_type, **kwargs: (
                self._register_resource(
                    resource_id=resource_id,
                    name=name,
                    resource_type=resource_type,
                    **kwargs,
                )
            ),
        )

        self.services.register_handler(
            "resources",
            "discover",
            lambda **kwargs: {
                "resources": [
                    self._resource_snapshot(resource)
                    for resource in self.resources.discover(**kwargs)
                ]
            },
        )

        self.services.register_handler(
            "resources",
            "update",
            lambda resource_id, **kwargs: {
                "resource": self._resource_snapshot(
                    self._update_resource(resource_id, **kwargs)
                )
            },
        )

        self.services.register_handler(
            "resources",
            "remove",
            lambda resource_id: self._remove_resource(resource_id),
        )

        self.services.register_handler(
            "resources",
            "category",
            lambda resource_id: {
                "organization_entries": [
                    {
                        "id": entry.entry_id,
                        "category": entry.category,
                        "name": entry.name,
                        "resource_id": entry.resource_id,
                    }
                    for entry in self.organization.by_resource(resource_id)
                ]
            },
        )

        self.services.register_handler(
            "organization",
            "list",
            lambda: {
                "entries": [
                    {
                        "id": entry.entry_id,
                        "category": entry.category,
                        "name": entry.name,
                        "resource_id": entry.resource_id,
                    }
                    for entry in self.organization
                ]
            },
        )

        self.services.register_handler(
            "organization",
            "by_category",
            lambda category: {
                "entries": [
                    {
                        "id": entry.entry_id,
                        "category": entry.category,
                        "name": entry.name,
                        "resource_id": entry.resource_id,
                    }
                    for entry in self.organization.by_category(category)
                ]
            },
        )

        self.services.register_handler(
            "routing",
            "routes",
            lambda: {"count": self.routing.count()},
        )

        self.services.register_handler(
            "health",
            "status",
            lambda: {
                "checks": [
                    {
                        "component_id": result.component_id,
                        "status": result.status.value,
                        "message": result.message,
                    }
                    for result in self.health.check_all()
                ],
                "overall": self.health.overall_status().value,
            },
        )

        self.services.register_handler(
            "health",
            "overall",
            lambda: {"overall": self.health.overall_status().value},
        )

        self.services.register_handler(
            "communication",
            "status",
            lambda: {
                "running": self.communication.is_running,
                "endpoints": self.communication.endpoint_count(),
                "messages": self.communication.message_count(),
            },
        )

        self.services.register_handler(
            "events",
            "status",
            lambda: {
                "running": self.events.is_running,
                "published": self.events.event_count(),
                "subscribers": self.events.subscriber_count(),
            },
        )

        self.services.register_handler(
            "runtime",
            "history",
            lambda: {
                "records": [
                    r.to_dict() for r in self.runtime_history.list_all()
                ]
            },
        )

        self.services.register_handler(
            "runtime",
            "active",
            lambda: {
                "records": [
                    r.to_dict() for r in self.runtime_history.list_active()
                ]
            },
        )

        self.services.register_handler(
            "runtime",
            "completed",
            lambda: {
                "records": [
                    r.to_dict() for r in self.runtime_history.list_completed()
                ]
            },
        )

        self.services.register_handler(
            "runtime",
            "get",
            lambda entity_id: {
                "record": self.runtime_history.get(entity_id).to_dict()
            },
        )

        self.services.register_handler(
            "runtime",
            "version",
            lambda client_version=None, **kwargs: {
                "version": CORE_VERSION,
                "supported": SUPPORTED_VERSIONS,
                "legacy": LEGACY_VERSIONS,
                "negotiated": negotiate(client_version),
                "is_legacy": is_legacy(client_version or CORE_VERSION),
            },
        )

        self.services.register_handler(
            "rescs",
            "list",
            lambda: {
                "resources": [
                    r.to_dict() for r in self.rescs.list_resources()
                ]
            },
        )

        self.services.register_handler(
            "rescs",
            "get",
            lambda resource_id: {
                "resource": (
                    self.rescs.fetch_resource(resource_id).to_dict()
                    if self.rescs.fetch_resource(resource_id) is not None
                    else None
                )
            },
        )

        self.services.register_handler(
            "rescs",
            "health",
            lambda: self.rescs.health(),
        )

        self.services.register_handler(
            "rescs",
            "runtimes",
            lambda: {
                "records": [
                    r.to_dict() for r in self.rescs.list_runtimes()
                ]
            },
        )

        # Agent scheduler — capability-driven assignment for low-capability devices
        self.services.register_handler(
            "agent",
            "profiles",
            lambda: {
                "profiles": [
                    p.to_dict() for p in self.scheduler.list_profiles()
                ]
            },
        )
        self.services.register_handler(
            "agent",
            "assignments",
            lambda: {
                "assignments": [
                    a.to_dict() for a in self.scheduler.list_assignments()
                ]
            },
        )
        self.services.register_handler(
            "agent",
            "list",
            lambda: {
                "agents": [
                    r.to_dict() for r in self.scheduler.list_agents()
                ]
            },
        )
        self.services.register_handler(
            "agent",
            "get",
            lambda device_id: {
                "assignment": self.scheduler.get_assignment(device_id).to_dict(),
                "agent": self.scheduler.get_agent(
                    self.scheduler.get_assignment(device_id).agent_id
                ).to_dict(),
            },
        )
        self.services.register_handler(
            "agent",
            "status",
            lambda: {
                "profiles": len(self.scheduler.list_profiles()),
                "assignments": self.scheduler.assignment_count(),
                "agents": len(self.scheduler.list_agents()),
            },
        )
        self.services.register_handler(
            "agent",
            "assign",
            lambda device_id, profile_id=None, **kwargs: self._assign_agent(
                device_id=device_id, profile_id=profile_id
            ),
        )
        self.services.register_handler(
            "agent",
            "release",
            lambda device_id: self._release_agent(device_id=device_id),
        )

    def _register_service_endpoints(self) -> None:
        """Register transport endpoints that dispatch to services."""

        for service in self.services.list():
            if not self._service_enabled(service.service_id):
                continue

            endpoint = self.dispatcher.endpoint_for(service.service_id)

            if self.communication.has_endpoint(endpoint):
                continue

            self.communication.register(endpoint, self.dispatcher.handle)

    def _register_service_routes(self) -> None:
        """Register routes from application message types to services."""

        routes = {
            "RESOURCES.LIST": "service:resources",
            "HEALTH.STATUS": "service:health",
            "ORGANIZATION.LIST": "service:organization",
            "ROUTING.ROUTES": "service:routing",
            "COMMUNICATION.STATUS": "service:communication",
        }

        for message_type, destination in routes.items():
            if not self.routing.has_route(message_type):
                self.routing.add_route(message_type, destination)

    def _register_resource(
        self,
        resource_id: str,
        name: str,
        resource_type: str,
        **kwargs,
    ) -> dict:
        """Register a resource and return its snapshot."""

        resource = Resource(
            resource_id=resource_id,
            name=name,
            resource_type=resource_type,
            **kwargs,
        )

        self.resources.register(resource)

        self._emit_resource_event(RESOURCE_REGISTERED, resource)
        self._track_resource_start(resource)

        return {"resource": self._resource_snapshot(resource)}

    def _remove_resource(self, resource_id: str) -> dict:
        """Remove a resource and publish a removal event."""

        # If a device with an agent assignment is removed, release the agent first
        try:
            candidate = self.resources.get(resource_id)
            if candidate.resource_type == "device":
                try:
                    self._release_agent(device_id=resource_id)
                except Exception:
                    pass
        except Exception:
            pass

        resource = self.resources.unregister(resource_id)

        self._emit_resource_event(RESOURCE_REMOVED, resource)
        self._track_resource_end(resource)

        return {"removed": resource.resource_id}

    def _update_resource(self, resource_id: str, **kwargs) -> Resource:
        """Update a resource and track status changes."""

        resource = self.resources.update(resource_id, **kwargs)

        # If status changed, treat as potential runtime status transition
        if "status" in kwargs:
            self._emit_resource_event(RESOURCE_REGISTERED, resource)
            # For device/agent, maintain runtime history on status
            if resource.resource_type == "device":
                if kwargs["status"] in ("online", "connected", "running"):
                    # Ensure tracking is active
                    try:
                        self.runtime_history.get(resource.resource_id)
                    except KeyError:
                        self._track_resource_start(resource)
                elif kwargs["status"] in ("offline", "disconnected", "stopped"):
                    self._track_resource_end(resource)
            elif resource.resource_type == "agent":
                if kwargs["status"] == "running":
                    try:
                        self.runtime_history.get(resource.resource_id)
                    except KeyError:
                        self._track_resource_start(resource)
                elif kwargs["status"] in ("stopped", "failed"):
                    self._track_resource_end(resource)

        return resource

    def _track_resource_start(self, resource: Resource) -> None:
        """Start runtime history tracking for device/agent resources."""

        try:
            if resource.resource_type == "device":
                record = self.runtime_history.start(
                    entity_id=resource.resource_id,
                    entity_type=EntityType.DEVICE,
                    metadata={
                        "name": resource.name,
                        "status": resource.status,
                        "capabilities": list(resource.capabilities),
                        **dict(resource.metadata),
                    },
                )
                if self.events.is_running:
                    self.events.emit(
                        event_type=DEVICE_CONNECTED,
                        source="resources",
                        payload={
                            "resource_id": resource.resource_id,
                            "device_type": resource.metadata.get(
                                "device_type", resource.resource_type
                            ),
                        },
                    )
                self._persist_to_rescs(resource, record)
            elif resource.resource_type == "agent":
                record = self.runtime_history.start(
                    entity_id=resource.resource_id,
                    entity_type=EntityType.AGENT,
                    metadata={
                        "name": resource.name,
                        "status": resource.status,
                        **dict(resource.metadata),
                    },
                )
                if self.events.is_running:
                    self.events.emit(
                        event_type=AGENT_STARTED,
                        source="resources",
                        payload={
                            "resource_id": resource.resource_id,
                            "agent_type": resource.metadata.get(
                                "agent_type", "local"
                            ),
                        },
                    )
                self._persist_to_rescs(resource, record)
            else:
                # Generic service/connection tracking
                record = self.runtime_history.start(
                    entity_id=resource.resource_id,
                    entity_type=resource.resource_type,
                    metadata={
                        "name": resource.name,
                        "status": resource.status,
                        **dict(resource.metadata),
                    },
                )
                self._persist_to_rescs(resource, record)
        except Exception:
            # History tracking must never break resource registration
            pass

    def _track_resource_end(self, resource: Resource) -> None:
        """Complete runtime history tracking for a resource."""

        try:
            # Attempt to end history; ignore if no record
            record = None
            try:
                record = self.runtime_history.end(
                    entity_id=resource.resource_id,
                    metadata={
                        "name": resource.name,
                        "final_status": resource.status,
                    },
                )
            except KeyError:
                pass

            # Persist updated runtime record and resource deletion
            try:
                # For delete, still attempt to persist final state then remove
                if record is not None:
                    try:
                        self.rescs.persist_runtime(record)
                    except Exception as exc:
                        self.logger.warning(
                            f"R.E.S.C.S. runtime persist failed: {exc}"
                        )
                try:
                    self.rescs.delete_resource(resource.resource_id)
                except Exception as exc:
                    self.logger.warning(
                        f"R.E.S.C.S. resource delete failed: {exc}"
                    )
            except Exception:
                pass

            if resource.resource_type == "device" and self.events.is_running:
                self.events.emit(
                    event_type=DEVICE_DISCONNECTED,
                    source="resources",
                    payload={"resource_id": resource.resource_id},
                )
            elif resource.resource_type == "agent" and self.events.is_running:
                self.events.emit(
                    event_type=AGENT_STOPPED,
                    source="resources",
                    payload={"resource_id": resource.resource_id},
                )
        except Exception:
            pass

    def _persist_to_rescs(self, resource: Resource, record) -> None:
        """Persist resource and runtime record to R.E.S.C.S. with isolation."""

        try:
            self.rescs.persist_resource(resource)
        except Exception as exc:
            self.logger.warning(f"R.E.S.C.S. persist resource failed: {exc}")

        try:
            self.rescs.persist_runtime(record)
        except Exception as exc:
            self.logger.warning(f"R.E.S.C.S. persist runtime failed: {exc}")

    def _assign_agent(self, device_id: str, profile_id: str | None = None) -> dict:
        """Assign a device to a suitable agent via the scheduler."""

        device = self.resources.get(device_id)

        if device.resource_type != "device":
            raise ValueError(f"Resource is not a device: {device_id}")

        # If already assigned and caller forces a different profile, reassign (preserve legacy idempotent otherwise)
        try:
            existing_assignment = self.scheduler.get_assignment(device_id)
            if existing_assignment is not None and profile_id is not None and existing_assignment.profile_id != profile_id:
                # Explicit profile override — release old assignment first (legacy explicit wins over auto)
                try:
                    self._release_agent(device_id=device_id)
                except Exception:
                    pass
        except Exception:
            pass

        agent, assignment = self.scheduler.assign(device, profile_id=profile_id)

        # Register the agent resource so it is discoverable via resources
        try:
            self.resources.register(agent)
            self._emit_resource_event(RESOURCE_REGISTERED, agent)
            self._track_resource_start(agent)
        except Exception:
            # Already registered — update in place
            try:
                existing = self.resources.get(agent.resource_id)
                existing.status = agent.status
                existing.metadata = dict(agent.metadata)
                existing.capabilities = list(agent.capabilities)
            except Exception:
                pass

        # Record assignment on device metadata
        try:
            device.metadata["assigned_agent"] = agent.resource_id
        except Exception:
            pass

        if self.events.is_running:
            self.events.emit(
                event_type=AGENT_STARTED,
                source="agent",
                payload={
                    "device_id": device_id,
                    "agent_id": agent.resource_id,
                    "profile_id": assignment.profile_id,
                },
            )

        return {
            "agent": self._resource_snapshot(agent),
            "assignment": assignment.to_dict(),
        }

    def _release_agent(self, device_id: str) -> dict:
        """Release the agent assigned to a device."""

        assignment = self.scheduler.release(device_id)

        # Unregister the agent resource
        try:
            agent_res = self.resources.get(assignment.agent_id)
            self.resources.unregister(assignment.agent_id)
            self._emit_resource_event(RESOURCE_REMOVED, agent_res)
            self._track_resource_end(agent_res)
        except Exception:
            pass

        # Clear assignment from device
        try:
            device = self.resources.get(device_id)
            device.metadata.pop("assigned_agent", None)
        except Exception:
            pass

        if self.events.is_running:
            self.events.emit(
                event_type=AGENT_STOPPED,
                source="agent",
                payload={
                    "device_id": device_id,
                    "agent_id": assignment.agent_id,
                    "profile_id": assignment.profile_id,
                },
            )

        return {"released": assignment.to_dict()}

    @staticmethod
    def _resource_snapshot(resource: Resource) -> dict:
        """Return a serializable snapshot of a resource."""

        return {
            "id": resource.resource_id,
            "name": resource.name,
            "type": resource.resource_type,
            "status": resource.status,
            "owner": resource.owner,
            "source": resource.source,
            "capabilities": list(resource.capabilities),
            "metadata": dict(resource.metadata),
        }

    def _initialize(self) -> None:
        """Initialize the complete C.O.R.E. application."""

        self.logger.info("C.O.R.E. initialized.")

        if not self.events.is_running:
            return

        for component in self.runtime.initialized_components():
            self.events.emit(
                event_type=COMPONENT_STARTED,
                source="core",
                payload={"component": component},
            )

    def _emit_message_sent(self, message) -> None:
        """Publish a communication event for a delivered message."""

        if not self.events.is_running:
            return

        self.events.emit(
            event_type=MESSAGE_SENT,
            source=message.source,
            payload={
                "destination": message.destination,
                "message_type": message.message_type,
                "message_id": message.message_id,
            },
        )

    def _emit_service_event(self, event_type: str, source: str, payload: dict) -> None:
        """Publish a service lifecycle/execution event."""

        if not self.events.is_running:
            return

        self.events.emit(
            event_type=event_type,
            source=source,
            payload=payload,
        )

    def _emit_resource_event(self, event_type: str, resource: Resource) -> None:
        """Publish a resource lifecycle event."""

        if not self.events.is_running:
            return

        self.events.emit(
            event_type=event_type,
            source="resources",
            payload={"resource_id": resource.resource_id},
        )

    def _emit_health_changed(self, result) -> None:
        """Publish a health change event for a component status transition."""

        if not self.events.is_running:
            return

        self.events.emit(
            event_type=HEALTH_CHANGED,
            source="health",
            payload={
                "component_id": result.component_id,
                "status": result.status.value,
            },
        )

    def _bridge_security_event(self, event: dict) -> None:
        """Bridge SecurityManager observer events onto the event bus."""

        if not self.events.is_running:
            return

        self.events.emit(
            event_type=event["event_type"],
            source="security",
            payload={
                "identity_id": event["identity_id"],
                "success": event["success"],
            },
        )

    def _attach_event_consumers(self) -> None:
        """Subscribe C.O.R.E. consumers to event bus events."""

        self.events.subscribe(
            "SYSTEM_STARTED",
            self._consume_system_started,
        )

        self.events.subscribe(
            "SYSTEM_STOPPED",
            self._consume_system_stopped,
        )

        self.events.subscribe(
            "SERVICE_FAILED",
            self._consume_service_failed,
        )

        self.events.subscribe(
            "SECURITY_ACCESS_DENIED",
            self._consume_security_denied,
        )

        self.events.subscribe(
            DEVICE_CONNECTED,
            self._handle_device_connected,
        )

        self.events.subscribe(
            DEVICE_DISCONNECTED,
            self._handle_device_disconnected,
        )

    def _consume_security_denied(self, event) -> None:
        """Log a rejected security boundary crossing."""

        self.logger.warning(
            "Security access denied: "
            f"identity={event.payload.get('identity_id')} "
            f"service={event.payload.get('service_id')} "
            f"operation={event.payload.get('operation')}."
        )

    def _consume_system_started(self, event) -> None:
        """React to application startup by refreshing health."""

        self.health.check_all()

    def _consume_system_stopped(self, event) -> None:
        """React to application shutdown."""

        self.logger.info("System stopped event consumed.")

    def _consume_service_failed(self, event) -> None:
        """Log a service failure event."""

        self.logger.error(
            f"Service event failure: "
            f"{event.payload.get('service_id')}."
        )

    def _handle_device_connected(self, event) -> None:
        """
        Auto-assign a device to a suitable agent on connect.

        Preserves legacy explicit `agent.assign` — this handler is idempotent
        and only assigns when no assignment exists. Auto-assign is
        **opt-in** via `agent.auto_assign=true` to keep legacy explicit flow
        as default (see `config/core.yaml`). Errors are isolated so a
        missing profile never breaks device registration.
        """
        try:
            # Config gate — legacy explicit flow is default; auto-assign is opt-in
            auto_enabled = False
            try:
                if self.configuration.is_running and self.configuration.has("agent.auto_assign"):
                    val = self.configuration.get("agent.auto_assign")
                    if isinstance(val, bool):
                        auto_enabled = val
                    elif isinstance(val, str):
                        auto_enabled = val.strip().lower() == "true"
            except Exception:
                auto_enabled = False

            if not auto_enabled:
                return

            device_id = (event.payload or {}).get("resource_id") or (event.payload or {}).get("device_id")
            if not device_id:
                return

            # Idempotent: already assigned → nothing to do (preserves legacy explicit flow)
            try:
                self.scheduler.get_assignment(device_id)
                return
            except Exception:
                pass

            # Fetch device resource; if not found, skip
            try:
                device = self.resources.get(device_id)
            except Exception:
                return

            if device.resource_type != "device":
                return

            # Optional profile hint from config: agent.auto_assign.profile_id
            profile_hint = None
            try:
                if self.configuration.is_running and self.configuration.has("agent.auto_assign.profile_id"):
                    hint = self.configuration.get("agent.auto_assign.profile_id")
                    if isinstance(hint, str) and hint.strip():
                        profile_hint = hint.strip()
            except Exception:
                profile_hint = None

            # Attempt assignment — reuse existing _assign_agent path for consistency
            try:
                self._assign_agent(device_id=device_id, profile_id=profile_hint)
                self.logger.info(f"Auto-assigned agent for device {device_id} (profile={profile_hint or 'auto'})")
            except Exception as exc:
                self.logger.warning(f"Auto-assign failed for device {device_id}: {exc}")
        except Exception:
            # Never let auto-assign break the event bus
            pass

    def _handle_device_disconnected(self, event) -> None:
        """
        Clean up assignment on disconnect when auto-assign is active.

        This is a no-op if the device was already released via explicit
        `agent.release` or `resources.remove` (which also releases). Keeps
        legacy behavior intact: disconnect does not force release if
        `agent.auto_assign` is false and caller manages lifecycle manually.
        """
        try:
            device_id = (event.payload or {}).get("resource_id") or (event.payload or {}).get("device_id")
            if not device_id:
                return
            # Only auto-cleanup if auto_assign is enabled; otherwise preserve manual lifecycle
            auto_enabled = False
            try:
                if self.configuration.is_running and self.configuration.has("agent.auto_assign"):
                    val = self.configuration.get("agent.auto_assign")
                    if isinstance(val, bool):
                        auto_enabled = val
                    elif isinstance(val, str):
                        auto_enabled = val.strip().lower() == "true"
            except Exception:
                auto_enabled = False
            if not auto_enabled:
                return

            # If assignment exists, release it — _release_agent is idempotent-safe
            try:
                self.scheduler.get_assignment(device_id)
            except Exception:
                return
            try:
                self._release_agent(device_id=device_id)
                self.logger.info(f"Auto-released agent for device {device_id} on disconnect")
            except Exception as exc:
                self.logger.warning(f"Auto-release failed for device {device_id}: {exc}")
        except Exception:
            pass

    def _register_health_checks(self) -> None:
        checks = {
            "runtime": self._check_runtime,
            "configuration": self._check_configuration,
            "logging": self._check_logging,
            "security": self._check_security,
            "resources": self._check_resources,
            "organization": self._check_organization,
            "events": self._check_events,
            "communication": self._check_communication,
            "routing": self._check_routing,
            "devices": self._check_devices,
            "health": self._check_health,
            "rescs": self._check_rescs,
            "services": self._check_services,
            "agent": self._check_agent,
        }

        for component_id, check in checks.items():
            if not self._service_enabled(component_id):
                continue

            self.health.register(component_id, check)

    def _check_runtime(self) -> HealthResult:
        healthy = self.runtime.is_running

        return HealthResult(
            component_id="runtime",
            status=(
                HealthStatus.HEALTHY
                if healthy
                else HealthStatus.UNHEALTHY
            ),
            message=(
                "Runtime is running."
                if healthy
                else "Runtime is not running."
            ),
        )

    def _check_configuration(self) -> HealthResult:
        healthy = self.configuration.is_running

        return HealthResult(
            component_id="configuration",
            status=(
                HealthStatus.HEALTHY
                if healthy
                else HealthStatus.UNHEALTHY
            ),
            message=(
                "Configuration manager is running."
                if healthy
                else "Configuration manager is not running."
            ),
        )

    def _check_logging(self) -> HealthResult:
        return HealthResult(
            component_id="logging",
            status=HealthStatus.HEALTHY,
            message="Logging is available.",
        )

    def _check_security(self) -> HealthResult:
        healthy = self.security.is_running

        return HealthResult(
            component_id="security",
            status=(
                HealthStatus.HEALTHY
                if healthy
                else HealthStatus.UNHEALTHY
            ),
            message=(
                "Security manager is running."
                if healthy
                else "Security manager is not running."
            ),
        )

    def _check_resources(self) -> HealthResult:
        return HealthResult(
            component_id="resources",
            status=HealthStatus.HEALTHY,
            message=f"{self.resources.count()} resources registered.",
        )

    def _check_organization(self) -> HealthResult:
        return HealthResult(
            component_id="organization",
            status=HealthStatus.HEALTHY,
            message=f"{self.organization.count()} organization entries.",
        )

    def _check_events(self) -> HealthResult:
        healthy = self.events.is_running

        return HealthResult(
            component_id="events",
            status=(
                HealthStatus.HEALTHY
                if healthy
                else HealthStatus.UNHEALTHY
            ),
            message=(
                "Event bus is running."
                if healthy
                else "Event bus is not running."
            ),
        )

    def _check_communication(self) -> HealthResult:
        if not self.communication.is_running:
            return HealthResult(
                component_id="communication",
                status=HealthStatus.UNHEALTHY,
                message="Communication subsystem is not running.",
            )

        return HealthResult(
            component_id="communication",
            status=HealthStatus.HEALTHY,
            message=(
                f"Communication online; "
                f"{self.communication.count()} channels."
            ),
        )

    def _check_routing(self) -> HealthResult:
        if not self.routing.is_running:
            return HealthResult(
                component_id="routing",
                status=HealthStatus.UNHEALTHY,
                message="Router is not running.",
            )

        return HealthResult(
            component_id="routing",
            status=HealthStatus.HEALTHY,
            message=f"Router online; {self.routing.count()} routes.",
        )

    def _check_health(self) -> HealthResult:
        return HealthResult(
            component_id="health",
            status=HealthStatus.HEALTHY,
            message="Health monitor is available.",
        )

    def _check_devices(self) -> HealthResult:
        try:
            registered = self.device_registry.registered_count()
            online = self.device_registry.online_count()
        except Exception:
            registered, online = 0, 0
        return HealthResult(
            component_id="devices",
            status=HealthStatus.HEALTHY,
            message=f"Devices registered={registered} online={online}.",
        )

    def device_metrics(self) -> dict:
        """Return device-layer observability, merging transport counters."""
        try:
            base = {
                "registered_devices": self.device_registry.registered_count(),
                "online_devices": self.device_registry.online_count(),
                "offline_devices": self.device_registry.offline_count(),
            }
        except Exception:
            base = {
                "registered_devices": 0,
                "online_devices": 0,
                "offline_devices": 0,
            }
        transport_metrics = {}
        metrics_fn = getattr(self.communication, "device_metrics", None)
        if callable(metrics_fn):
            try:
                transport_metrics = dict(metrics_fn())
            except Exception:
                transport_metrics = {}
        merged = dict(base)
        for key, value in transport_metrics.items():
            merged.setdefault(key, value)
        return merged

    def _data_reader_factory(self, owner_scope, allow_cross_owner=False):
        """Build an owner-scoped R.E.S.C.S. reader for the current adapter."""
        from core.data.rescs_reader import AdapterDataReader, HttpDataReader
        from core.rescs import HttpRescsAdapter

        adapter = self.rescs
        if isinstance(adapter, HttpRescsAdapter):
            endpoint = getattr(adapter, "_endpoint", None) or getattr(
                adapter, "endpoint", "http://localhost:8081"
            )
            timeout = getattr(adapter, "_timeout", 2.0) or 2.0
            return HttpDataReader(
                endpoint=endpoint,
                owner_scope=owner_scope,
                allow_cross_owner=allow_cross_owner,
                timeout=timeout,
            )
        return AdapterDataReader(
            adapter,
            owner_scope=owner_scope,
            allow_cross_owner=allow_cross_owner,
        )

    def data_metrics(self) -> dict:
        """Return data-layer observability, merging organizer counters."""
        try:
            metrics = dict(self.data_organizer.data_metrics())
        except Exception:
            metrics = {}
        transport_metrics = {}
        metrics_fn = getattr(self.communication, "data_metrics", None)
        if metrics_fn is None:
            metrics_fn = getattr(self.communication, "device_metrics", None)
        if callable(metrics_fn):
            try:
                transport_metrics = dict(metrics_fn())
            except Exception:
                transport_metrics = {}
        merged = dict(metrics)
        for key, value in transport_metrics.items():
            merged.setdefault(key, value)
        return merged

    def create_organization_ingestor(self):
        """Build a RESCS -> organization ingestor for the current adapter.

        The ingestor is constructed on demand so it always reads through
        the active R.E.S.C.S. adapter and organizes into the live
        registry/organization index. It holds no lifecycle of its own.
        """
        from core.organization.ingestion import ResourceIngestor

        return ResourceIngestor(
            self.rescs,
            self.resources,
            self.organization,
        )

    def _check_services(self) -> HealthResult:
        running = [
            service
            for service in self.services.list()
            if service.status.value == "running"
        ]

        healthy = len(running) == self.services.count()

        return HealthResult(
            component_id="services",
            status=(
                HealthStatus.HEALTHY
                if healthy
                else HealthStatus.DEGRADED
            ),
            message=(
                f"{len(running)}/{self.services.count()} "
                "services are running."
            ),
        )

    def _check_rescs(self) -> HealthResult:
        try:
            health = self.rescs.health()
            healthy = bool(health.get("healthy", True))
            adapter = health.get("adapter", "unknown")
            return HealthResult(
                component_id="rescs",
                status=(
                    HealthStatus.HEALTHY
                    if healthy
                    else HealthStatus.DEGRADED
                ),
                message=f"R.E.S.C.S. adapter={adapter}; healthy={healthy}",
            )
        except Exception as exc:
            return HealthResult(
                component_id="rescs",
                status=HealthStatus.UNHEALTHY,
                message=f"R.E.S.C.S. health failed: {exc}",
            )

    def _check_agent(self) -> HealthResult:
        try:
            profiles = self.scheduler.count()
            assignments = self.scheduler.assignment_count()
            return HealthResult(
                component_id="agent",
                status=HealthStatus.HEALTHY,
                message=f"Agent scheduler: {profiles} profiles, {assignments} assignments.",
            )
        except Exception as exc:
            return HealthResult(
                component_id="agent",
                status=HealthStatus.UNHEALTHY,
                message=f"Agent scheduler failed: {exc}",
            )

    def _shutdown_configuration(self) -> None:
        """Shutdown configuration infrastructure."""

        self.configuration.stop()

    def _shutdown_logging(self) -> None:
        """Shutdown logging infrastructure."""

    def _shutdown_security(self) -> None:
        self.security.stop()
        self.security.clear()

    def _shutdown_resources(self) -> None:
        try:
            self.scheduler.clear()
        except Exception:
            pass
        self.resources.clear()

    def _shutdown_organization(self) -> None:
        self.organization.clear()

    def _shutdown_events(self) -> None:
        self.events.stop()
        self.events.clear()

    def _shutdown_communication(self) -> None:
        self.communication.stop()
        self.communication.clear()

    def _shutdown_routing(self) -> None:
        self.routing.stop()
        self.routing.clear()

    def _shutdown_health(self) -> None:
        self.health.stop()
        self.health.clear()

    def _shutdown_rescs(self) -> None:
        # File adapter persists across stop; in-memory is cleared.
        # Keep file persistence to survive Windows restarts.
        if isinstance(self.rescs, InMemoryRescsAdapter):
            try:
                self.rescs.clear()
            except Exception:
                pass

    def _shutdown_dependencies(self) -> None:
        self.dependencies.clear()

    def _shutdown_services(self) -> None:
        self.services.clear()

    def _shutdown(self) -> None:
        """
        Perform final application shutdown.

        Component-specific cleanup is handled by the runtime in reverse
        dependency order. This method only performs final application-level
        logging and lifecycle notification.
        """

        try:
            self.events.emit(
                event_type=SYSTEM_STOPPED,
                source="core",
                payload={"state": "stopped"},
            )
        except Exception:
            pass

        self.logger.info("C.O.R.E. shutdown complete.")

    def start(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "C.O.R.E. application is not initialized."
            )

        if self.state == RuntimeState.RUNNING:
            return

        self._load_configuration()

        self.runtime.start()

        if not self.events.is_running:
            return

        self.events.emit(
            event_type=SYSTEM_STARTED,
            source="core",
            payload={"state": "running"},
        )

    def stop(self) -> None:
        core_initialized = "core" in self.runtime.initialized_components()

        self.runtime.stop()

        if not core_initialized and self.events.is_running:
            try:
                self.events.emit(
                    event_type=SYSTEM_STOPPED,
                    source="core",
                    payload={"state": "stopped"},
                )
            except Exception:
                pass

    def restart(self) -> None:
        self.stop()

        self._register_dependencies()
        self._register_internal_services()

        self.start()

    def health_check(self):
        return self.health.check_all()

    @property
    def state(self):
        return self.runtime.state

    @property
    def is_running(self) -> bool:
        return self.runtime.is_running
