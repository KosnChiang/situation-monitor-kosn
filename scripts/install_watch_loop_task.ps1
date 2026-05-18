# ============================================================
# scripts/install_watch_loop_task.ps1 -- register mock-only Scheduled
# Tasks for the watch loop + quote feed.
#
# What it does:
#   1. Refuses to run if LIVE_TRADING / EXECUTION_MODE / BROKER_MODE
#      are already set to anything but the mock-only values.
#   2. Refuses to run if any well-known broker credential env is set.
#   3. Registers two scheduled tasks under \AIFiboVisionTrader\:
#        * watch_loop -- invokes scripts/watch_loop_run.ps1
#        * quote_feed -- invokes scripts/quote_feed_run.ps1
#   4. Each task's Action explicitly prefixes the wrapper invocation
#      with `$env:LIVE_TRADING='false'; $env:EXECUTION_MODE='mock';
#      $env:BROKER_MODE='mock';` so the envelope is pinned at the
#      task-action level (visible in Task Scheduler GUI) BEFORE the
#      wrapper's own refuse-if-hot block runs.
#   5. Tasks are registered DISABLED by default. Operator must
#      Enable-ScheduledTask + Start-ScheduledTask, or pass -EnableNow.
#   6. --Submit and --LiveCapture are OPT-IN: install without -Submit
#      means the loop will write to logs/watch_loop.jsonl but never
#      call RiskGate/MockExecutor.
#   7. Refuses to overwrite existing tasks unless -Force is given.
#
# Principal: Interactive, current user, RunLevel Limited. Tasks
#   will NEVER run as LocalSystem / NetworkService / SYSTEM. They
#   only fire when the operator user is logged on, so the offline
#   image capture path works without elevation.
#
# Example:
#   .\scripts\install_watch_loop_task.ps1                       # disabled, dry
#   .\scripts\install_watch_loop_task.ps1 -EnableNow            # enable + start (dry)
#   .\scripts\install_watch_loop_task.ps1 -EnableNow -Submit    # opt-in mock fills
#   .\scripts\install_watch_loop_task.ps1 -Force                # reinstall (overwrite)
# ============================================================

