"""Regression checks that require no TCP/HTTP listener."""
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import Message as Headers
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.application import CoreApplication
from core.communication.models import Message
from core.communication.tcp import TcpTransport
from core.configuration.models import Configuration
from core.configuration.validator import ConfigurationValidator
from core.data.errors import DataAccessDenied, DataRetrievalFailed
from core.data.requests import validate_data_request
from core.data.rescs_reader import HttpDataReader
from core.portal.server import HostPortal, INDEX_HTML
from core.resources.models import Resource
from core.scheduler.models import AgentProfile
from core.scheduler.scheduler import AgentScheduler, NoSuitableAgent


def invoke(portal, method, path, body=None, headers=None):
    handler = portal._make_handler().__new__(portal._make_handler())
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = method + " " + path + " HTTP/1.1"
    handler.headers = Headers()
    raw = json.dumps(body or {}).encode()
    values = {"Host": "127.0.0.1:8765", "Content-Type": "application/json",
              "X-Core-Request": "true", "Content-Length": str(len(raw))}
    values.update(headers or {})
    for key, value in values.items():
        if value is not None:
            handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    getattr(handler, "do_" + method)()
    return handler.wfile.getvalue()


@pytest.mark.parametrize("host", ["192.168.1.5", "10.0.0.4", "0.0.0.0", "host.example"])
def test_non_loopback_requires_tls(host):
    with pytest.raises(ValueError, match="TLS"):
        TcpTransport(host=host)


def test_lan_startup_sets_security_before_transport(tmp_path):
    path = tmp_path / "core.yaml"
    path.write_text('core: {name: test, version: "0.4.0"}\nnetwork: {enabled: true}\n'
                    'communication: {transport: tcp, host: "0.0.0.0", port: 5000, tls: {enabled: true}}\n'
                    'security: {provider: token}\n')
    app = CoreApplication(config_path=path)
    with patch.object(TcpTransport, "_build_ssl_context", return_value=object()):
        app._load_configuration()
    assert isinstance(app.communication, TcpTransport)
    assert app.communication.hardened


def test_mutation_origin_must_match_port():
    portal = HostPortal(None)
    handler = SimpleNamespace(headers={"Content-Type": "application/json", "Host": "127.0.0.1:8765",
                                       "Origin": "http://127.0.0.1:9000", "X-Core-Request": "true"})
    with pytest.raises(ValueError):
        portal._check_portal_origin(handler)


def test_get_rejects_rebinding_host():
    portal = HostPortal(None)
    assert b"403" in invoke(portal, "GET", "/", headers={"Host": "evil.example:8765"}).split(b"\r\n", 1)[0]


def test_dashboard_helper_sets_csrf_header():
    assert '"X-Core-Request"' in INDEX_HTML


@pytest.mark.parametrize("value", [[], {}, ["record_list"]])
def test_invalid_request_type_is_reported(value):
    parsed, error = validate_data_request({"request_type": value})
    assert parsed is None and error[0] == "INVALID_DATA_REQUEST"


def test_file_owner_is_checked_before_content():
    reader = HttpDataReader("http://rescs", "alice")
    with patch.object(reader, "_get_json", return_value={"file": {"id": "f", "owner": "bob"}}) as call:
        with pytest.raises(DataAccessDenied):
            reader.get_file_bytes("f")
    assert call.call_count == 1


def test_http_json_reads_are_bounded():
    reader = HttpDataReader("http://rescs", "alice")
    sizes = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size=-1):
            sizes.append(size)
            return b"x" * max(1, size)
    with patch("urllib.request.urlopen", return_value=Response()):
        with pytest.raises(DataRetrievalFailed):
            reader._get_json("/large")
    assert sizes and sizes[0] > 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_invalid_lease_rejected(value):
    config = Configuration({"core": {"name": "test", "version": "0.4.0"},
                            "communication": {"connection_lease_seconds": value}})
    assert not ConfigurationValidator().is_valid(config)


