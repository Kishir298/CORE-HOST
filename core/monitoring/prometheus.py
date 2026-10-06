"""Prometheus metrics exporter for CORE-HOST."""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

# Connection metrics
core_connections_active = Gauge(
    "core_connections_active",
    "Number of active connections",
)
core_connections_total = Counter(
    "core_connections_total",
    "Total connections accepted",
)
core_connections_rejected = Counter(
    "core_connections_rejected",
    "Total connections rejected",
)

# Message metrics
core_messages_total = Counter(
    "core_messages_total",
    "Total messages processed",
    ["direction"],  # sent, received
)
core_messages_bytes = Counter(
    "core_messages_bytes",
    "Total message bytes",
    ["direction"],  # sent, received
)

# Portal metrics
core_portal_requests_total = Counter(
    "core_portal_requests_total",
    "Total portal HTTP requests",
    ["method", "endpoint", "status"],
)
core_portal_request_duration_seconds = Histogram(
    "core_portal_request_duration_seconds",
    "Portal request latency",
    ["method", "endpoint"],
)

# RESCS adapter metrics
core_rescs_adapter_requests_total = Counter(
    "core_rescs_adapter_requests_total",
    "Total RESCS adapter requests",
    ["operation", "status"],
)
core_rescs_adapter_latency_seconds = Histogram(
    "core_rescs_adapter_latency_seconds",
    "RESCS adapter request latency",
    ["operation"],
)

# TLS metrics
core_tls_handshake_duration_seconds = Histogram(
    "core_tls_handshake_duration_seconds",
    "TLS handshake duration",
)
core_tls_handshake_failures_total = Counter(
    "core_tls_handshake_failures_total",
    "Total TLS handshake failures",
)

# Device metrics
core_device_registrations_total = Counter(
    "core_device_registrations_total",
    "Total device registrations",
    ["status"],
)
core_device_messages_routed_total = Counter(
    "core_device_messages_routed_total",
    "Total device messages routed",
    ["status"],
)

# Service dispatch metrics
core_service_dispatch_total = Counter(
    "core_service_dispatch_total",
    "Total service dispatches",
    ["service", "operation", "status"],
)
core_service_dispatch_latency_seconds = Histogram(
    "core_service_dispatch_latency_seconds",
    "Service dispatch latency",
    ["service", "operation"],
)

# Scheduler metrics
core_scheduler_assignments_total = Counter(
    "core_scheduler_assignments_total",
    "Total scheduler assignments",
    ["profile", "status"],
)


def metrics_endpoint() -> Response:
    """FastAPI endpoint for Prometheus metrics."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )