import pytest

from core.errors import (
    ServiceAlreadyRegistered,
    ServiceDependencyError,
    ServiceNotFound,
)
from core.services import Service, ServiceManager, ServiceStatus


def make_service(
    service_id: str,
    dependencies: list[str] | None = None,
    priority: int = 100,
) -> Service:
    return Service(
        service_id=service_id,
        name=service_id.title(),
        version="0.1.0",
        dependencies=dependencies or [],
        startup_priority=priority,
    )


def test_service_defaults():
    service = make_service("database")

    assert service.service_id == "database"
    assert service.name == "Database"
    assert service.version == "0.1.0"
    assert service.dependencies == []
    assert service.startup_priority == 100
    assert service.status == ServiceStatus.REGISTERED
    assert service.health == "unknown"


def test_service_health():
    service = make_service("database")

    service.mark_healthy()
    assert service.health == "healthy"

    service.mark_unhealthy()
    assert service.health == "unhealthy"


def test_register_service():
    manager = ServiceManager()
    service = make_service("database")

    assert manager.register(service) is service
    assert manager.get("database") is service
    assert manager.count() == 1


def test_duplicate_service():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)

    with pytest.raises(ServiceAlreadyRegistered):
        manager.register(service)


def test_missing_service():
    manager = ServiceManager()

    with pytest.raises(ServiceNotFound):
        manager.get("missing")


def test_initialize_service():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)
    manager.initialize("database")

    assert service.status == ServiceStatus.STOPPED


def test_start_service():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)
    manager.start("database")

    assert service.status == ServiceStatus.RUNNING
    assert service.health == "healthy"


def test_stop_service():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)
    manager.start("database")
    manager.stop("database")

    assert service.status == ServiceStatus.STOPPED
    assert service.health == "unknown"


def test_restart_service():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)
    manager.start("database")
    manager.restart("database")

    assert service.status == ServiceStatus.RUNNING
    assert service.health == "healthy"


def test_health_check():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)

    assert manager.health_check("database") == "unhealthy"

    manager.start("database")

    assert manager.health_check("database") == "healthy"


def test_missing_dependency():
    manager = ServiceManager()

    service = make_service(
        "memory",
        dependencies=["database"],
    )

    manager.register(service)

    with pytest.raises(ServiceDependencyError):
        manager.start("memory")


def test_dependency_starts_before_service():
    manager = ServiceManager()

    database = make_service("database", priority=10)
    memory = make_service(
        "memory",
        dependencies=["database"],
        priority=20,
    )

    manager.register(database)
    manager.register(memory)

    manager.start("memory")

    assert database.status == ServiceStatus.RUNNING
    assert memory.status == ServiceStatus.RUNNING


def test_dependency_stops_after_dependent():
    manager = ServiceManager()

    database = make_service("database")
    memory = make_service(
        "memory",
        dependencies=["database"],
    )

    manager.register(database)
    manager.register(memory)

    manager.start("memory")
    manager.stop("database")

    assert memory.status == ServiceStatus.STOPPED
    assert database.status == ServiceStatus.STOPPED


def test_circular_dependency():
    manager = ServiceManager()

    service_a = make_service("a", dependencies=["b"])
    service_b = make_service("b", dependencies=["a"])

    manager.register(service_a)
    manager.register(service_b)

    with pytest.raises(ServiceDependencyError):
        manager.start("a")


def test_unregister_service():
    manager = ServiceManager()
    service = make_service("database")

    manager.register(service)
    manager.unregister("database")

    assert manager.count() == 0

    with pytest.raises(ServiceNotFound):
        manager.get("database")


def test_list_services():
    manager = ServiceManager()

    manager.register(make_service("database"))
    manager.register(make_service("memory"))

    services = manager.list()

    assert len(services) == 2
    assert services[0].service_id == "database"
    assert services[1].service_id == "memory"