def test_environment_double_separator_preserves_key_underscores(monkeypatch):
    monkeypatch.setenv("AUDIT_COMMUNICATION__CONNECTION_LEASE_SECONDS", "120")
    config = Configuration()
    config.load_environment("AUDIT_")
    assert config.get("communication.connection_lease_seconds") == 120


def test_scheduler_idempotent_at_capacity():
    scheduler = AgentScheduler()
    scheduler.register_profile(AgentProfile(profile_id="one", name="one", max_assignments=1,
                                            capabilities=["chat"]))
    device = Resource("d", "d", "device", capabilities=["chat"])
    first = scheduler.assign(device, profile_id="one")
    assert scheduler.assign(device, profile_id="one") == first


def test_scheduler_capacity_check_is_atomic():
    scheduler = AgentScheduler()
    scheduler.register_profile(AgentProfile(profile_id="one", name="one", max_assignments=1,
                                            capabilities=["chat"]))
    original = scheduler.find_suitable_agent
    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(0.02)
        return result
    scheduler.find_suitable_agent = delayed
    def assign(i):
        try: return scheduler.assign(Resource(str(i), str(i), "device", capabilities=["chat"]), profile_id="one")
        except NoSuitableAgent: return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(assign, range(4)))
    assert sum(x is not None for x in results) == 1


def test_all_socket_writes_share_a_frame_lock():
    transport = TcpTransport()
    class Sock:
        def __init__(self): self.active = 0; self.overlap = False
        def sendall(self, data):
            self.active += 1
            self.overlap |= self.active > 1
            time.sleep(0.01)
            self.active -= 1
    sock = Sock()
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: transport._send_frame(sock, Message("a", "b", "TEST")), range(5)))
    assert not sock.overlap


def test_mtls_requires_trust_root():
    with pytest.raises(ValueError, match="cafile"):
        TcpTransport(use_tls=True, require_client_cert=True)


def test_registry_can_clear_authoritative_owner():
    from core.resources.registry import ResourceRegistry
    registry = ResourceRegistry()
    registry.register(Resource("r", "r", "device", owner="alice", source="old"))
    registry.update("r", owner=None, source=None)
    assert registry.get("r").owner is None and registry.get("r").source is None


def test_registry_iterator_is_a_snapshot():
    from core.resources.registry import ResourceRegistry
    registry = ResourceRegistry()
    registry.register(Resource("r", "r", "device"))
    snapshot = iter(registry)
    registry.register(Resource("other", "other", "device"))
    assert [r.resource_id for r in snapshot] == ["r"]


def test_persistent_credential_file_is_private(tmp_path):
    import os
    from core.rescs.adapter import FileRescsAdapter
    path = tmp_path / "rescs.json"
    adapter = FileRescsAdapter(path)
    adapter.persist_device({"device_id": "alice", "token": "private"})
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0


def test_resource_registration_timestamp_restores_without_last_seen(tmp_path):
    from core.rescs.adapter import FileRescsAdapter
    path = tmp_path / "rescs.json"
    path.write_text(json.dumps({"resources": [{"resource_id": "r", "name": "r", "resource_type": "device",
                                              "registered_at": "2020-01-01T00:00:00+00:00"}]}))
    assert FileRescsAdapter(path).fetch_resource("r").registered_at.year == 2020


def test_proxy_import_and_owner_prefix_boundary():
    from core.communication.devices import DeviceRecord
    from core.services.rescs_proxy import RescsDeviceProxy
    proxy = RescsDeviceProxy(None)
    device = DeviceRecord("alice", "Alice")
    with pytest.raises(ValueError, match="other device"):
        proxy.validate_device_access(device, "notes", "personal.device.alice2.notes")


