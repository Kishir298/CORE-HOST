"""Host portal: localhost control plane over stdlib HTTP.

:class:`HostPortal` serves a JSON API plus a single-page dashboard. It is a
presentation layer only: every value is read through public C.O.R.E.
authorities (registry, resources, services, scheduler, health, events,
router, RESCS adapter) and :mod:`core.portal.models` redaction. It never
touches internal dictionaries, sockets, or persistence files.
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from . import geo
from .capabilities import host_facts
from .models import device_entry, envelope, event_entry, health_entry, redact, session_entry

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024
EVENT_HISTORY = 200


def _safe(callable_, default=None):
    try:
        return callable_()
    except Exception:
        return default


def _iso(value: Any) -> Any:
    try:
        if hasattr(value, "isoformat"):
            return value.isoformat()
    except Exception:
        pass
    return value


class HostPortal:
    """Localhost web portal bound to a running :class:`CoreApplication`."""

    def __init__(
        self,
        app: Any,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        auto_open_browser: bool = False,
        location_precision: str = geo.PRECISION_APPROXIMATE,
    ) -> None:
        self._app = app
        self.host = host or DEFAULT_HOST
        # NOTE: port 0 means "ephemeral" — do NOT use `or` here, it would
        # remap 0 to the default and collide with other portal instances.
        self.port = DEFAULT_PORT if port is None else int(port)
        self.auto_open_browser = bool(auto_open_browser)
        self.location_precision = location_precision
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._events: deque = deque(maxlen=EVENT_HISTORY)
        self._locations: dict[str, dict] = {}
        self._host_location: dict = geo.unknown_location()
        self._rescs_last_ok: str | None = None
        self._rescs_latency_ms: float | None = None
        self._warned_remote = False

    @property
    def url(self) -> str:
        """Public portal URL."""
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        """Whether the HTTP server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # -- lifecycle ------------------------------------------------------

    def start(self) -> str:
        """Start the portal (idempotent); return the URL."""
        if self.is_running:
            return self.url
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(
                f"Host portal refused non-loopback bind {self.host!r}: "
                "portal has no authentication; bind 127.0.0.1/localhost "
                "or front with token auth."
            )
        try:
            from core.events import types as event_types

            names = [
                value
                for name, value in vars(event_types).items()
                if name.isupper() and isinstance(value, str)
            ]
            for name in names:
                try:
                    self._app.events.subscribe(name, self._record_event)
                except Exception:
                    pass
        except Exception:
            pass
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        # An ephemeral port (0) resolves here.
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
            name="core-host-portal",
        )
        self._thread.start()
        self._started_at = time.time()
        if self.auto_open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass
        return self.url

    def stop(self) -> None:
        """Stop the portal (idempotent)."""
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    # -- events ----------------------------------------------------------

    def _record_event(self, event: Any) -> None:
        try:
            self._events.append(event)
        except Exception:
            pass

    # -- data builders (all defensive) ------------------------------------

    def _version(self) -> str:
        try:
            from core.version import __version__ as version
        except Exception:
            version = "unknown"
        return str(version)

    def api_status(self) -> dict:
        app = self._app
        devices = _safe(lambda: app.device_registry.list_devices()) or []
        online = sum(1 for device in devices if getattr(device, "status", "") == "online")
        transport = type(getattr(app, "communication", None)).__name__
        host = _safe(lambda: app.configuration.get("communication.host", "127.0.0.1"))
        port = _safe(lambda: app.configuration.get("communication.port", 0))
        return {
            "service": "core-host-portal",
            "core_version": self._version(),
            "runtime_state": str(getattr(getattr(app, "state", ""), "value", getattr(app, "state", "")) or ""),
            "is_running": bool(_safe(lambda: app.is_running, False)),
            "uptime_seconds": (time.time() - self._started_at) if self._started_at else None,
            "transport": transport,
            "lan_bind": {"host": host, "port": port},
            "tls": self._tls_info(),
            "devices": {
                "registered": len(devices),
                "online": online,
                "offline": len(devices) - online,
            },
            "agents": {
                "assigned": _safe(lambda: app.scheduler.assignment_count(), 0),
            },
            "portal": {"url": self.url},
        }

    def _tls_info(self) -> dict:
        tls = _safe(lambda: self._app.configuration.get("communication.tls", {}))
        if isinstance(tls, dict):
            return {
                "enabled": bool(tls.get("enabled", False)),
                "configured": bool(tls.get("certfile") or tls.get("certificate")),
            }
        return {"enabled": None, "configured": None}

    def api_devices(self) -> list:
        devices = _safe(lambda: self._app.device_registry.list_devices()) or []
        out = []
        for record in devices:
            try:
                out.append(device_entry(record))
            except Exception:
                continue
        return out

    def api_resources(self) -> list:
        resources = _safe(lambda: self._app.resources.list_resources()) or []
        out = []
        for resource in resources:
            try:
                if hasattr(resource, "to_dict"):
                    out.append(resource.to_dict())
                else:
                    out.append(
                        {
                            "resource_id": getattr(resource, "resource_id", None),
                            "name": getattr(resource, "name", None),
                            "resource_type": getattr(resource, "resource_type", None),
                            "status": getattr(resource, "status", None),
                        }
                    )
            except Exception:
                continue
        return out

    def api_services(self) -> list:
        services = _safe(lambda: self._app.services.list_services()) or []
        out = []
        for service in services:
            try:
                operations = _safe(
                    lambda service_id=service.service_id: self._app.services.list_operations(service_id)
                ) or []
                out.append(
                    {
                        "service_id": service.service_id,
                        "name": service.name,
                        "version": service.version,
                        "status": str(getattr(service.status, "value", service.status)),
                        "health": service.health,
                        "operations": list(operations),
                    }
                )
            except Exception:
                continue
        return out

    def api_agents(self) -> dict:
        scheduler = self._app.scheduler
        profiles = []
        for profile in _safe(lambda: scheduler.list_profiles()) or []:
            try:
                profiles.append(profile.to_dict())
            except Exception:
                continue
        assignments = []
        for assignment in _safe(lambda: scheduler.list_assignments()) or []:
            try:
                item = assignment.to_dict()
                item["assigned_at"] = _iso(item.get("assigned_at"))
                assignments.append(item)
            except Exception:
                continue
        agents = []
        for agent in _safe(lambda: scheduler.list_agents()) or []:
            try:
                agents.append(agent.to_dict() if hasattr(agent, "to_dict") else {"agent_id": getattr(agent, "resource_id", None)})
            except Exception:
                continue
        return {"profiles": profiles, "assignments": assignments, "agents": agents}

    def api_health(self) -> dict:
        results = _safe(lambda: self._app.health.check_all()) or []
        checks = []
        overall = "UNKNOWN"
        try:
            overall = str(_safe(lambda: self._app.health.overall_status(), "UNKNOWN"))
        except Exception:
            pass
        for result in results:
            try:
                checks.append(health_entry(result))
            except Exception:
                continue
        return {"overall": overall, "checks": checks}

    def api_events(self, *, limit: int = 100) -> list:
        items = []
        for event in list(self._events)[-max(1, min(limit, EVENT_HISTORY)) :]:
            try:
                items.append(event_entry(event))
            except Exception:
                continue
        return items

    def api_network(self) -> dict:
        devices = self.api_devices()
        for entry in devices:
            location = self._locations.get(entry.get("device_id") or "")
            entry["location"] = geo.apply_precision(
                location or geo.unknown_location(), self.location_precision
            )
            entry["distance"] = geo.format_distance(
                geo.distance_between(self._host_location, location or {})
            )
        routes = _safe(lambda: self._app.routing.list_routes(), None)
        if routes is None:
            routes = _safe(lambda: dict(getattr(self._app.routing, "_routes", {}) or {}))
        transport = self._app.communication
        connections = _safe(lambda: transport.list_connections(), None)
        return {
            "topology": "Logical C.O.R.E. connectivity (not physical topology).",
            "host": host_facts(),
            "host_location": geo.apply_precision(
                self._host_location, self.location_precision
            ),
            "devices": devices,
            "routes": routes if isinstance(routes, dict) else {},
            "active_connections": len(connections) if isinstance(connections, list) else None,
        }

    def api_sessions(self) -> list:
        transport = self._app.communication
        connections = _safe(lambda: transport.list_connections())
        if not isinstance(connections, list):
            return []
        out = []
        for session in connections:
            try:
                out.append(session_entry(session))
            except Exception:
                continue
        return out

    def api_rescs(self) -> dict:
        adapter = getattr(self._app, "rescs", None)
        started = time.monotonic()
        health = _safe(lambda: adapter.health(), {}) or {}
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        if health:
            import datetime as _datetime

            self._rescs_last_ok = _datetime.datetime.now(
                _datetime.timezone.utc
            ).isoformat()
            self._rescs_latency_ms = latency_ms
        return {
            "adapter": type(adapter).__name__ if adapter is not None else None,
            "health": health,
            "latency_ms": latency_ms,
            "last_successful_operation": self._rescs_last_ok,
            "note": "R.E.S.C.S. remains the persistence authority; the portal shows integration state only.",
        }

    def api_config(self) -> dict:
        get = lambda path, default=None: _safe(
            lambda: self._app.configuration.get(path, default), default
        )
        return {
            "core": {"name": get("core.name"), "version": get("core.version")},
            "environment": get("environment"),
            "communication": {
                "transport": get("communication.transport"),
                "host": get("communication.host"),
                "port": get("communication.port"),
                "tls_enabled": get("communication.tls.enabled"),
            },
            "network": {"enabled": get("network.enabled")},
            "security": {
                "provider": get("security.provider"),
                "enforce_authorization": get("security.enforce_authorization"),
            },
            "web": {
                "enabled": get("web.enabled"),
                "host": get("web.host"),
                "port": get("web.port"),
                "auto_open_browser": get("web.auto_open_browser"),
            },
        }

    def api_diagnostics(self) -> dict:
        import platform
        import sys

        errors = [
            item
            for item in self.api_events(limit=EVENT_HISTORY)
            if item.get("severity") == "error"
        ][-10:]
        components = {}
        for name in (
            "configuration", "communication", "routing", "resources",
            "organization", "events", "health", "services", "runtime",
        ):
            component = getattr(self._app, name, None)
            components[name] = (
                bool(_safe(lambda: component.is_running, False))
                if component is not None
                else None
            )
        return {
            "python": platform.python_version(),
            "platform": platform.platform() if hasattr(platform, "platform") else sys.platform,
            "core_version": self._version(),
            "components": components,
            "event_counts": {
                "buffered": len(self._events),
                "recent_errors": len(errors),
            },
            "recent_errors": errors,
        }

    # -- mutations (explicit, safe, existing APIs only) --------------------

    def assign_agent(self, device_id: str, profile_id: str | None = None) -> dict:
        """Assign a device through ``CoreApplication._assign_agent``."""
        result = self._app._assign_agent(device_id=device_id, profile_id=profile_id)
        agent = result.get("agent", {}) if isinstance(result, dict) else {}
        assignment = result.get("assignment", {}) if isinstance(result, dict) else {}
        agent_id = None
        if isinstance(agent, dict):
            agent_id = agent.get("resource_id") or agent.get("id")
        if isinstance(assignment, dict):
            assignment = dict(assignment)
            if isinstance(assignment.get("assigned_at"), object):
                assignment["assigned_at"] = _iso(assignment.get("assigned_at"))
            agent_id = agent_id or assignment.get("agent_id")
        return {"agent_id": agent_id, "agent": agent, "assignment": assignment}

    def release_agent(self, device_id: str) -> dict:
        """Release a device assignment through ``CoreApplication._release_agent``."""
        result = self._app._release_agent(device_id=device_id)
        agent_id = None
        if isinstance(result, dict):
            released = result.get("released")
            if isinstance(released, dict):
                agent_id = released.get("agent_id")
            agent_id = agent_id or result.get("agent_id")
        return {"released": True, "device_id": device_id, "agent_id": agent_id}

    def set_device_location(self, device_id: str, location: dict) -> dict:
        """Record an operator-supplied device location (RAM-only)."""
        if not isinstance(location, dict):
            raise ValueError("location must be an object")
        stored = geo.make_location(
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            source=location.get("source", geo.SOURCE_MANUAL),
            accuracy_meters=location.get("accuracy_meters"),
            timestamp=location.get("timestamp"),
        )
        self._locations[device_id] = stored
        return geo.apply_precision(stored, self.location_precision)

    def set_host_location(self, location: dict) -> dict:
        """Record the host's own location (RAM-only, manual)."""
        if not isinstance(location, dict):
            raise ValueError("location must be an object")
        self._host_location = geo.make_location(
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            source=location.get("source", geo.SOURCE_MANUAL),
            accuracy_meters=location.get("accuracy_meters"),
            timestamp=location.get("timestamp"),
        )
        return geo.apply_precision(self._host_location, self.location_precision)

    # -- HTTP plumbing ------------------------------------------------------

    def _make_handler(self):
        portal = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "CoreHostPortal/0.4.0"

            def log_message(self, *args):  # quiet by default
                pass

            def _send_json(self, payload: Any, *, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _send_text(self, text: str, content_type: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _read_json(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    length = 0
                if length <= 0 or length > MAX_BODY_BYTES:
                    return {}
                try:
                    raw = self.rfile.read(length)
                    parsed = json.loads(raw.decode("utf-8"))
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}

            def do_GET(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                try:
                    if path == "/":
                        self._send_text(INDEX_HTML, "text/html; charset=utf-8")
                    elif path == "/api/status":
                        self._send_json(envelope(portal.api_status()))
                    elif path == "/api/devices":
                        self._send_json(envelope(portal.api_devices()))
                    elif path == "/api/resources":
                        self._send_json(envelope(portal.api_resources()))
                    elif path == "/api/services":
                        self._send_json(envelope(portal.api_services()))
                    elif path == "/api/agents":
                        self._send_json(envelope(portal.api_agents()))
                    elif path == "/api/health":
                        self._send_json(envelope(portal.api_health()))
                    elif path == "/api/events":
                        self._send_json(envelope(portal.api_events()))
                    elif path == "/api/network":
                        self._send_json(envelope(portal.api_network()))
                    elif path == "/api/sessions":
                        self._send_json(envelope(portal.api_sessions()))
                    elif path == "/api/rescs":
                        self._send_json(envelope(portal.api_rescs()))
                    elif path == "/api/config":
                        self._send_json(envelope(portal.api_config()))
                    elif path == "/api/diagnostics":
                        self._send_json(envelope(portal.api_diagnostics()))
                    else:
                        self._send_json(
                            envelope(None, ok=False, error="unknown endpoint"),
                            status=404,
                        )
                except Exception as exc:
                    self._send_json(
                        envelope(None, ok=False, error=f"portal error: {exc}"),
                        status=500,
                    )

            def do_POST(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                try:
                    if path == "/api/agents/assign":
                        body = self._read_json()
                        device_id = body.get("device_id")
                        if not device_id:
                            raise ValueError("device_id is required")
                        result = portal.assign_agent(
                            str(device_id), body.get("profile_id")
                        )
                        self._send_json(envelope(result))
                    elif path == "/api/agents/release":
                        body = self._read_json()
                        device_id = body.get("device_id")
                        if not device_id:
                            raise ValueError("device_id is required")
                        self._send_json(envelope(portal.release_agent(str(device_id))))
                    elif path == "/api/devices/location":
                        body = self._read_json()
                        device_id = body.get("device_id")
                        if not device_id or not isinstance(body.get("location"), dict):
                            raise ValueError("device_id + location object required")
                        self._send_json(
                            envelope(portal.set_device_location(str(device_id), body["location"]))
                        )
                    elif path == "/api/location":
                        body = self._read_json()
                        if not isinstance(body.get("location"), dict):
                            raise ValueError("location object required")
                        self._send_json(envelope(portal.set_host_location(body["location"])))
                    else:
                        self._send_json(
                            envelope(None, ok=False, error="unknown endpoint"),
                            status=404,
                        )
                except ValueError as exc:
                    self._send_json(
                        envelope(None, ok=False, error=str(exc)), status=400
                    )
                except Exception as exc:
                    self._send_json(
                        envelope(None, ok=False, error=f"portal error: {exc}"),
                        status=500,
                    )

        return _Handler


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>C.O.R.E. Host Portal</title>
<style>
:root{color-scheme:dark light}
body{font-family:system-ui,sans-serif;margin:0;padding:0 12px 40px;max-width:1100px}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}
nav button{padding:6px 10px;cursor:pointer}
section{display:none}
section.active{display:block}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{border:1px solid #8883;padding:4px 8px;text-align:left;font-size:14px;overflow-wrap:anywhere}
.badge{padding:2px 8px;border-radius:8px;font-size:12px}
.ok{background:#1a7f37;color:#fff}.warn{background:#9a6700;color:#fff}
.err{background:#b42318;color:#fff}.mut{background:#555;color:#fff}
pre{background:#0002;padding:8px;overflow:auto;font-size:12px}
@media (max-width:640px){table,thead,tbody,tr,th,td{display:block}th{display:none}td{border:none;border-bottom:1px solid #8883}}
</style>
</head>
<body>
<header><h1>C.O.R.E. Host Portal</h1><span id="overall" class="badge mut">…</span></header>
<p id="statusline">connecting…</p>
<nav id="tabs"></nav>
<main id="content"></main>
<script>
const SECTIONS=[
 ["overview","Overview"],["devices","Devices"],["network","Network"],
 ["resources","Resources"],["services","Services"],["agents","Agents"],
 ["rescs","R.E.S.C.S."],["health","Health"],["events","Events"],
 ["routing","Routing"],["data","Data"],["sessions","Sessions"],
 ["security","Security"],["config","Configuration"],["logs","Logs"],
 ["diagnostics","Diagnostics"]
];
let active="overview", cache={};
async function api(path,opts){const r=await fetch(path,opts);return r.json();}
function esc(v){return String(v??"").replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function badge(text,cls){return `<span class="badge ${cls}">${esc(text)}</span>`;}
function statusBadge(s){s=String(s||"UNKNOWN").toUpperCase();
 if(s==="HEALTHY"||s==="ONLINE"||s==="RUNNING")return badge(s,"ok");
 if(s==="DEGRADED"||s==="EXPIRING"||s==="UNKNOWN")return badge(s,"warn");
 if(s==="FAILED"||s==="OFFLINE"||s==="DOWN")return badge(s,"err");return badge(s,"mut");}
function table(rows){if(!rows||!rows.length)return "<p><i>none</i></p>";
 const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 return "<table><thead><tr>"+keys.map(k=>`<th>${esc(k)}</th>`).join("")+"</tr></thead><tbody>"+
 rows.map(r=>"<tr>"+keys.map(k=>`<td>${esc(typeof r[k]==="object"?JSON.stringify(r[k]):r[k])}</td>`).join("")+"</tr>").join("")+"</tbody></table>";}
async function refreshStatus(){
 try{const j=await api("/api/status");cache.status=j.data;
  document.getElementById("statusline").textContent=
   `v${j.data.core_version} · ${j.data.runtime_state} · devices ${j.data.devices.online}/${j.data.devices.registered} online · agents ${j.data.agents.assigned}`;
 }catch(e){document.getElementById("statusline").textContent="portal unreachable";}
 try{const h=await api("/api/health");cache.health=h.data;
  document.getElementById("overall").outerHTML=badge(h.data.overall,h.data.overall==="HEALTHY"?"ok":"warn").replace('class="badge','id="overall" class="badge');
 }catch(e){}
}
async function render(){
 const c=document.getElementById("content");
 const ROUTE_DESCRIPTIONS={
  "RESOURCES.LIST":"resources service: list resources",
  "HEALTH.STATUS":"health service: component checks",
  "ORGANIZATION.LIST":"organization service: discovery snapshot",
  "ROUTING.ROUTES":"routing service: this route table",
  "COMMUNICATION.STATUS":"communication service: transport status"};
 const routeDescription=t=>ROUTE_DESCRIPTIONS[t]||"custom/dynamic route";
 if(active==="overview"){const s=cache.status||{};c.innerHTML=`<pre>${esc(JSON.stringify(s,null,1))}</pre>`;return;}
 if(active==="routing"){const n=cache.network||((await api("/api/network")).data);cache.network=n;
  c.innerHTML=`<p>${esc(n.topology||"")}</p><h3>Routes</h3>`+table(Object.entries(n.routes||{}).map(([k,v])=>({message_type:k,destination:v,description:routeDescription(k)})));return;}
 if(active==="data"){const r=cache.rescs||((await api("/api/rescs")).data);cache.rescs=r;
  c.innerHTML=`<pre>${esc(JSON.stringify(r,null,1))}</pre>`;return;}
 if(active==="security"){const cfg=cache.config||((await api("/api/config")).data);cache.config=cfg;
  c.innerHTML=`<p>Provider: ${esc(cfg.security?.provider)} · enforce_authorization: ${esc(cfg.security?.enforce_authorization)}</p><p>TLS on LAN transport is mandatory; portal binds localhost by default.</p>`;return;}
 if(active==="logs"||active==="diagnostics"){const d=cache.diag||((await api("/api/diagnostics")).data);cache.diag=d;
  c.innerHTML=`<pre>${esc(JSON.stringify(active==="logs"?d.recent_errors:d,null,1))}</pre>`;return;}
 if(active==="config"){const cfg=cache.config||((await api("/api/config")).data);cache.config=cfg;
  c.innerHTML=`<pre>${esc(JSON.stringify(cfg,null,1))}</pre>`;return;}
 const map={devices:"/api/devices",network:"/api/network",resources:"/api/resources",services:"/api/services",agents:"/api/agents",rescs:"/api/rescs",health:"/api/health",events:"/api/events",sessions:"/api/sessions"};
 const j=await api(map[active]);let data=j.data;
 if(active==="network"){cache.network=data;c.innerHTML=`<p>${esc(data.topology||"")}</p>`+table(data.devices);return;}
 if(active==="agents"){c.innerHTML=`<h3>Assignments</h3>`+table(data.assignments)+`<h3>Profiles</h3>`+table(data.profiles)+`<h3>Agents</h3>`+table(data.agents);return;}
 if(active==="health"){c.innerHTML=statusBadge(data.overall)+table(data.checks);return;}
 if(active==="events"){
  if(!data.length){c.innerHTML=`<p><i>No events yet — the ring fills as the bus publishes; history from before portal start is not backfilled.</i></p>`;return;}
  c.innerHTML=table(data.map(e=>({time:e.timestamp,type:e.event_type,severity:e.severity,source:e.source,summary:e.summary})));return;}
 if(active==="devices"){c.innerHTML=table(data.map(d=>({...d,lease:d.lease?JSON.stringify(d.lease):undefined})));return;}
 c.innerHTML=table(Array.isArray(data)?data:[data]);
}
function tabs(){const n=document.getElementById("tabs");
 n.innerHTML=SECTIONS.map(([id,label])=>`<button data-s="${id}">${label}</button>`).join("");
 n.querySelectorAll("button").forEach(b=>b.onclick=()=>{active=b.dataset.s;
  document.querySelectorAll("main section").forEach(()=>{});
  document.getElementById("content").innerHTML="<p>loading…</p>";render();});}
tabs();refreshStatus();render();setInterval(async()=>{await refreshStatus();await render();},4000);
</script>
</body>
</html>
"""
