# C.O.R.E. Device Network Communication

Central device layer: authenticated registration, discovery, presence,
device-to-device routing, failure handling and reconnection over the
existing hardened TCP transport. One C.O.R.E. host relays
`device -> C.O.R.E. -> device`; there is no device-to-device direct P2P.

## Lifecycle

```text
Device authentication
        ↓
Device registration
        ↓
Device presence
        ↓
Device discovery
        ↓
Destination validation
        ↓
Device routing
        ↓
Destination delivery
        ↓
Disconnect/offline
        ↓
Reconnect
```

## Connection sequence

```text
TCP connection
      ↓
TLS if external (0.0.0.0 requires TLS 1.2+, fail closed)
      ↓
CORE_HANDSHAKE {identity_id, credential, protocol_version, join_name?}
      ↓
authentication (TokenAuthenticationProvider for external)
      ↓
CORE_HANDSHAKE_RESPONSE {authenticated, identity_id, protocol_version,
  connection_id, connected_at, lease_expires_at, lease_duration_seconds}
      ↓
DEVICE_REGISTER {device_id, device_name, device_type, platform,
  capabilities, protocol_version, join_name?}
      ↓
DEVICE_REGISTER_RESPONSE {registered: true, device_id, status: "online",
  join_name, connected_at, lease_expires_at, lease_duration_seconds}
      ↓
application / device messages
```

`join_name` is optional in both payloads: clients that send it (current
`CORE-CLIENT`) get it persisted verbatim; older clients that omit it get a
server-derived `<device-name>-<short-device-id>` fallback, so the protocol
stays v0.3.0 compatible. It is a display label only — identity binding
(`identity_id == device_id == source`) is unchanged and a foreign
`join_name` never grants another device's identity.

`DEVICE_REGISTER` is only accepted after authentication, and the
authenticated `identity_id` must equal the registering `device_id`.
Application traffic before registration is rejected with
`DEVICE_NOT_REGISTERED`; endpoint traffic (e.g. `service:*`) keeps its
legacy behavior.

## Identity model

`device_id` (stable device identity), `identity_id` (security identity),
`join_name` (stable human-readable label, e.g. `MacBook-mac-01`) and
`connection_id` (one live socket session) are distinct. A reconnecting
device keeps its `device_id`, `identity_id` and `join_name`, and receives
a new `connection_id` plus a new 24-hour lease. Only one
active connection may represent a `device_id`; a duplicate active
registration is rejected with `DEVICE_ALREADY_REGISTERED` without touching
the live binding.

Persistent device identity (`device_id`, `identity_id`, `join_name`,
metadata, credential material) survives restarts via R.E.S.C.S. Ephemeral
session state (`connection_id`, live socket, lease timers) never persists:
restored devices always start `offline` with no connection.

## Connection lease (host-authoritative, 24 hours)

Every authenticated connection starts a lease
(`CONNECTION_LEASE_SECONDS`, default `86400`, configurable via
`communication.connection_lease_seconds`) at the moment authentication
succeeds — never at provisioning, host start, or record creation. The
triple `connected_at / lease_expires_at / lease_duration_seconds` is
returned in both handshake and register responses for client-side
tracking. At expiry the host forcibly closes the connection through the
standard cleanup path (device `offline`, `connection_id` cleared,
`DEVICE_DISCONNECTED` emitted, slot released); an expired connection can
no longer communicate. Stale-expiry safety rides the existing
`connection_id` guards, so an old session can never offline a newer one.

## Message types

```text
CORE_HANDSHAKE
CORE_HANDSHAKE_RESPONSE
DEVICE_REGISTER
DEVICE_REGISTER_RESPONSE
DEVICE_DISCOVER            request payload: {}
DEVICE_DISCOVER_RESPONSE   payload: {devices: [...]}
DEVICE_INFO                request payload: {device_id}
DEVICE_INFO_RESPONSE       payload: {device: {...}}
DEVICE_ERROR               payload: {error, message, request_id}
```

Each discovery entry contains `device_id, device_name, device_type,
platform, capabilities, status, last_seen`. Discovery returns all
registered devices (online and offline); never devices that never
registered. Presence is exactly `online` / `offline`. `last_seen` is a UTC
timestamp of the most recent activity.

## Routing

For a device-to-device message `message.destination` names the target
`device_id`. C.O.R.E. verifies destination exists, is registered, is
online, has an active connection whose `connection_id` matches the
registry, and that source/identity equal the authenticated identity.
Forwarding preserves `message_id, message_type, payload, request_id,
identity_id, source, destination`. Offline targets yield
`DEVICE_UNAVAILABLE` (no silent drop, no queue, no blocking); the sender
connection stays usable.

