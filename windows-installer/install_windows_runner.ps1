param(
    [string]$OrchestratorUrl,
    [string]$RunnerId,
    [string]$FallbackPersona,
    [string]$InstallDir = "C:\Clarion\WindowsRunner",
    [string]$TaskName = "Clarion Windows Runner",
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$InstallerRoot = if ($PSScriptRoot) {
    $PSScriptRoot
}
elseif ($PSCommandPath) {
    Split-Path -Parent $PSCommandPath
}
else {
    Split-Path -Parent $MyInvocation.MyCommand.Definition
}

function Write-Step {
    param([string]$Message)
    Write-Host "[Installer] $Message" -ForegroundColor Cyan
}

function Prompt-Value {
    param(
        [string]$Prompt,
        [string]$Default
    )
    if ($NonInteractive) {
        return $Default
    }
    $suffix = if ($Default) { " [$Default]" } else { "" }
    $raw = Read-Host "$Prompt$suffix"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $Default
    }
    return $raw.Trim()
}

function Ensure-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this installer in an elevated PowerShell session (Administrator)."
    }
}

function Value-OrDefault {
    param(
        [string]$Value,
        [string]$Default
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Default
    }
    return $Value
}

function Resolve-AgentSource {
    $sameDir = Join-Path $InstallerRoot "windows_runner_agent.ps1"
    if (Test-Path $sameDir) {
        return $sameDir
    }
    $parentDir = Join-Path (Split-Path -Parent $InstallerRoot) "windows_runner_agent.ps1"
    if (Test-Path $parentDir) {
        return $parentDir
    }
    if ($NonInteractive) {
        throw "Cannot find windows_runner_agent.ps1 next to installer."
    }
    $manual = Read-Host "Path to windows_runner_agent.ps1"
    if (-not (Test-Path $manual)) {
        throw "windows_runner_agent.ps1 not found at $manual"
    }
    return $manual
}

try {
    Ensure-Admin
    Write-Step "Preparing Clarion Windows Runner installer"

    $OrchestratorUrl = Prompt-Value -Prompt "Orchestrator URL (http://host:5000)" -Default (Value-OrDefault -Value $OrchestratorUrl -Default "http://192.168.20.95:5000")
    Write-Host "Runner ID must match the orchestrator dashboard name (e.g. win-runner-1), not the PC hostname ($($env:COMPUTERNAME))." -ForegroundColor Yellow
    $RunnerId = Prompt-Value -Prompt "Runner ID (dashboard name, e.g. win-runner-1)" -Default (Value-OrDefault -Value $RunnerId -Default "")
    $FallbackPersona = Prompt-Value -Prompt "Fallback persona" -Default (Value-OrDefault -Value $FallbackPersona -Default "Sales")
    $InstallDir = Prompt-Value -Prompt "Install directory" -Default $InstallDir
    $TaskName = Prompt-Value -Prompt "Scheduled Task name" -Default $TaskName

    if ([string]::IsNullOrWhiteSpace($OrchestratorUrl) -or [string]::IsNullOrWhiteSpace($RunnerId)) {
        throw "Orchestrator URL and Runner ID are required."
    }

    $agentSource = Resolve-AgentSource
    $installPath = [System.IO.Path]::GetFullPath($InstallDir)
    $null = New-Item -Path $installPath -ItemType Directory -Force

    $agentDest = Join-Path $installPath "windows_runner_agent.ps1"
    Write-Step "Copying agent script to $agentDest"
    Copy-Item -Path $agentSource -Destination $agentDest -Force

    $watchdogSource = Join-Path $InstallerRoot "windows_runner_watchdog.ps1"
    if (-not (Test-Path $watchdogSource)) {
        $watchdogSource = Join-Path (Split-Path -Parent $InstallerRoot) "windows_runner_watchdog.ps1"
    }
    $watchdogDest = Join-Path $installPath "windows_runner_watchdog.ps1"
    if (Test-Path $watchdogSource) {
        Write-Step "Copying watchdog script to $watchdogDest"
        Copy-Item -Path $watchdogSource -Destination $watchdogDest -Force
    }

    $wrapperPath = Join-Path $installPath "start_windows_runner.ps1"
    $wrapper = @"
`$ErrorActionPreference = 'Stop'
& '$agentDest' -OrchestratorUrl '$OrchestratorUrl' -RunnerId '$RunnerId' -FallbackPersona '$FallbackPersona'
"@
    Set-Content -Path $wrapperPath -Value $wrapper -Encoding UTF8

    Write-Step "Registering scheduled task '$TaskName' (startup, SYSTEM)"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapperPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName

    if (Test-Path $watchdogDest) {
        # Task name without spaces (schtasks and older Task Scheduler APIs break on spaces).
        $watchdogTaskName = "ClarionWindowsRunnerWatchdog"
        Write-Step "Registering watchdog scheduled task '$watchdogTaskName' (every 2 minutes)"

        $watchdogBatch = Join-Path $installPath "run_watchdog.cmd"
        @(
            '@echo off',
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0windows_runner_watchdog.ps1`" -RunnerTaskName `"$TaskName`""
        ) | Set-Content -Path $watchdogBatch -Encoding ASCII

        $watchdogPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $watchdogSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $watchdogRegistered = $false

        try {
            $watchdogAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$watchdogBatch`""
            $watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
                -RepetitionInterval (New-TimeSpan -Minutes 2) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            Register-ScheduledTask -TaskName $watchdogTaskName -Action $watchdogAction -Trigger $watchdogTrigger `
                -Principal $watchdogPrincipal -Settings $watchdogSettings -Force | Out-Null
            $watchdogRegistered = $true
        } catch {
            Write-Step "Register-ScheduledTask for watchdog failed, trying schtasks.exe"
        }

        if (-not $watchdogRegistered) {
            $schArgs = @(
                '/Create', '/F',
                '/TN', $watchdogTaskName,
                '/TR', $watchdogBatch,
                '/SC', 'MINUTE',
                '/MO', '2',
                '/RU', 'SYSTEM',
                '/RL', 'HIGHEST'
            )
            $schtasksOut = & schtasks.exe @schArgs 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "schtasks failed to register watchdog (exit $LASTEXITCODE): $schtasksOut"
            }
        }
    }

    Write-Host ""
    Write-Host "Install complete." -ForegroundColor Green
    Write-Host "Task:          $TaskName"
    Write-Host "Runner ID:     $RunnerId"
    Write-Host "Orchestrator:  $OrchestratorUrl"
    Write-Host "Install path:  $installPath"
    Write-Host "Wrapper:       $wrapperPath"
    Write-Host ""
    Write-Host "Quick checks:" -ForegroundColor Yellow
    Write-Host "  Get-ScheduledTask -TaskName `"$TaskName`" | Get-ScheduledTaskInfo"
    Write-Host "  Get-ScheduledTask -TaskName ClarionWindowsRunnerWatchdog | Get-ScheduledTaskInfo"
    Write-Host "  Get-Content -Path `"$wrapperPath`""
    exit 0
}
catch {
    Write-Host "Install failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
