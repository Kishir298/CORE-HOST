from core.health import (
    HealthMonitor,
    HealthResult,
    HealthStatus,
)


def healthy_check() -> HealthResult:
    return HealthResult(
        component_id="database",
        status=HealthStatus.HEALTHY,
        message="Database operational.",
    )


def degraded_check() -> HealthResult:
    return HealthResult(
        component_id="network",
        status=HealthStatus.DEGRADED,
        message="Network degraded.",
    )


def unhealthy_check() -> HealthResult:
    return HealthResult(
        component_id="rovert",
        status=HealthStatus.UNHEALTHY,
        message="ROVERT unavailable.",
    )


def test_health_result_defaults():
    result = HealthResult(
        component_id="core",
        status=HealthStatus.HEALTHY,
    )

    assert result.component_id == "core"
    assert result.status == HealthStatus.HEALTHY
    assert result.message == ""
    assert result.checked_at is not None


def test_register_health_check():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)

    assert monitor.count() == 1
    assert monitor.status("database") == HealthStatus.UNKNOWN


def test_healthy_check():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)

    result = monitor.check("database")

    assert result.status == HealthStatus.HEALTHY
    assert result.message == "Database operational."
    assert monitor.status("database") == HealthStatus.HEALTHY


def test_degraded_check():
    monitor = HealthMonitor()

    monitor.register("network", degraded_check)

    result = monitor.check("network")

    assert result.status == HealthStatus.DEGRADED


def test_unhealthy_check():
    monitor = HealthMonitor()

    monitor.register("rovert", unhealthy_check)

    result = monitor.check("rovert")

    assert result.status == HealthStatus.UNHEALTHY


def test_failed_health_check():
    monitor = HealthMonitor()

    def broken_check():
        raise RuntimeError("device disconnected")

    monitor.register("device", broken_check)

    result = monitor.check("device")

    assert result.status == HealthStatus.UNHEALTHY
    assert "device disconnected" in result.message


def test_invalid_health_result():
    monitor = HealthMonitor()

    def invalid_check():
        return "healthy"

    monitor.register("invalid", invalid_check)

    result = monitor.check("invalid")

    assert result.status == HealthStatus.UNHEALTHY
    assert "invalid result" in result.message


def test_missing_health_check():
    monitor = HealthMonitor()

    result = monitor.check("missing")

    assert result.status == HealthStatus.UNKNOWN


def test_get_missing_component():
    monitor = HealthMonitor()

    result = monitor.get("missing")

    assert result.component_id == "missing"
    assert result.status == HealthStatus.UNKNOWN


def test_check_all():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)
    monitor.register("network", degraded_check)

    results = monitor.check_all()

    assert len(results) == 2
    assert results[0].status == HealthStatus.HEALTHY
    assert results[1].status == HealthStatus.DEGRADED


def test_overall_healthy():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)
    monitor.register("core", healthy_check)

    monitor.check_all()

    assert monitor.overall_status() == HealthStatus.HEALTHY


def test_overall_degraded():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)
    monitor.register("network", degraded_check)

    monitor.check_all()

    assert monitor.overall_status() == HealthStatus.DEGRADED


def test_overall_unhealthy():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)
    monitor.register("rovert", unhealthy_check)

    monitor.check_all()

    assert monitor.overall_status() == HealthStatus.UNHEALTHY


def test_overall_unknown():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)

    assert monitor.overall_status() == HealthStatus.UNKNOWN


def test_unregister():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)
    monitor.unregister("database")

    assert monitor.count() == 0
    assert monitor.status("database") == HealthStatus.UNKNOWN


def test_clear():
    monitor = HealthMonitor()

    monitor.register("database", healthy_check)
    monitor.register("network", degraded_check)

    monitor.clear()

    assert monitor.count() == 0
    assert monitor.overall_status() == HealthStatus.UNKNOWN
