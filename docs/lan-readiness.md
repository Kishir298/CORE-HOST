# Physical LAN Readiness — Windows host ↔ Mac device

## Status

```text
AUTOMATED TESTING:      IMPLEMENTED (localhost suites, all passing)
PHYSICAL LAN VALIDATION: NOT YET PERFORMED — do not claim otherwise
```

The physical test has NOT been run. This document is the procedure for
when the user performs it.

## Topology under test

```text
WINDOWS (C.O.R.E. host/server)
    │ real LAN TCP/TLS
    ▼
MAC (external R.I.S.A.R.M.S. device)
```

## 1. Windows host setup

Firewall port 5000 is already configured and working. Verify it is still
in place (see `docs/windows-firewall.md`); no further firewall change is
needed unless the port number is changed.

## 2. TLS certificate configuration (Windows)

External `0.0.0.0` binding fails closed without valid TLS 1.2+ material —
never disable it, never fall back to plaintext for the LAN test.

Create a self-signed certificate (PowerShell, paths are examples):

```powershell
New-SelfSignedCertificate -DnsName "core-windows" -CertStoreLocation "Cert:\LocalMachine\My"
```

Export the certificate (public) and private key to files, e.g.
`C:\risarms\tls\core.crt` and `C:\risarms\tls\core.key`, or with
OpenSSL:

```powershell
openssl req -x509 -newkey rsa:2048 -nodes `
  -keyout C:\risarms\tls\core.key `
  -out C:\risarms\tls\core.crt `
  -subj "/CN=core-windows" -days 825
```

Copy the **public** `core.crt` to the Mac (needed for `--ca-file` below).
The private `core.key` stays on the Windows host. Neither file is ever
committed to git (see `.gitignore`: `*.crt`, `*.key`, `*.pem`).

## 3. Windows C.O.R.E. configuration

Copy the example (do not edit the committed example with real paths):

```powershell
copy config\core.lan.example.yaml config\core.lan.yaml
```

Edit `config\core.lan.yaml` so `communication.tls.certfile/keyfile` point
at the real files:

```yaml
communication:
  enabled: true
  transport: tcp
  host: "0.0.0.0"
  port: 5000
  tls:
    enabled: true
    certfile: "C:\\risarms\\tls\\core.crt"
    keyfile: "C:\\risarms\\tls\\core.key"

network:
  enabled: true

security:
  provider: token

rescs:
  adapter: file
  path: "var/rescs.json"
