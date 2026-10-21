#Requires -Version 5.1
<#
.SYNOPSIS
  Install or uninstall the RISARMS\CORE Scheduled Task for C.O.R.E. autostart.

.DESCRIPTION
  Creates a Task Scheduler entry that runs `py -m core start` at logon/startup.
  Works on the Windows 11 laptop that co-hosts C.O.R.E. + R.E.S.C.S.
  Safe to re-run — it recreates the task if it already exists.

.PARAMETER Uninstall
  Remove the task instead of installing.

.PARAMETER TaskPath
  Scheduler folder. Default \RISARMS\

.PARAMETER TaskName
  Task name. Default CORE

.PARAMETER Python
  Python launcher. Default "py"

.PARAMETER Config
  Config path relative to repo root. Default "config/core.yaml"

.EXAMPLE
  .\scripts\windows\install_core_task.ps1
  .\scripts\windows\install_core_task.ps1 -Uninstall
#>

param(
    [switch]$Uninstall,
    [string]$TaskPath = "\RISARMS\",
    [string]$TaskName = "CORE",
    [string]$Python = "py",
    [string]$Config = "config/core.yaml"
)

$ErrorActionPreference = "Stop"

# Resolve repo root as parent of scripts/windows
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
if (-not (Test-Path (Join-Path $RepoRoot "core\__main__.py"))) {
    Write-Error "Repository root not found: $RepoRoot (expected core/__main__.py)"
}

function Test-IsAdmin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$isAdmin = Test-IsAdmin

if ($Uninstall) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false -ErrorAction Stop
        Write-Host "Removed task $TaskPath$TaskName" -ForegroundColor Green
    } catch {
        Write-Warning "Task not found or already removed: $TaskPath$TaskName ($_)"
    }
    # Let Task Scheduler own its registry; remove only an empty COM folder.
    try {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $folder = $svc.GetFolder($TaskPath)
        if ($folder.GetTasks(0).Count -eq 0 -and $folder.GetFolders(0).Count -eq 0) {
            $svc.GetFolder("\").DeleteFolder($TaskPath.Trim("\"), 0)
        }
    } catch { Write-Warning "Could not remove empty task folder: $_" }
    exit 0
}

# Build action: py -m core --config config/core.yaml start
$Arguments = "-m core --config `"$Config`" start"
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $RepoRoot

$Triggers = @(
    New-ScheduledTaskTrigger -AtLogOn
)
if ($isAdmin) {
    try { $Triggers += New-ScheduledTaskTrigger -AtStartup } catch { Write-Warning "AtStartup trigger not available: $_" }
}

# Settings: restart on failure, allow on battery, don't stop on idle
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

# Principal: run as current user; if admin, use SYSTEM for startup? Keep interactive user.
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
if (-not $isAdmin) {
    # S4U may not be available on all SKUs; fall back to Interactive
    try {
        $Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    } catch { $Principal = New-ScheduledTaskPrincipal -GroupId "Users" }
}

# Ensure folder exists
try {
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $root = $svc.GetFolder("\")
    try { $root.GetFolder($TaskPath.TrimEnd("\")) } catch { $root.CreateFolder($TaskPath.TrimEnd("\").TrimStart("\")) | Out-Null }
} catch { Write-Warning "Could not ensure task folder via COM: $_" }

$Task = New-ScheduledTask -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal -Description "C.O.R.E. Communication, Organization and Resource Engine — R.I.S.A.R.M.S. brain. Repo: $RepoRoot"

# Register (overwrite if exists)
try {
    Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -InputObject $Task -Force | Out-Null
    Write-Host "Installed task $TaskPath$TaskName -> $Python $Arguments (wd: $RepoRoot)" -ForegroundColor Green
    Write-Host "Triggers: AtLogOn" -NoNewline
    if ($isAdmin) { Write-Host " + AtStartup" -NoNewline }
    Write-Host ""
    Write-Host "Manage: Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Get-ScheduledTaskInfo"
} catch {
    Write-Error "Failed to register task: $_"
    exit 1
}
