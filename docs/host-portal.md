# Host Portal (v0.4.0)

Localhost-only administrative control plane for C.O.R.E.-HOST. Presentation
layer only: every value is read through public C.O.R.E. authorities and
redacted before it reaches the browser.

## Startup

```powershell
py -m core --config config\core.lan.yaml
```

Output includes:

```text
Host Portal:
http://127.0.0.1:8765
```

Open that URL in a browser (desktop, tablet, or phone-sized viewport).

## Configuration (`web` section, all optional)

```yaml
web:
  enabled: true            # default true
  host: "127.0.0.1"        # default; remote admin is opt-in — never 0.0.0.0 by default
  port: 8765               # default; 0 = ephemeral (tests)
  auto_open_browser: false # default false
  location:
    enabled: true
    precision: approximate  # exact | approximate | city | hidden
```

## Sections (16)

Overview, Devices, Network, Resources, Services, Agents, R.E.S.C.S.,
Health, Events, Routing, Data Distribution, Sessions, Security,
Configuration, Logs, Diagnostics.

The dashboard polls `/api/status` every 4 seconds; no full refresh needed
(no WebSockets in v0.4.0).

## Architecture

```text
Browser (localhost only)
    ↓ HTTP (stdlib ThreadingHTTPServer)
HostPortal (core/portal/)
    ↓ public authorities only
DeviceRegistry · ResourceRegistry · OrganizationEngine · Router ·
ServiceManager · AgentScheduler · HealthMonitor · Runtime · RESCS adapter
```

## Network view

Topology is labeled **Logical C.O.R.E. connectivity** — never physical
topology. Device locations come only from explicit reports
(`POST /api/devices/location`) or manual host entry
(`POST /api/location`); otherwise `Location: Unknown`. Distances use
Haversine only when both endpoints have coordinates, else
`Distance unavailable`. Private LAN IPs are never geolocated.

## AI offload

`POST /api/agents/assign` and the `agent` service `infer` operation place
requests through the existing `AgentScheduler` (no duplicate scheduler).
`infer` returns placement (`agent_id`, `profile_id`, execution location);
model generation itself is the assigned agent runtime's job — the portal
never fabricates model output.

## Security

- Binds `127.0.0.1` by default; non-localhost bind prints a warning.
- No auth on the portal itself: localhost reach is the boundary.
- Responses pass recursive redaction: provisioning credentials, session
  tokens (shown only as `ACTIVE`), TLS keys, RESCS/S3 secrets, and
  credential-bearing URLs never leave the backend.
- Agent assign/release are the only mutations, both existing APIs.
- Events view reads a bounded in-memory ring (200); tracebacks are never
  sent to external devices (diagnostic detail is host-local).

## Troubleshooting

- Port in use → the portal logs a warning and the host keeps running;
  set `web.port` or `web.enabled: false`.
- Empty events → the ring fills as the bus publishes (subscribe happens
  at portal start).
- `web.enabled: false` disables the portal entirely (CLI unaffected).