```

`config\core.lan.yaml` is git-ignored (`config/*.local.*` does not match —
keep it out of commits manually; it contains machine paths).

## 4. Find the Windows LAN IP

```powershell
ipconfig
```

Use the IPv4 address of the Wi-Fi/LAN adapter on the same network as the
Mac, e.g. `192.168.1.10`. Do not hardcode it anywhere in the repo; it is
passed to the Mac client at runtime.

## 5. Provision the Mac device identity (Windows, one-time)

The first `CORE_HANDSHAKE` authenticates against a provisioned identity,
so register the Mac's `device_id` + token **before** the first connect.
The token is prompted securely and never logged:

```powershell
py -m core --config config\core.lan.yaml provision-device `
  --device-id mac-01 --device-name "MacBook" --platform mac `
  --device-type phone --capabilities chat
```

Expected output:

```text
Provisioned device: mac-01
Identity persisted (offline). Token is stored, never displayed.
```

Give the same token to the Mac user out-of-band. Re-running the command
rotates the stored token.

## 5b. Optional: plaintext token logging (development testing only)

By default the host logs login attempts as `token=<REDACTED>`. For
physical testing, where watching the actual token in the Windows log is
useful, enable it explicitly in `config\core.lan.yaml` (never commit this
file with real paths):

```yaml
security:
  log_external_device_tokens: true
```

```text
[INFO] External device login attempt
       device_id=mac-01
       join_name=MacBook-mac-01
       token=<TOKEN>
```

SECURITY WARNING: plaintext tokens in logs can leak via log files,
screen sharing, or crash reports. Keep this `false` everywhere except
the active physical test, and rotate the device token afterwards with
`provision-device`. Tokens are never persisted to R.E.S.C.S., the device
registry snapshot, or the client remembered-device file regardless of
this flag.

## 6. Start C.O.R.E. (Windows)

```powershell
py -m core --config config\core.lan.yaml start
```

(`start` may be omitted — running `py -m core --config config\core.lan.yaml`
with no subcommand also starts the host. Any other unknown command prints
an actionable error instead of exiting silently.)

Expected: `C.O.R.E. is running.` Leave it running (Ctrl+C to stop).

## 7. Mac client setup

The client lives in the separate `Kishir298/CORE-CLIENT` repository and is
stdlib-only (no `core` imports, no extra packages). Check it out alongside
this host repo (as `RISARMS/CORE-CLIENT`) and run it from there:

```bash
cd /path/to/CORE-CLIENT
python3 -m client --help
```

Save the remembered device (one-time; stores NO secrets):

```bash
python3 -m client --remember \
  --device-file ~/.risarms-device.json \
  --device-id mac-01 \
  --device-name "MacBook" \
  --host 192.168.1.10 --port 5000
```

Expected:

```text
Remembered device saved to ~/.risarms-device.json (no secrets stored).
Login is still required on every launch (Option A).
```

Copy the Windows `core.crt` to the Mac, e.g. `~/core.crt`.

## 8. Logging in (Mac — every launch, Option A)

```bash
python3 -m client \
  --device-file ~/.risarms-device.json \
  --host 192.168.1.10 --port 5000 \
  --ca-file ~/core.crt
```

The client prompts `Token for mac-01:` (or pass `--token` for scripting).
If the certificate is self-signed and verification must be skipped on a
trusted LAN only, add `--insecure` explicitly.

## 9. Connecting + device registration (Mac)

After login the client automatically performs:

```text
TCP connect to <windows-lan-ip>:5000
TLS handshake (1.2+)
CORE_HANDSHAKE {identity_id, credential (provisioning), protocol_version}
CORE_HANDSHAKE_RESPONSE {authenticated: true, connection_id, session_token,
  connected_at, lease_expires_at, lease_duration_seconds: 86400}
DEVICE_REGISTER {device_id, join_name, ..., _session_token}
DEVICE_REGISTER_RESPONSE {registered: true, device_id, status: "online",
  join_name, session_token, ...lease triple}
```

Expected client output: the R.I.S.A.R.M.S. session banner with device,
`join_name`, ONLINE status, temporary session token, `connection_id`,
and the 24-hour lease countdown.

On the host, `var/rescs.json` gains a `devices` entry for the Mac
(identity fields + provisioning token; no `connection_id`, no session
token, no live status, no lease timers).

## 10. Disconnect behavior

Quit the client (`quit` or Ctrl+C): the host marks the Mac `offline`,
invalidates the session token, clears `connection_id`, updates
`last_seen`. The `devices` entry remains. The client prints:

```text
Disconnected; session token cleared (device remains remembered).
```

## 11. Reconnect behavior

Launch the client again (login again — Option A), with the same
`device_id`/`identity_id` + provisioning credential:

- authentication succeeds against the persisted identity
- the host issues a NEW session token + NEW `connection_id` + fresh lease
- `DEVICE_REGISTER` restores the same logical record, `online`
- duplicate `DEVICE_REGISTER` while online is rejected with
  `DEVICE_ALREADY_REGISTERED`; a wrong token is rejected and the device
  stays `offline`; the old session token is invalid

While the client stays open, typing `reconnect` re-establishes the
session over a new `connection_id` + new session token + new lease
without re-entering anything. `session` shows live state.

## 12. C.O.R.E. restart behavior

Shut C.O.R.E. down and start it again **before** the Mac reconnects:

- log shows the restore count (`Restored N persisted device(s)`)
- discovery lists the Mac as `status: offline`
- `registered_devices` includes it; `online_devices` does not

## 13. Option A login behavior (remembered device ≠ login session)

Persistent (`~/.risarms-device.json`): `device_id`, `identity_id`,
`join_name`, endpoint, non-secret metadata. Ephemeral (memory only):
provisioning credential, session token, socket, `connection_id`, auth
state, lease tracking. The session token is displayed while connected
but never persisted.

- App open → session stays authenticated (reconnect works).
- Full app shutdown → session destroyed, connection closed cleanly,
  nothing secret written to disk.
- Next launch → remembered device loads, login is required again.
- The device is NOT treated as brand-new: no re-registration from scratch.

 ## Validation checklist (fill in during the physical test)

  - [ ] TLS handshake succeeds from Mac to Windows
  - [ ] `CORE_HANDSHAKE_RESPONSE.authenticated == true` with `session_token`
  - [ ] Session banner shows token + `connection_id` + 24h countdown
  - [ ] `~/.risarms-device.json` contains NO session token/credential
  - [ ] `reconnect` yields new token + new `connection_id` + fresh lease
 - [ ] Host log shows the login attempt with `device_id` + `join_name`
       (token visible only with `log_external_device_tokens: true`)
 - [ ] Host log shows `External device authenticated` with `connection_id`
 - [ ] First `DEVICE_REGISTER` returns `status: online` with `join_name`
 - [ ] `var/rescs.json` contains the Mac identity incl. `join_name`
       (no connection state, no lease timers)
 - [ ] Handshake/register responses carry `connected_at`,
       `lease_expires_at`, `lease_duration_seconds = 86400`
 - [ ] Mac Wi-Fi drop → host shows `offline`, record retained
 - [ ] C.O.R.E. restart → Mac restored as `offline` with `join_name` intact
 - [ ] Mac reconnect → `online`, same `device_id`/`identity_id`/`join_name`,
       new `connection_id`, new lease
 - [ ] Lease expiry is covered by automated fake-clock tests
       (do not wait 24 hours physically)
 - [ ] Wrong credential → rejected, stays `offline`
 - [ ] Claiming another `device_id` → rejected
 - [ ] Client restart → login required again, device still remembered

## Security notes

- Never disable TLS or switch to the existence provider for the LAN test.
- `var/rescs.json` contains device tokens: it is git-ignored local state;
  never commit it, never copy it off the host insecurely.
- The Mac remembered-device file contains no secrets; the login token is
  never written to disk by the client.
