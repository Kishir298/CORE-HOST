# Audit operating notes — 20 October 2026

This snapshot is based on commit 9742ed6ead9104045094b791bc2aea871f730969.

- All non-loopback TCP binds require TLS and credential authentication.
- Verified external service calls now enforce permissions even if internal service authorization is disabled. Read-only device provisioning cannot mutate resources. Inference requires the execute permission. Raw RESCS list/get/runtime views require admin; use DATA_REQUEST for owner-scoped reads.
- Use double underscores between environment path components: CORE_COMMUNICATION__CONNECTION_LEASE_SECONDS=86400. Single underscores inside a component are retained in that form.
- HTTP file metadata must identify its owner. Missing/foreign ownership fails closed.
- The optional RescsDeviceProxy requires an adapter implementing its optional data_request method. Bundled adapters do not supply that write-oriented extension; use the supported DATA_REQUEST reader path.
- Monitoring dependencies are optional: install the monitoring extra if importing core.monitoring. Metrics exporters are scaffolding; this audit does not claim runtime instrumentation exists everywhere.
- Put CORE-CLIENT beside CORE-HOST to enable tests/test_client_pair_contract.py. It uses the real protocol code over in-memory I/O and does not test a TLS handshake or OS listener.

Run python -m pytest -q and python -m ruff check --select F core tests on the Windows host with working local networking. Validate actual certificates, provisioning, firewall, restart, persistence, and Task Scheduler before deployment. Keep existing local configuration, certificates, credentials, and runtime data when applying the source archive.
