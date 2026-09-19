import argparse
import time
import warnings
from pathlib import Path

from core.application import CoreApplication
from core.runtime import Runtime


def create_parser() -> argparse.ArgumentParser:
    """Create the C.O.R.E. command-line interface parser."""

    # Imported lazily to avoid circular import at module load
    try:
        from core.version import __version__ as _ver
    except Exception:
        _ver = "0.3.0"

    parser = argparse.ArgumentParser(
        prog="core",
        description="C.O.R.E. control interface.",
    )

    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to configuration YAML file (default: config/core.yaml if present).",
    )
    parser.add_argument(
        "--env",
        "--environment",
        dest="environment",
        type=str,
        default="development",
        help="Configuration environment (default: development).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"C.O.R.E. v{_ver}",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "status",
        help="Show C.O.R.E. runtime status.",
    )

    subparsers.add_parser(
        "start",
        help="Start C.O.R.E.",
    )

    subparsers.add_parser(
        "stop",
        help="Stop C.O.R.E.",
    )

    subparsers.add_parser(
        "services",
        help="Show registered C.O.R.E. services.",
    )

    subparsers.add_parser(
        "resources",
        help="Show registered C.O.R.E. resources.",
    )

    subparsers.add_parser(
        "connections",
        help="Show active communication endpoints.",
    )

    subparsers.add_parser(
        "health",
        help="Show C.O.R.E. health status.",
    )

    subparsers.add_parser(
        "agents",
        help="Show agent assignments (scheduler).",
    )

    provision_parser = subparsers.add_parser(
        "provision-device",
        help="Pre-provision an external-device identity + token (first-connect setup).",
    )
    provision_parser.add_argument("--device-id", required=True)
    provision_parser.add_argument("--device-name", default=None)
    provision_parser.add_argument("--device-type", default="generic")
    provision_parser.add_argument("--platform", default="unknown")
    provision_parser.add_argument(
        "--capabilities", default="", help="Comma-separated list."
    )
    provision_parser.add_argument(
        "--token",
        default=None,
        help="Device token (prompted securely when omitted; never logged).",
    )
    provision_parser.add_argument(
        "--permissions", default="read", help="Comma-separated list."
    )

    version_parser = subparsers.add_parser(
        "version",
        help="Show C.O.R.E. version and compatibility.",
    )
    version_parser.add_argument(
        "--client",
        type=str,
        default=None,
        help="Client version to negotiate (e.g., 0.2.1, 0.3.0).",
    )

    return parser


def _print_services(app: CoreApplication) -> None:
    """Print registered services and their lifecycle state."""

    print("Services:")

    services = app.services.list_services()

    if not services:
        print("  No services registered.")
        return

    for service in services:
        service_id = getattr(service, "service_id", str(service))
        name = getattr(service, "name", service_id)
        version = getattr(service, "version", "unknown")

        print(
            f"  {service_id} | {name} | v{version}"
        )


def _print_resources(app: CoreApplication) -> None:
    """Print registered resources."""

    print("Resources:")

    resources = app.resources.list_resources()

    if not resources:
        print("  No resources registered.")
        return

    for resource in resources:
        resource_id = getattr(
            resource,
            "resource_id",
            getattr(resource, "id", str(resource)),
        )

        resource_type = getattr(
            resource,
            "resource_type",
            getattr(resource, "type", "unknown"),
        )

        print(
            f"  {resource_id} | {resource_type}"
        )


def _print_connections(app: CoreApplication) -> None:
    """Print communication endpoint information."""

    print("Connections:")

    count = app.communication.count()

    print(
        f"  Registered endpoints: {count}"
    )

    if not app.communication.is_running:
        print("  Communication: STOPPED")
        return

    print("  Communication: RUNNING")


def _print_health(app: CoreApplication) -> None:
    """Run and print the current C.O.R.E. health state."""

    print("Health:")

    if not app.is_running:
        print("  Overall: NOT RUNNING")
        return

    results = app.health_check()
    overall = app.health.overall_status()

    print(
        f"  Overall: {overall.value.upper()}"
    )

    for result in results:
        print(
            f"  {result.component_id}: "
            f"{result.status.value.upper()} - "
            f"{result.message}"
        )


def _print_agents(app: CoreApplication) -> None:
    """Print agent scheduler assignments."""

    print("Agents:")

    try:
        assignments = app.scheduler.list_assignments()
        profiles = app.scheduler.list_profiles()
    except Exception as exc:
        print(f"  Scheduler unavailable: {exc}")
        return

    print(f"  Profiles: {len(profiles)}")
    for p in profiles:
        print(f"    {p.profile_id} | {p.name} | v{p.version} | host={p.host}")

    if not assignments:
        print("  No assignments.")
        return

    print(f"  Assignments: {len(assignments)}")
    for a in assignments:
        print(f"    {a.device_id} -> {a.agent_id} ({a.profile_id})")


def _print_version(app: CoreApplication, client_version: str | None = None) -> None:
    """Print version and negotiation (preserves legacy 0.2.1 output)."""

    from core.version import CORE_VERSION, LEGACY_VERSIONS, SUPPORTED_VERSIONS, negotiate

    negotiated = negotiate(client_version)
    print(f"C.O.R.E. v{CORE_VERSION}")
    print(f"Supported: {', '.join(SUPPORTED_VERSIONS)}")
    print(f"Legacy: {', '.join(LEGACY_VERSIONS)}")
    if client_version:
        print(f"Client: {client_version} → Negotiated: {negotiated}")
    else:
        print(f"Negotiated (no client): {negotiated}")
    # Also show app config version for parity
    try:
        cfg_ver = app.configuration.get("core.version", CORE_VERSION) if app.configuration.is_running else CORE_VERSION
        print(f"Config: {cfg_ver}")
    except Exception:
        pass


def execute(
    args: argparse.Namespace,
    runtime: Runtime,
) -> int:
    """
    Execute a CLI command against a Runtime.

    .. deprecated::
        Use ``execute_application`` with a ``CoreApplication`` instance.
        This shim is retained for backward compatibility and emits a
        DeprecationWarning. It will be removed in v0.4.0; migrate to
        ``execute_application`` before then.
    """

    warnings.warn(
        "core.cli.main.execute(runtime) is deprecated; use execute_application(app)",
        DeprecationWarning,
        stacklevel=2,
    )

    # Forward start/stop/status to runtime; other commands require CoreApplication
    if args.command == "start":
        runtime.start()
        print("C.O.R.E. started.")
        return 0

    if args.command == "stop":
        runtime.stop()
        print("C.O.R.E. stopped.")
        return 0

    if args.command == "status":
        print(
            f"Runtime: {runtime.state.value.upper()}"
        )
        return 0

    if args.command == "services":
        print("Services:")
        print(
            "  Service information requires CoreApplication "
            "(use: py -m core --config <path> services)."
        )
        return 0

    if args.command == "resources":
        print("Resources:")
        print(
            "  Resource information requires CoreApplication "
            "(use: py -m core --config <path> resources)."
        )
        return 0

    if args.command == "connections":
        print("Connections:")
        print(
            "  Connection information requires CoreApplication "
            "(use: py -m core --config <path> connections)."
        )
        return 0

    if args.command == "health":
        print("Health:")
        print(
            "  Health information requires CoreApplication "
            "(use: py -m core --config <path> health)."
        )
        return 0

    if args.command == "agents":
        print("Agents:")
        print(
            "  Agent information requires CoreApplication "
            "(use: py -m core --config <path> agents)."
        )
        return 0

    if args.command == "version":
        # Legacy runtime fallback — respect version negotiation even without app
        from core.version import CORE_VERSION, negotiate

        print(f"C.O.R.E. v{CORE_VERSION}")
        print(f"Negotiated: {negotiate(getattr(args, 'client', None))}")
        return 0

    print(
        f"ERROR: unknown command '{args.command}'. "
        "Run 'py -m core --help' for usage."
    )
    return 2


def execute_application(
    args: argparse.Namespace,
    app: CoreApplication,
) -> int:
    """
    Execute a CLI command against the complete C.O.R.E. application.
    """

    if args.command == "start":
        app.start()
        print("C.O.R.E. started.")
        return 0

    if args.command == "stop":
        app.stop()
        print("C.O.R.E. stopped.")
        return 0

    if args.command == "status":
        print(
            f"Runtime: {app.state.value.upper()}"
        )
        return 0

    if args.command == "services":
        _print_services(app)
        return 0

    if args.command == "resources":
        _print_resources(app)
        return 0

    if args.command == "connections":
        _print_connections(app)
        return 0

    if args.command == "health":
        _print_health(app)
        return 0

    if args.command == "agents":
        _print_agents(app)
        return 0

    if args.command == "version":
        _print_version(app, getattr(args, "client", None))
        return 0

    if args.command == "provision-device":
        import getpass

        token = getattr(args, "token", None)
        if not token:
            try:
                token = getpass.getpass(
                    f"Token for {getattr(args, 'device_id', 'device')}: "
                )
            except Exception:
                token = None
        if not token:
            print("ERROR: a non-empty token is required.")
            return 2
        capabilities = [
            c.strip()
            for c in str(getattr(args, "capabilities", "") or "").split(",")
            if c.strip()
        ]
        permissions = [
            p.strip()
            for p in str(getattr(args, "permissions", "") or "").split(",")
            if p.strip()
        ]
        try:
            snapshot = app.provision_device(
                device_id=args.device_id,
                token=token,
                device_name=getattr(args, "device_name", None),
                device_type=getattr(args, "device_type", "generic"),
                platform=getattr(args, "platform", "unknown"),
                capabilities=capabilities,
                permissions=permissions,
            )
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
        finally:
            token = None
        print(f"Provisioned device: {snapshot['device_id']}")
        print("Identity persisted (offline). Token is stored, never displayed.")
        return 0

    print(
        "ERROR: unknown command. Run 'py -m core --help' for usage; "
        "'start' runs the host (default when no command is given)."
    )
    return 2


def run_application(app: CoreApplication) -> int:
    """Run the real C.O.R.E. application until interrupted."""

    try:
        app.start()

        from core.version import __version__ as _run_ver

        print(f"C.O.R.E. v{_run_ver}")
        print("────────────────────────")
        print(
            f"Runtime: {app.state.value.upper()}"
        )
        print()
        print("C.O.R.E. is running.")
        print("Press Ctrl+C to shut down.")

        while app.is_running:
            time.sleep(1)

    except KeyboardInterrupt:
        print()
        print("C.O.R.E. shutting down...")

    finally:
        app.stop()

    print("C.O.R.E. stopped.")

    return 0


def main() -> int:
    """CLI entry point."""

    parser = create_parser()
    args = parser.parse_args()

    config_path = getattr(args, "config", None)
    environment = getattr(args, "environment", "development")

    # Resolve config path: explicit --config wins, else default resolution
    # inside CoreApplication.
    app = CoreApplication(
        config_path=Path(config_path) if config_path else None,
        environment=environment,
    )

    if args.command == "start" or args.command is None:
        # Default invocation (no subcommand) starts the long-running host
        # so `py -m core --config <file>` never exits silently.
        return run_application(app)

    return execute_application(args, app)


if __name__ == "__main__":
    raise SystemExit(main())