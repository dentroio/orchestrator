<#
.SYNOPSIS
Local watchdog for the Clarion Windows runner agent.

Restarts the runner scheduled task if the heartbeat file is stale.
Runs as a short scheduled task every few minutes (Ready between runs is normal).
#>
param(
    [string]$HeartbeatPath = "$env:ProgramData\ClarionLab\runner-heartbeat.txt",
    [string]$RunnerTaskName = "Clarion Windows Runner",
    [int]$StaleSeconds = 180
)

$ErrorActionPreference = "Continue"
$LogFilePath = Join-Path $env:ProgramData "ClarionLab\windows-runner-watchdog.log"

function Write-WatchdogLog {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss,fff') - [ClarionWatchdog] $Message"
    Write-Host $line
    try {
        $dir = Split-Path -Parent $LogFilePath
        if (-not (Test-Path $dir)) {
            New-Item -Path $dir -ItemType Directory -Force | Out-Null
        }
        Add-Content -Path $LogFilePath -Value $line -Encoding UTF8
    } catch { }
}

function Get-HeartbeatAgeSeconds {
    if (-not (Test-Path $HeartbeatPath)) {
        return [int]::MaxValue
    }
    try {
        $raw = (Get-Content -Path $HeartbeatPath -Raw -ErrorAction Stop).Trim()
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return [int]::MaxValue
        }
        $epoch = [int64]$raw
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        return [int]($now - $epoch)
    } catch {
        return [int]::MaxValue
    }
}

function Restart-RunnerTask {
    param([string]$TaskName)

    $info = $null
    try {
        $info = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Get-ScheduledTaskInfo
    } catch {
        Write-WatchdogLog "Cannot query task '$TaskName': $($_.Exception.Message)"
        return $false
    }

    if ($info.State -eq "Running") {
        try {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
            Start-Sleep -Seconds 2
        } catch {
            Write-WatchdogLog "Stop-ScheduledTask failed, trying schtasks /End: $($_.Exception.Message)"
            $null = & schtasks.exe /End /TN $TaskName 2>&1
            Start-Sleep -Seconds 2
        }
    }

    try {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-WatchdogLog "Start-ScheduledTask failed, trying schtasks /Run: $($_.Exception.Message)"
        $out = & schtasks.exe /Run /TN $TaskName 2>&1
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Write-WatchdogLog "schtasks /Run failed (exit $LASTEXITCODE): $out"
        return $false
    }
}

try {
    $age = Get-HeartbeatAgeSeconds
    if ($age -le $StaleSeconds) {
        Write-WatchdogLog "Heartbeat OK (${age}s <= ${StaleSeconds}s); no action"
        exit 0
    }

    Write-WatchdogLog "Heartbeat stale (${age}s > ${StaleSeconds}s); restarting '$RunnerTaskName'"
    if (Restart-RunnerTask -TaskName $RunnerTaskName) {
        Write-WatchdogLog "Restart requested successfully"
        exit 0
    }
    exit 1
} catch {
    Write-WatchdogLog "Watchdog error: $($_.Exception.Message)"
    exit 1
}
