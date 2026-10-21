class CoreError(Exception):
    """Base exception for all C.O.R.E. errors."""


class ConfigurationError(CoreError):
    """Base exception for configuration-related errors."""


class InvalidConfiguration(ConfigurationError):
    """Raised when configuration is invalid."""


class ConfigurationNotLoaded(ConfigurationError):
    """Raised when configuration is accessed before being loaded."""


class ServiceError(CoreError):
    """Base exception for service-related errors."""


class ServiceNotFound(ServiceError):
    """Raised when a requested service does not exist."""


class ServiceAlreadyRegistered(ServiceError):
    """Raised when a service is already registered."""


class ServiceDependencyError(ServiceError):
    """Raised when a service dependency cannot be satisfied."""


class ResourceError(CoreError):
    """Base exception for resource-related errors."""


class ResourceNotFound(ResourceError):
    """Raised when a requested resource does not exist."""


class ResourceAlreadyRegistered(ResourceError):
    """Raised when a resource is already registered."""


class CommunicationError(CoreError):
    """Base exception for communication failures."""


class MessageError(CommunicationError):
    """Raised when a message is invalid."""


class RoutingError(CommunicationError):
    """Raised when a message cannot be routed."""


class HealthError(CoreError):
    """Base exception for health-monitoring failures."""


class DependencyError(CoreError):
    """Base exception for dependency-management failures."""


class CircularDependencyError(DependencyError):
    """Raised when circular dependencies are detected."""


class InitializationError(CoreError):
    """Raised when a component cannot initialize."""


class ShutdownError(CoreError):
    """Raised when a component cannot shut down."""
