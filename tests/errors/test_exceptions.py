import pytest

from core.errors import (
    CircularDependencyError,
    CommunicationError,
    ConfigurationError,
    ConfigurationNotLoaded,
    CoreError,
    DependencyError,
    HealthError,
    InitializationError,
    InvalidConfiguration,
    MessageError,
    ResourceAlreadyRegistered,
    ResourceError,
    ResourceNotFound,
    RoutingError,
    ServiceAlreadyRegistered,
    ServiceDependencyError,
    ServiceError,
    ServiceNotFound,
    ShutdownError,
)


@pytest.mark.parametrize(
    "exception",
    [
        ConfigurationError,
        ConfigurationNotLoaded,
        InvalidConfiguration,
        ServiceError,
        ServiceNotFound,
        ServiceAlreadyRegistered,
        ServiceDependencyError,
        ResourceError,
        ResourceNotFound,
        ResourceAlreadyRegistered,
        CommunicationError,
        MessageError,
        RoutingError,
        HealthError,
        DependencyError,
        CircularDependencyError,
        InitializationError,
        ShutdownError,
    ],
)
def test_core_exceptions_inherit_from_core_error(exception):
    assert issubclass(exception, CoreError)


def test_invalid_configuration_message():
    error = InvalidConfiguration("Invalid core configuration")

    assert str(error) == "Invalid core configuration"


def test_service_not_found_message():
    error = ServiceNotFound("Service not found: database")

    assert str(error) == "Service not found: database"


def test_resource_not_found_message():
    error = ResourceNotFound("Resource not found: rovert-main")

    assert str(error) == "Resource not found: rovert-main"


def test_circular_dependency_error():
    error = CircularDependencyError(
        "Circular dependency detected"
    )

    assert str(error) == "Circular dependency detected"
