# C.O.R.E. Windows Autostart

C.O.R.E. is designed to run **24/7** on the Windows 11 laptop that co-hosts
R.E.S.C.S. This document describes how to register C.O.R.E. for automatic
start without manual `py -m core start`.

## Option 1 — Task Scheduler (Recommended, no admin service install)

Task Scheduler is the simplest Windows-native way to launch C.O.R.E. at
logon and restart it if it crashes.

### Install

PowerShell 5.1 (Run as user that owns the R.I.S.A.R.M.S. folder):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
.\scripts\windows\install_core_task.ps1
```

What it does:

* Creates task `RISARMS\CORE` in `Task Scheduler`
* Trigger: **At log on** (any user) + **At startup** (if admin)
* Action: `py -m core --config config/core.yaml start`
* Setting: *If the task fails, restart every 1 minute, up to 3 attempts*
* Condition: *Start even on battery, do not stop on idle*
* Working directory: repository root

### Manage

```powershell
Get-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\"
Enable-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\"
Disable-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\"
Unregister-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\" -Confirm:$false
Start-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\"
Stop-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\"
```

Logs:

* C.O.R.E. stdout: `var/core.log` (if you redirect in the action)
* Task history: `Event Viewer → Applications and Services Logs → Microsoft → Windows → TaskScheduler → Operational`

### Uninstall

```powershell
.\scripts\windows\install_core_task.ps1 -Uninstall
```

## Option 2 — Windows Service via NSSM (for true headless, no logon)

If you need C.O.R.E. before any user logs on, wrap it with
[NSSM](https://nssm.cc/) (Non-Sucking Service Manager) — 1 binary, no code:

```powershell
nssm install CORE "C:\Windows\py.exe" "-m core --config <RISARMS>\CORE-HOST\config\core.yaml start"
nssm set CORE AppDirectory <RISARMS>\CORE-HOST
nssm set CORE AppStdout <RISARMS>\CORE-HOST\var\core.log
nssm set CORE AppStderr <RISARMS>\CORE-HOST\var\core_error.log
nssm set CORE AppRestartDelay 5000
nssm start CORE
```

## Firewall Note (external transport)

If `config/core.yaml` uses `communication.host: 0.0.0.0` with
`network.enabled: true`, you must allow the port through Windows
Defender Firewall:

```powershell
New-NetFirewallRule -DisplayName "C.O.R.E. TCP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000
```

Replace `5000` with `communication.port`. See `docs/windows-firewall.md`.

## Verification

After a reboot (or manual start):

```powershell
py -m core --config config/core.yaml status
py -m core --config config/core.yaml health
Get-ScheduledTask -TaskName "CORE" -TaskPath "\RISARMS\" | Get-ScheduledTaskInfo
```

Health should show `HEALTHY`/`DEGRADED` (not `UNHEALTHY`) and
`var/rescs.json` should grow as devices register.