@pytest.mark.parametrize("permission,expected", [("read", True), ("write", False)])
def test_verified_session_authorizes_without_provisioning_secret(permission, expected):
    from core.security import Identity, IdentityType, Permission, SecurityManager, SecurityPolicy
    from core.security.context import authenticated_transport
    from core.security.provider import TokenAuthenticationProvider
    from core.services.dispatch import ServiceDispatcher
    from core.services.manager import ServiceManager
    from core.services.models import Service
    security = SecurityManager(TokenAuthenticationProvider())
    security.register_identity(Identity("alice", "alice", IdentityType.DEVICE,
                                        frozenset({Permission.READ}), {"token": "secret"}))
    policy = SecurityPolicy()
    policy.grant("demo", "test", Permission(permission))
    services = ServiceManager()
    services.register(Service(service_id="demo", name="demo", version="1.0"))
    services.register_handler("demo", "test", lambda: {"ok": True})
    services.start("demo")
    dispatcher = ServiceDispatcher(services, security, policy)
    message = Message("alice", "service:demo", "SERVICE_REQUEST", {"operation": "test"}, identity_id="alice")
    with authenticated_transport("alice"):
        response = dispatcher.handle(message)
    assert response.payload["success"] is expected


def test_remote_read_cannot_bypass_owner_scope_via_raw_rescs():
    from core.security import Identity, IdentityType, Permission, SecurityManager, SecurityPolicy
    from core.security.context import authenticated_transport
    from core.services.dispatch import ServiceDispatcher
    security = SecurityManager()
    security.register_identity(Identity("alice", "alice", IdentityType.DEVICE, frozenset({Permission.READ})))
    policy = SecurityPolicy()
    policy.grant("rescs", "list", Permission.READ)
    dispatcher = ServiceDispatcher(None, security, policy)
    message = Message("alice", "service:rescs", "SERVICE_REQUEST", {"operation": "list"}, identity_id="alice")
    with authenticated_transport("alice"):
        response = dispatcher.handle(message)
    assert response.payload["success"] is False


def test_empty_injected_device_registry_is_preserved():
    from core.communication.devices import DeviceRegistry
    registry = DeviceRegistry()
    assert TcpTransport(device_registry=registry).device_registry is registry


def test_invalid_authoritative_resource_is_not_deleted():
    from core.resources.registry import ResourceRegistry
    from core.organization.ingestion import ResourceIngestor
    registry = ResourceRegistry()
    registry.register(Resource("r", "good", "device"))
    adapter = SimpleNamespace(list_resources=lambda: [{"resource_id": "r", "name": "bad"}])
    result = ResourceIngestor(adapter, registry).reconcile()
    assert result["failed"] == ["r"] and result["removed"] == []
    assert registry.get("r").name == "good"


def test_bad_request_does_not_disable_service():
    from core.services.manager import ServiceManager
    from core.services.models import Service, ServiceStatus
    services = ServiceManager()
    services.register(Service("demo", "demo", "1.0"))
    services.register_handler("demo", "echo", lambda value: value)
    services.start("demo")
    with pytest.raises(TypeError): services.execute("demo", "echo")
    assert services.status("demo") == ServiceStatus.RUNNING
    assert services.execute("demo", "echo", "valid") == "valid"


@pytest.mark.parametrize("method", ["list_resources", "list_runtimes", "list_devices"])
def test_http_adapter_respects_disabled_fallback(method):
    from core.rescs.adapter import HttpRescsAdapter
    adapter = HttpRescsAdapter(fallback=False)
    with patch.object(adapter, "_request", side_effect=OSError("offline")):
        with pytest.raises(OSError): getattr(adapter, method)()


def test_http_get_filters_are_query_parameters():
    from core.rescs.adapter import HttpRescsAdapter
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{}'
    with patch("urllib.request.urlopen", return_value=Response()) as call:
        HttpRescsAdapter()._request("GET", "/api/v1/records", {"entity_type": "device"})
    request = call.call_args.args[0]
    assert request.data is None and request.full_url.endswith("?entity_type=device")