param(
    [switch]$EnableNow,
    [switch]$Submit,
    [switch]$LiveCapture,
    [switch]$DedupeFromTrades,
    [switch]$Force,
    [string]$Symbol = "XAUUSD",
    [double]$BasePrice = 2250.0,
    [double]$QuoteInterval = 1.0,
    [int]$LoopInterval = 5,
    [int]$DedupeCooldown = 60
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

# ----- 1. Refuse if envelope is hot ----------------------------------------
if ($env:LIVE_TRADING -and $env:LIVE_TRADING -ne "false") {
    Write-Error "Refusing to run: LIVE_TRADING=$($env:LIVE_TRADING). Must be 'false'."
}
if ($env:EXECUTION_MODE -and $env:EXECUTION_MODE -ne "mock") {
    Write-Error "Refusing to run: EXECUTION_MODE=$($env:EXECUTION_MODE). Must be 'mock'."
}
if ($env:BROKER_MODE -and $env:BROKER_MODE -ne "mock") {
    Write-Error "Refusing to run: BROKER_MODE=$($env:BROKER_MODE). Must be 'mock'."
}

# ----- 2. Refuse if any broker credential env is set -----------------------
$Forbidden = @(
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
    "IB_ACCOUNT", "IB_USERNAME", "IB_PASSWORD",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN"
)
foreach ($name in $Forbidden) {
    if ([System.Environment]::GetEnvironmentVariable($name, "Process")) {
        Write-Error "Refusing to run: forbidden broker credential env '$name' is set."
    }
}

# ----- 3. Verify wrappers exist --------------------------------------------
$loopWrapper = Join-Path $projectRoot "scripts\watch_loop_run.ps1"
$feedWrapper = Join-Path $projectRoot "scripts\quote_feed_run.ps1"
if (-not (Test-Path $loopWrapper)) { Write-Error "Missing wrapper: $loopWrapper" }
if (-not (Test-Path $feedWrapper)) { Write-Error "Missing wrapper: $feedWrapper" }

# ----- 4. Build task action command lines ----------------------------------
# Each Action pins the envelope env vars INSIDE the command line itself, so
# Task Scheduler GUI shows them and any future hand-edit is visible. The
# wrapper script then re-pins (Phase 5.E) and the python tool re-checks
# (Phase 5.D _refuse_if_live). Triple defense.

$envPin = "`$env:LIVE_TRADING='false'; `$env:EXECUTION_MODE='mock'; `$env:BROKER_MODE='mock';"

$loopExtraArgs = ""
if ($Submit)           { $loopExtraArgs += " -Submit" }
if ($LiveCapture)      { $loopExtraArgs += " -LiveCapture" }
if ($DedupeFromTrades) { $loopExtraArgs += " -DedupeFromTrades" }
$loopExtraArgs += " -Interval $LoopInterval -DedupeCooldown $DedupeCooldown"

$loopCmdInner = "$envPin & '$loopWrapper'$loopExtraArgs"
$loopArgument = "-NoProfile -ExecutionPolicy Bypass -Command `"$loopCmdInner`""

$feedExtraArgs = " -Symbol $Symbol -BasePrice $BasePrice -Interval $QuoteInterval"
$feedCmdInner = "$envPin & '$feedWrapper'$feedExtraArgs"
$feedArgument = "-NoProfile -ExecutionPolicy Bypass -Command `"$feedCmdInner`""

# ----- 5. Define each task -------------------------------------------------
$taskFolder = "\AIFiboVisionTrader\"
$tasks = @(
    @{ Name = "watch_loop"; Argument = $loopArgument },
    @{ Name = "quote_feed"; Argument = $feedArgument }
)

# Common pieces: trigger (AtLogOn current user), principal (Interactive
# Limited), settings (allow battery, no multiple instances).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

# ----- 6. Register each task -----------------------------------------------
foreach ($t in $tasks) {
    $name = $t.Name
    $argument = $t.Argument

    $existing = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name `
                                   -ErrorAction SilentlyContinue
    if ($existing) {
        if (-not $Force) {
            Write-Error ("Task '${taskFolder}${name}' already exists. " +
                "Pass -Force to overwrite, or run scripts\uninstall_watch_loop_task.ps1 first.")
        }
        Write-Host "[install] -Force: removing existing '${taskFolder}${name}'"
        Unregister-ScheduledTask -TaskPath $taskFolder -TaskName $name -Confirm:$false
    }

    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument

    Register-ScheduledTask `
        -TaskPath  $taskFolder `
        -TaskName  $name `
        -Action    $action `
        -Trigger   $trigger `
        -Principal $principal `
        -Settings  $settings `
        -Description "Mock-only $name (Phase 5.F). Envelope pinned in Action. See docs/phase5f_scheduler_runbook.md." | Out-Null

    Write-Host "[install] registered ${taskFolder}${name}"

    if (-not $EnableNow) {
        Disable-ScheduledTask -TaskPath $taskFolder -TaskName $name | Out-Null
        Write-Host "[install]   state: Disabled (operator must Enable-ScheduledTask + Start-ScheduledTask)"
    } else {
        Enable-ScheduledTask -TaskPath $taskFolder -TaskName $name | Out-Null
        Start-ScheduledTask  -TaskPath $taskFolder -TaskName $name
        Write-Host "[install]   state: Enabled + Started"
    }
}

Write-Host ""
Write-Host "----- install summary -----"
Write-Host "  task folder    : $taskFolder"
Write-Host "  watch_loop arg : $loopArgument"
Write-Host "  quote_feed arg : $feedArgument"
Write-Host "  default state  : $(if ($EnableNow) { 'Enabled + Started' } else { 'Disabled' })"
Write-Host "  submit         : $($Submit.IsPresent)"
Write-Host "  live_capture   : $($LiveCapture.IsPresent)"
Write-Host ""
Write-Host "Next steps:"
if ($EnableNow) {
    Write-Host "  - Status: .\scripts\watch_loop_status.ps1"
    Write-Host "  - Stop:   .\scripts\restart_watch_loop.ps1 (or Stop-ScheduledTask)"
} else {
    Write-Host "  - Enable: Enable-ScheduledTask -TaskPath '$taskFolder' -TaskName watch_loop"
    Write-Host "  - Start:  Start-ScheduledTask  -TaskPath '$taskFolder' -TaskName watch_loop"
    Write-Host "  - (do the same for quote_feed before starting watch_loop)"
}
Write-Host "  - Uninstall: .\scripts\uninstall_watch_loop_task.ps1"
