from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock

from core.health.models import HealthResult, HealthStatus

HealthCheck = Callable[[], HealthResult]


class HealthMonitor:
    """
    Tracks the health of C.O.R.E. components.

    HealthMonitor stores registered health checks and their latest results.
    Checks are executed synchronously and failures are converted into
    UNHEALTHY results so one broken component does not crash the monitor.
    """

    def __init__(
        self,
        on_change: Callable[[HealthResult], None] | None = None,
    ) -> None:
        self._checks: dict[str, HealthCheck] = {}
        self._results: dict[str, HealthResult] = {}
        self._last_checked: dict[str, datetime] = {}
        self._check_counts: dict[str, int] = {}
        self._failure_counts: dict[str, int] = {}

        self._lock = RLock()
        self._active = True
        self._on_change = on_change

    def start(self) -> None:
        """Start health monitoring."""

        with self._lock:
            self._active = True

    def stop(self) -> None:
        """Stop health monitoring."""

        with self._lock:
            self._active = False

    @property
    def is_running(self) -> bool:
        """Return whether health monitoring is active."""

        with self._lock:
            return self._active

    def register(
        self,
        component_id: str,
        check: HealthCheck,
    ) -> None:
        """Register or replace a health check for a component."""

        if not component_id:
            raise ValueError("Component ID cannot be empty.")

        if not callable(check):
            raise TypeError("Health check must be callable.")

        with self._lock:
            self._checks[component_id] = check
            self._results[component_id] = HealthResult(
                component_id=component_id,
                status=HealthStatus.UNKNOWN,
                message="Health check has not run yet.",
            )
            self._last_checked.pop(component_id, None)
            self._check_counts[component_id] = 0
            self._failure_counts[component_id] = 0

    def unregister(self, component_id: str) -> None:
        """Remove a component's health check and stored state."""

        with self._lock:
            self._checks.pop(component_id, None)
            self._results.pop(component_id, None)
            self._last_checked.pop(component_id, None)
            self._check_counts.pop(component_id, None)
            self._failure_counts.pop(component_id, None)

    def check(self, component_id: str) -> HealthResult:
        """
        Execute the health check for a component.

        Missing checks return UNKNOWN. Exceptions and invalid return values
        become UNHEALTHY results. A notification fires whenever the resulting
        status differs from the previously stored status.
        """

        with self._lock:
            if not self._active:
                raise RuntimeError("Health monitor is not running.")

            check = self._checks.get(component_id)

        if check is None:
            return HealthResult(
                component_id=component_id,
                status=HealthStatus.UNKNOWN,
                message="No health check registered.",
            )

        checked_at = datetime.now(timezone.utc)

        with self._lock:
            self._check_counts[component_id] += 1

        try:
            result = check()

            if not isinstance(result, HealthResult):
                with self._lock:
                    self._failure_counts[component_id] += 1

                result = HealthResult(
                    component_id=component_id,
                    status=HealthStatus.UNHEALTHY,
                    message="Health check returned an invalid result.",
                )

        except Exception as exc:
            with self._lock:
                self._failure_counts[component_id] += 1

            result = HealthResult(
                component_id=component_id,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {exc}",
            )

        previous_status = None

        with self._lock:
            previous = self._results.get(component_id)
            if previous is not None:
                previous_status = previous.status

            self._results[component_id] = result
            self._last_checked[component_id] = checked_at

        if self._on_change is not None and previous_status != result.status:
            try:
                self._on_change(result)
            except Exception:
                pass

        return result

    def check_all(self) -> list[HealthResult]:
        """Execute every registered health check."""

        with self._lock:
            if not self._active:
                raise RuntimeError("Health monitor is not running.")

            component_ids = list(self._checks)

        return [
            self.check(component_id)
            for component_id in component_ids
        ]

    def get(self, component_id: str) -> HealthResult:
        """Return the latest stored health result for a component."""

        with self._lock:
            return self._results.get(
                component_id,
                HealthResult(
                    component_id=component_id,
                    status=HealthStatus.UNKNOWN,
                    message="Component not registered.",
                ),
            )

    def status(self, component_id: str) -> HealthStatus:
        """Return the latest health status for a component."""

        return self.get(component_id).status

    def overall_status(self) -> HealthStatus:
        """
        Calculate the overall health of all registered components.

        Priority:
        UNHEALTHY > DEGRADED > UNKNOWN > HEALTHY
        """

        with self._lock:
            if not self._results:
                return HealthStatus.UNKNOWN

            statuses = [
                result.status
                for result in self._results.values()
            ]

        if any(status == HealthStatus.UNHEALTHY for status in statuses):
            return HealthStatus.UNHEALTHY

        if any(status == HealthStatus.DEGRADED for status in statuses):
            return HealthStatus.DEGRADED

        if any(status == HealthStatus.UNKNOWN for status in statuses):
            return HealthStatus.UNKNOWN

        return HealthStatus.HEALTHY

    def last_checked(self, component_id: str) -> datetime | None:
        """Return the timestamp of the latest health check."""

        with self._lock:
            return self._last_checked.get(component_id)

    def check_count(self, component_id: str) -> int:
        """Return how many times a component's health check has run."""

        with self._lock:
            return self._check_counts.get(component_id, 0)

    def failure_count(self, component_id: str) -> int:
        """Return how many times a component's health check has failed."""

        with self._lock:
            return self._failure_counts.get(component_id, 0)

    def count(self) -> int:
        """Return the number of registered health checks."""

        with self._lock:
            return len(self._checks)

    def clear(self) -> None:
        """Remove all health checks and stored health state."""

        with self._lock:
            self._checks.clear()
            self._results.clear()
            self._last_checked.clear()
            self._check_counts.clear()
            self._failure_counts.clear()