## Error codes

```text
DEVICE_ERROR            envelope type for all protocol errors
DEVICE_UNAVAILABLE      destination offline or without an active connection
DEVICE_NOT_FOUND        destination does not exist
DEVICE_ALREADY_REGISTERED  duplicate active registration
DEVICE_NOT_REGISTERED   operation requires registration first
DEVICE_REGISTRATION_FAILED registration validation failure
INVALID_DESTINATION     destination field itself is invalid
COMMUNICATION_ERROR     transport/routing failure without a specific code
```

No Python tracebacks are exposed through the protocol.

## Disconnect / reconnect

Close, rejection or shutdown marks the device `offline`, clears its
`connection_id`, preserves the record and `last_seen`, and releases the
TCP slot. Cleanup only applies when the stored `connection_id` matches the
closing connection, so a stale close can never take a reconnected device
offline. Reconnect = authenticate + register again with the same
`device_id`; exactly one registry record ever exists per device.

## Fixed limits

```text
MAX_FRAME_SIZE = 10 MB (4-byte big-endian length prefix + UTF-8 JSON)
MAX_CONNECTIONS = 64
HEADER_SIZE = 4
TLS_HANDSHAKE_TIMEOUT = 5.0s
IDLE_CONNECTION_TIMEOUT = 300.0s
MINIMUM_TLS_VERSION = TLS 1.2
```

## Observability

Device metrics (`TcpTransport.device_metrics()`, merged by
`CoreApplication.device_metrics()`): `registered_devices,
online_devices, offline_devices, device_registration_failures,
device_routing_failures, device_discovery_requests,
device_messages_routed`. Existing TCP metrics (`active_connections,
total_connections, rejected_connections, authentication_failures,
protocol_failures, messages_received/sent`) are unchanged, plus
`lease_expirations` counting host-enforced lease closures. Registration
and presence transitions emit `DEVICE_CONNECTED` / `DEVICE_DISCONNECTED`
on the existing event bus; a `devices` health check reports
registered/online counts. `Router.route_to_device()` adds registry-backed
routing alongside the unchanged static `add_route` / `route` behavior.

## Automated vs physical validation

Everything above is implemented and validated by the automated localhost
(`127.0.0.1`) multi-device simulation suite
(`tests/communication/test_device_protocol.py`,
`tests/integration/test_device_messaging.py`: device-a/b/c sockets through
framing, handshake, auth, registration, routing and reconnect, plus
concurrency and stale-connection races). Physical-device communication
(phones, tablets, watches, ESP32 over LAN/internet) has NOT been
demonstrated — see `docs/lan-readiness.md` for the physical validation
procedure (status: NOT YET PERFORMED).

## Persistent registration

Registered device identities survive disconnections **and** C.O.R.E.
process restarts. Persistence flows through the existing R.E.S.C.S.
adapter architecture — never a second database:

```text
Mac device
    ↓ TCP/TLS
Windows C.O.R.E.
    ↓ DEVICE_REGISTER (+ auth token handoff)
DeviceRegistry (authoritative, one record per device_id)
    ↓ sanitized identity snapshot (no connection_id, no live status)
R.E.S.C.S. persistence (dedicated device records)
```

Persisted identity fields: `device_id, join_name, device_name, device_type,
platform, capabilities, identity_id, protocol_version, registered_at,
last_seen, permissions, token`. Runtime fields (`status`,
`connection_id`, lease timers) are never persisted.

On startup C.O.R.E. restores every persisted identity as `offline` with
`connection_id=None`, mirrors it into the `ResourceRegistry` (runtime
mirror only), and re-provisions the `SecurityManager` identity (without
overwriting operator-provisioned ones) so the device can authenticate on
reconnect. Only a fresh authentication + `DEVICE_REGISTER` transitions it
to `online` with a new `connection_id` — reconnecting keeps exactly one
logical record. Disconnect, C.O.R.E. restart and reconnect therefore all
preserve the registered identity:

```text
disconnect: YES
C.O.R.E. restart: YES
reconnect: YES
```

Covered by `tests/communication/test_device_persistence.py` (round-trip,
multi-device restart counts, repeated reconnects, stale-close race,
app shutdown/restart with `FileRescsAdapter`, post-restart discovery,
credential rejection, cross-identity claim rejection, TCP reconnect after
restore). The auth token is stored in local R.E.S.C.S. storage (same trust
domain as the server process, e.g. git-ignored `var/rescs.json`); never
commit credential-bearing state files.
