# C.O.R.E. Windows Firewall

When C.O.R.E. exposes `TcpTransport` on `0.0.0.0` for external devices
(phones, watches, R.O.V.E.R.T., sensors) a firewall rule is required.

## When needed

* `config/core.yaml`:
  ```yaml
  communication:
    transport: tcp
    host: 0.0.0.0
    port: 5000
  network:
    enabled: true
  ```
* Without `host: 0.0.0.0` the transport binds `127.0.0.1` only — no rule needed.

## Create rule (PowerShell, Admin)

```powershell
New-NetFirewallRule -DisplayName "C.O.R.E. TCP 5000" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 `
  -Profile Private,Domain -Description "C.O.R.E. external devices"
```

## Scope hardening (optional)

Restrict to your LAN subnet only:

```powershell
New-NetFirewallRule -DisplayName "C.O.R.E. TCP 5000 LAN" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 `
  -RemoteAddress 192.168.1.0/24 -Profile Private
```

## Remove

```powershell
Remove-NetFirewallRule -DisplayName "C.O.R.E. TCP 5000"
```

## Verify

```powershell
Get-NetFirewallRule -DisplayName "C.O.R.E. TCP 5000" | Format-List
Test-NetConnection -ComputerName 127.0.0.1 -Port 5000
```

On failure, C.O.R.E. falls back to `127.0.0.1` with a warning when
`network.enabled` is not `true`.

## External-device hardening

Current C.O.R.E. version = 0.4.0.

External TCP requires TLS. External devices authenticate before application messages.
Connections are persistent. Maximum frame size is 10 MB. Maximum active connections is 64.
Idle connections expire after 300 seconds. TLS handshake timeout is 5 seconds.

External `0.0.0.0` listeners fail closed when TLS is missing or invalid —
they never silently downgrade to plaintext. TLS minimum is 1.2.

External authentication cannot use existence-only authentication:
`TokenAuthenticationProvider` is required for external-device configuration.
