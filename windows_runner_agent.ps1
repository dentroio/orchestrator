<#
.SYNOPSIS
Clarion Lab Traffic Agent for Windows (stable host / real user)

.DESCRIPTION
This script runs on a real Windows domain PC. It keeps the host and logged-in
user stable, asks the Orchestrator for a host-specific traffic plan, then
generates continuous traffic based on that plan.

It integrates with the orchestrator as a source of truth for identity mapping,
target selection, and telemetry, without rotating personas like the Pi runners.

.PARAMETER OrchestratorUrl
The URL of the lab orchestrator (e.g. http://192.168.20.95:5000)

.PARAMETER RunnerId
The configured runner name for this Windows host in the Orchestrator UI.

.PARAMETER FallbackPersona
(Optional) If the local logged-in user cannot be found in the Orchestrator's
identity database, this persona will be used. (e.g. "Engineering")
#>

param (
    [Parameter(Mandatory=$true)]
    [string]$OrchestratorUrl,

    [Parameter(Mandatory=$true)]
    [string]$RunnerId,

    [Parameter(Mandatory=$false)]
    [string]$FallbackPersona = "Sales"
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$global:LogLines = New-Object System.Collections.Generic.List[string]
$global:TrafficHistory = New-Object System.Collections.Generic.List[object]
$global:CurrentPlan = $null
$global:WindowsAgentVersion = "2026.05.19.2"
$global:AgentStartedAt = Get-Date
$global:AgentHealth = @{
    phase = "starting"
    last_error = ""
    last_session_result = ""
    uptime_s = 0
}
$HeartbeatPath = Join-Path $env:ProgramData "ClarionLab\runner-heartbeat.txt"
$LogFilePath = Join-Path $env:ProgramData "ClarionLab\windows-runner.log"

function Update-AgentHealth {
    param([hashtable]$Updates)
    foreach ($key in $Updates.Keys) {
        $global:AgentHealth[$key] = $Updates[$key]
    }
    $global:AgentHealth.uptime_s = [int]((Get-Date) - $global:AgentStartedAt).TotalSeconds
}

function Write-HeartbeatFile {
    try {
        $dir = Split-Path -Parent $HeartbeatPath
        if (-not (Test-Path $dir)) {
            New-Item -Path $dir -ItemType Directory -Force | Out-Null
        }
        Set-Content -Path $HeartbeatPath -Value ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Encoding ASCII -Force
    } catch { }
}

function Invoke-ControlAction {
    param([object]$Control)
    if (-not $Control) { return }
    if ($Control.restart_requested) {
        $reason = if ($Control.restart_reason) { [string]$Control.restart_reason } else { "orchestrator" }
        Write-OrchLog "Orchestrator requested agent restart: $reason" "WARNING"
        Update-AgentHealth @{ phase = "restarting"; last_error = $reason }
        $ctx = Get-ComputerContext
        Send-Telemetry -Status "restarting ($($ctx.username))" -ControlAckRestart
        exit 0
    }
}

function Write-OrchLog {
    param([string]$Message, [string]$Level="INFO")
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss,fff")
    $logLine = "$timestamp - [WindowsAgent] - $Level - $Message"
    Write-Host $logLine

    $global:LogLines.Add($logLine)
    if ($global:LogLines.Count -gt 100) {
        $global:LogLines.RemoveAt(0)
    }
    try {
        $logDir = Split-Path -Parent $LogFilePath
        if (-not (Test-Path $logDir)) {
            New-Item -Path $logDir -ItemType Directory -Force | Out-Null
        }
        Add-Content -Path $LogFilePath -Value $logLine -Encoding UTF8
    } catch { }
}

function Get-ComputerContext {
    $computerSystem = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $domain = if ($computerSystem -and $computerSystem.Domain) { $computerSystem.Domain } else { "" }
    $isDomainJoined = [bool]($computerSystem -and $computerSystem.PartOfDomain)
    $hostname = $env:COMPUTERNAME
    $fqdn = if ($domain -and $hostname) { "$hostname.$domain" } else { $hostname }
    $interactiveUser = ""
    if ($computerSystem -and $computerSystem.UserName) {
        $interactiveUser = [string]$computerSystem.UserName
    }
    # Prefer the interactive user for policy attribution. If nobody is logged in,
    # use the machine identity so traffic is still attributable to this host.
    $effectiveUsername = if ($interactiveUser) { $interactiveUser } else { "$hostname$" }
    $principalType = if ($interactiveUser) { "user" } else { "machine" }

    return @{
        hostname = $hostname
        fqdn = $fqdn
        username = $effectiveUsername
        interactive_username = $interactiveUser
        principal_type = $principalType
        user_logged_in = [bool]$interactiveUser
        domain_joined = $isDomainJoined
        machine_auth_capable = $isDomainJoined
    }
}

function Send-Telemetry {
    param(
        [string]$Status,
        [switch]$ControlAckRestart
    )

    $ctx = Get-ComputerContext
    Update-AgentHealth @{ phase = if ($Status -match '^discovery') { 'discovery' } elseif ($Status -match '^active') { 'active' } else { 'idle' } }
    $telemetryData = @{
        runner_id = $RunnerId
        status = $Status
        code_version = "windows-agent/$($global:WindowsAgentVersion)"
        log_lines = $global:LogLines.ToArray()
        traffic_history = @($global:TrafficHistory.ToArray())
        timestamp = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        platform = "Windows"
        hostname = $ctx.hostname
        fqdn = $ctx.fqdn
        username = $ctx.username
        interactive_username = $ctx.interactive_username
        principal_type = $ctx.principal_type
        user_logged_in = $ctx.user_logged_in
        domain_joined = $ctx.domain_joined
        machine_auth_capable = $ctx.machine_auth_capable
        current_plan = $global:CurrentPlan
        health = $global:AgentHealth
    }
    if ($ControlAckRestart) {
        $telemetryData.control_ack = @{ restart = $true }
    }
    $body = $telemetryData | ConvertTo-Json -Depth 8

    try {
        $response = Invoke-RestMethod -Uri "$OrchestratorUrl/api/runner/telemetry" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 3 -ErrorAction Stop
        Write-HeartbeatFile
        if ($response -and $response.control) {
            Invoke-ControlAction -Control $response.control
        }
    } catch {
        Update-AgentHealth @{ last_error = $_.Exception.Message }
    }
}

function Get-ObservedPolicyAction {
  <#
  Network policy "allow" = TCP/TLS reached the host and an HTTP response was returned.
  Application blocks (403/401 from CDN/bot protection) are still "allow" for lab policy tests.
  Only connection failures/timeouts count as network "deny".
  #>
    param([object]$StatusCode, [System.Exception]$Exception)
    if ($null -ne $Exception -and $null -eq $Exception.Response) {
        return "deny"
    }
    $code = 0
    if ($StatusCode -is [int]) { $code = $StatusCode }
    elseif ($StatusCode -match '^\d+$') { $code = [int]$StatusCode }
    if ($code -ge 200 -and $code -lt 600) {
        return "allow"
    }
    return "deny"
}

function Send-PolicyTestResult {
    param(
        [string]$CaseId,
        [string]$Target,
        [string]$Method,
        [string]$ExpectedAction,
        [string]$ObservedAction,
        [string]$TestResult,
        [object]$StatusCode
    )
    if (-not $ExpectedAction) {
        return
    }

    $ctx = Get-ComputerContext
    $payload = @{
        runner_id = $RunnerId
        session_id = "windows-$($ctx.hostname)-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
        identity = @{
            username = $ctx.username
            device_name = $ctx.hostname
            persona = if ($global:CurrentPlan.identity.persona) { $global:CurrentPlan.identity.persona } else { $FallbackPersona }
            department = if ($global:CurrentPlan.identity.department) { $global:CurrentPlan.identity.department } else { "" }
        }
        results = @(
            @{
                timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
                case_id = $CaseId
                target = $Target
                method = $Method
                status = $StatusCode
                expected_action = $ExpectedAction
                observed_action = $ObservedAction
                test_result = $TestResult
            }
        )
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    } | ConvertTo-Json -Depth 8
    try {
        Invoke-RestMethod -Uri "$OrchestratorUrl/api/runner/policy-test-results" -Method Post -Body $payload -ContentType "application/json" -TimeoutSec 4 -ErrorAction Stop | Out-Null
    } catch { }
}

function Get-WindowsHostPlan {
    $ctx = Get-ComputerContext
    $body = @{
        hostname = $ctx.hostname
        fqdn = $ctx.fqdn
        username = $ctx.username
        interactive_username = $ctx.interactive_username
        principal_type = $ctx.principal_type
        user_logged_in = $ctx.user_logged_in
        fallback_persona = $FallbackPersona
        domain_joined = $ctx.domain_joined
        machine_auth_capable = $ctx.machine_auth_capable
    } | ConvertTo-Json -Depth 4

    return Invoke-RestMethod -Uri "$OrchestratorUrl/api/windows-hosts/$RunnerId/plan" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 8 -ErrorAction Stop
}

Write-OrchLog "Starting Windows Traffic Agent"
Write-OrchLog "Agent version: windows-agent/$($global:WindowsAgentVersion)"
Write-OrchLog "Orchestrator: $OrchestratorUrl"

$context = Get-ComputerContext
if ($context.user_logged_in) {
    Write-OrchLog "Detected interactive Windows user: $($context.username)"
} else {
    Write-OrchLog "No interactive user detected; using machine identity: $($context.username)"
}
Write-OrchLog "Detected host: $($context.hostname) (domain_joined=$($context.domain_joined))"

$plan = $null
while (-not $plan) {
    try {
        $plan = Get-WindowsHostPlan
        $global:CurrentPlan = $plan
        if ($plan.orchestrator_control) {
            Invoke-ControlAction -Control $plan.orchestrator_control
        }
    } catch {
        Write-OrchLog "Failed to fetch Windows host plan from orchestrator: $($_.Exception.Message)" "ERROR"
        Write-OrchLog "Retrying plan fetch in 30 seconds..." "WARNING"
        Start-Sleep -Seconds 30
    }
}

$persona = if ($plan.identity -and $plan.identity.persona) { $plan.identity.persona } else { $FallbackPersona }
$targetUrls = @($plan.access_urls)
$executionMode = if ($plan.execution_mode) { [string]$plan.execution_mode } else { "discovery" }
$trafficMethod = if ($plan.traffic_method) { [string]$plan.traffic_method } else { "GET" }
$userAgent = if ($plan.user_agent) { [string]$plan.user_agent } else { "ClarionLab-Windows-$($persona -replace '\s','')/2.0" }
$minSleep = if ($plan.traffic_min_sleep) { [int]$plan.traffic_min_sleep } else { 5 }
$maxSleep = if ($plan.traffic_max_sleep) { [int]$plan.traffic_max_sleep } else { 30 }
$policyCases = @()
if ($plan.policy_test_plan -and $plan.policy_test_plan.cases) {
    $policyCases = @($plan.policy_test_plan.cases)
}
$planCaseIndex = 0
$planRefreshAt = (Get-Date).AddMinutes(2)
$discoverySleep = 15

function Update-PlanFromOrchestrator {
    param([ref]$PlanRef, [ref]$PersonaRef, [ref]$TargetUrlsRef, [ref]$ExecutionModeRef,
        [ref]$TrafficMethodRef, [ref]$UserAgentRef, [ref]$MinSleepRef, [ref]$MaxSleepRef,
        [ref]$PolicyCasesRef, [ref]$PolicyCaseIndexRef, [ref]$PlanRefreshAtRef)

    $fresh = Get-WindowsHostPlan
    $PlanRef.Value = $fresh
    $global:CurrentPlan = $fresh
    if ($fresh.identity -and $fresh.identity.persona) { $PersonaRef.Value = $fresh.identity.persona }
    $TargetUrlsRef.Value = @($fresh.access_urls)
    if ($fresh.execution_mode) { $ExecutionModeRef.Value = [string]$fresh.execution_mode }
    if ($fresh.traffic_method) { $TrafficMethodRef.Value = [string]$fresh.traffic_method }
    if ($fresh.user_agent) { $UserAgentRef.Value = [string]$fresh.user_agent }
    if ($fresh.traffic_min_sleep) { $MinSleepRef.Value = [int]$fresh.traffic_min_sleep }
    if ($fresh.traffic_max_sleep) { $MaxSleepRef.Value = [int]$fresh.traffic_max_sleep }
    if ($fresh.policy_test_plan -and $fresh.policy_test_plan.cases) {
        $PolicyCasesRef.Value = @($fresh.policy_test_plan.cases)
    } else {
        $PolicyCasesRef.Value = @()
    }
    if ($fresh.orchestrator_control) {
        Invoke-ControlAction -Control $fresh.orchestrator_control
    }
    $mode = $ExecutionModeRef.Value
    $PlanRefreshAtRef.Value = if ($mode -eq "discovery") { (Get-Date).AddSeconds(15) } else { (Get-Date).AddMinutes(2) }
    Write-OrchLog "Refreshed orchestrator plan: mode '$mode', persona '$($PersonaRef.Value)', $($TargetUrlsRef.Value.Count) targets"
}

if ($executionMode -ne "discovery" -and $targetUrls.Count -eq 0) {
    Write-OrchLog "No target URLs returned for this Windows host plan. Exiting." "ERROR"
    exit 1
}

Write-OrchLog "Using orchestrator plan for persona '$persona' with $($targetUrls.Count) targets"
if ($executionMode -eq "discovery") {
    Write-OrchLog "Windows runner mode: discovery-only (telemetry and host context only)"
} else {
    Write-OrchLog "Traffic cadence: $minSleep-$maxSleep seconds via $trafficMethod"
    Write-OrchLog "=== Starting Continuous Traffic Session ==="
}
while ($true) {
    $telemetryStatus = if ($executionMode -eq "discovery") { "discovery ($($context.username))" } else { "active ($($context.username))" }
    Send-Telemetry -Status $telemetryStatus

    if ((Get-Date) -ge $planRefreshAt) {
        try {
            Update-PlanFromOrchestrator -PlanRef ([ref]$plan) -PersonaRef ([ref]$persona) `
                -TargetUrlsRef ([ref]$targetUrls) -ExecutionModeRef ([ref]$executionMode) `
                -TrafficMethodRef ([ref]$trafficMethod) -UserAgentRef ([ref]$userAgent) `
                -MinSleepRef ([ref]$minSleep) -MaxSleepRef ([ref]$maxSleep) `
                -PolicyCasesRef ([ref]$policyCases) -PolicyCaseIndexRef ([ref]$planCaseIndex) `
                -PlanRefreshAtRef ([ref]$planRefreshAt)
        } catch {
            Write-OrchLog "Plan refresh failed: $($_.Exception.Message)" "WARNING"
            $planRefreshAt = (Get-Date).AddSeconds(30)
        }
    }

    if ($executionMode -eq "discovery") {
        Start-Sleep -Seconds $discoverySleep
        continue
    }

    if ($targetUrls.Count -eq 0) {
        Write-OrchLog "Current plan has no targets. Sleeping before retry." "WARNING"
        Start-Sleep -Seconds 30
        continue
    }

    $caseId = ""
    $expectedAction = ""
    $target = $null
    $requestMethod = $trafficMethod
    if ($policyCases.Count -gt 0) {
        $case = $policyCases[$planCaseIndex % $policyCases.Count]
        $planCaseIndex++
        $caseId = if ($case.case_id) { [string]$case.case_id } else { "" }
        $expectedAction = if ($case.expected_action) { [string]$case.expected_action } else { "" }
        $target = if ($case.target_url) { [string]$case.target_url } else { "" }
        if ($case.method) { $requestMethod = [string]$case.method }
    } else {
        $target = $targetUrls | Get-Random
    }
    $observedAction = "unknown"
    $testResult = "inconclusive"
    $statusCode = "ERROR"

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $httpException = $null
    try {
        $request = Invoke-WebRequest -Uri $target -Method $requestMethod -Headers @{"User-Agent" = $userAgent} -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        $statusCode = $request.StatusCode
        $observedAction = Get-ObservedPolicyAction -StatusCode $statusCode -Exception $null
        Write-OrchLog "$requestMethod $target -> $($request.StatusCode) (policy: $observedAction)"
    } catch {
        $httpException = $_.Exception
        $statusCode = if ($httpException.Response) { $httpException.Response.StatusCode.value__ } else { "ERROR" }
        $observedAction = Get-ObservedPolicyAction -StatusCode $statusCode -Exception $httpException
        $level = if ($observedAction -eq "allow") { "INFO" } else { "WARNING" }
        Write-OrchLog "$requestMethod $target -> $statusCode (policy: $observedAction)" $level
    }
    $sw.Stop()
    $global:TrafficHistory.Add(@{
        timestamp = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        target = [string]$target
        method = [string]$requestMethod
        status = $statusCode
        latency = [math]::Round($sw.Elapsed.TotalMilliseconds, 0).ToString() + "ms"
    })
    while ($global:TrafficHistory.Count -gt 50) {
        $global:TrafficHistory.RemoveAt(0)
    }

    if ($expectedAction -eq "allow" -or $expectedAction -eq "deny") {
        $testResult = if ($expectedAction -eq $observedAction) { "pass" } else { "fail" }
        Update-AgentHealth @{ last_session_result = $testResult }
        Send-PolicyTestResult -CaseId $caseId -Target $target -Method $requestMethod -ExpectedAction $expectedAction -ObservedAction $observedAction -TestResult $testResult -StatusCode $statusCode
    }

    if ($maxSleep -lt $minSleep) {
        $maxSleep = $minSleep
    }
    $sleepTime = Get-Random -Minimum $minSleep -Maximum ($maxSleep + 1)
    Start-Sleep -Seconds $sleepTime
}
