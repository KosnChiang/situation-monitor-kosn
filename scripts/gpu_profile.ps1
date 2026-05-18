# ============================================================
# scripts/gpu_profile.ps1 -- read-only GPU partition profile.
#
# Reports the live state of both GPUs (memory / util / temp / power
# / display attachment), lists which process is on which GPU, and --
# with -Strict -- judges whether the current layout matches the
# project convention:
#
#   GPU 0 (bus 0B): display card  (Windows DWM, TradingView, browsers).
#                   Should NOT host project python or ollama compute.
#   GPU 1 (bus 0C): compute card  (Ollama today; YOLO when wired).
#                   Should be headless (display_active=Disabled).
#
# Does NOT pin LIVE_TRADING / EXECUTION_MODE / BROKER_MODE -- a
# read-only inspection MUST NOT mask a hot envelope the operator is
# trying to detect. Does NOT modify anything: no Stop-Process, no
# Set-Item env at process scope (CUDA_VISIBLE_DEVICES is read, not
# written), no Remove-Item, no scheduled-task touch.
#
# Exit codes:
#   0  OK  (no -Strict, or -Strict with no drift detected)
#   2  DRIFT (-Strict and partition violates the convention)
#
# Examples:
#   .\scripts\gpu_profile.ps1                # informational
#   .\scripts\gpu_profile.ps1 -Strict        # exit 2 on drift
# ============================================================

param(
    [switch]$Strict
)

# Read-only inspection: don't blow up on minor nvidia-smi quirks.
$ErrorActionPreference = "Continue"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

Write-Host "gpu_profile.ps1 -- read-only GPU partition profile"
Write-Host ("  projectRoot         : {0}" -f $projectRoot)
Write-Host ("  strict mode         : {0}" -f $Strict.IsPresent)
Write-Host ("  CUDA_VISIBLE_DEVICES: [{0}]" -f $env:CUDA_VISIBLE_DEVICES)
Write-Host ""

# ----- 1. Per-GPU summary --------------------------------------------------
Write-Host "=== 1. per-GPU summary (mem/util/temp/power/display) ==="
$gpuSummary = nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,display_active --format=csv,noheader,nounits 2>$null
if (-not $gpuSummary) {
    Write-Host "  nvidia-smi unavailable or no NVIDIA GPUs visible to this process."
    Write-Host "  (If you set CUDA_VISIBLE_DEVICES, nvidia-smi still shows all cards;"
    Write-Host "   the most likely cause is the NVIDIA driver / CLI not on PATH.)"
    exit 0
}

Write-Host ("{0,-3} {1,-26} {2,9} {3,9} {4,9} {5,5} {6,5} {7,7} {8}" -f `
    "idx", "name", "mem_total", "mem_used", "mem_free", "util%", "temp", "power_W", "display")
# Hashtable (not OrderedDictionary): the latter treats int keys as positional
# inserts and throws "argument out of range" when the slot doesn't pre-exist.
$gpus = @{}
foreach ($line in @($gpuSummary)) {
    if (-not $line) { continue }
    $p = $line -split ',' | ForEach-Object { $_.Trim() }
    if ($p.Count -lt 9) { continue }
    $idx = [int]$p[0]
    $gpus[$idx] = @{
        index          = $idx
        name           = $p[1]
        mem_total_mb   = [int]$p[2]
        mem_used_mb    = [int]$p[3]
        mem_free_mb    = [int]$p[4]
        util_pct       = [int]$p[5]
        temp_c         = [int]$p[6]
        power_w        = [double]$p[7]
        display_active = $p[8]
        processes      = @()
    }
    Write-Host ("{0,-3} {1,-26} {2,9} {3,9} {4,9} {5,5} {6,5} {7,7} {8}" -f `
        $p[0], $p[1], $p[2], $p[3], $p[4], $p[5], $p[6], $p[7], $p[8])
}

# ----- 2. GPU UUID map -----------------------------------------------------
Write-Host ""
Write-Host "=== 2. GPU UUID map ==="
$uuidQuery = nvidia-smi --query-gpu=index,uuid --format=csv,noheader 2>$null
$indexByUuid = @{}
foreach ($line in @($uuidQuery)) {
    if (-not $line) { continue }
    $p = $line -split ',' | ForEach-Object { $_.Trim() }
    if ($p.Count -lt 2) { continue }
    $idx = [int]$p[0]
    $uuid = $p[1]
    $indexByUuid[$uuid] = $idx
    Write-Host ("  GPU {0} -> {1}" -f $idx, $uuid)
}

# ----- 3. Per-GPU compute process list ------------------------------------
Write-Host ""
Write-Host "=== 3. process list per GPU (nvidia-smi --query-compute-apps) ==="
Write-Host "Note: per-process VRAM is reported N/A on Windows WDDM consumer driver."
Write-Host ""
$procsQuery = nvidia-smi --query-compute-apps=pid,process_name,used_memory,gpu_uuid --format=csv,noheader 2>$null
foreach ($line in @($procsQuery)) {
    if (-not $line) { continue }
    $p = $line -split ',' | ForEach-Object { $_.Trim() }
    if ($p.Count -lt 4) { continue }
    $procId = [int]$p[0]
    $name   = $p[1]
    $mem    = $p[2]
    $uuid   = $p[3]
    if ($indexByUuid.ContainsKey($uuid)) {
        $idx = $indexByUuid[$uuid]
        $gpus[$idx].processes += @{ pid = $procId; name = $name; mem = $mem }
    }
}

foreach ($idx in @($gpus.Keys | Sort-Object)) {
    Write-Host ("--- GPU {0} ({1} compute process(es)) ---" -f $idx, $gpus[$idx].processes.Count)
    if ($gpus[$idx].processes.Count -eq 0) {
        Write-Host "  (no compute processes)"
    } else {
        foreach ($pr in $gpus[$idx].processes) {
            Write-Host ("  pid={0,-7} mem={1,-9} name={2}" -f $pr.pid, $pr.mem, (Split-Path $pr.name -Leaf))
        }
    }
}

# ----- 4. Strict health checks --------------------------------------------
$drift = 0
if ($Strict) {
    Write-Host ""
    Write-Host "=== 4. strict partition health (convention: GPU 0=display, GPU 1=compute) ==="

    # Check 1: GPU 0 display_active = Enabled.
    if ($gpus.Contains(0)) {
        $da = $gpus[0].display_active
        if ($da -eq "Enabled") {
            Write-Host "  [OK]    GPU 0 display_active=Enabled (display card, as expected)"
        } else {
            Write-Host "  [DRIFT] GPU 0 display_active=$da (expected Enabled; monitor not attached?)"
            $drift++
        }
    } else {
        Write-Host "  [INFO]  GPU 0 not present"
    }

    # Check 2: GPU 1 display_active = Disabled.
    if ($gpus.Contains(1)) {
        $da = $gpus[1].display_active
        if ($da -eq "Disabled") {
            Write-Host "  [OK]    GPU 1 display_active=Disabled (headless compute card, as expected)"
        } else {
            Write-Host "  [DRIFT] GPU 1 display_active=$da (expected Disabled; monitor attached to compute card)"
            $drift++
        }
    } else {
        Write-Host "  [INFO]  GPU 1 not present"
    }

    # Check 3: ollama placement -- if present, it must be on GPU 1, NEVER on GPU 0.
    $ollamaOnGpu0 = 0
    $ollamaOnGpu1 = 0
    foreach ($idx in @($gpus.Keys | Sort-Object)) {
        foreach ($pr in $gpus[$idx].processes) {
            if ((Split-Path $pr.name -Leaf) -match '^ollama') {
                if ($idx -eq 0) { $ollamaOnGpu0++ }
                elseif ($idx -eq 1) { $ollamaOnGpu1++ }
            }
        }
    }
    if ($ollamaOnGpu0 -gt 0) {
        Write-Host ("  [DRIFT] {0} ollama.exe process(es) on GPU 0 (must be GPU 1 only)" -f $ollamaOnGpu0)
        $drift++
    } elseif ($ollamaOnGpu1 -gt 0) {
        Write-Host ("  [OK]    {0} ollama.exe on GPU 1 (compute card)" -f $ollamaOnGpu1)
    } else {
        Write-Host "  [INFO]  no ollama.exe compute process detected (LLM not loaded yet)"
    }

    # Check 4: any project python.exe (CommandLine matching tools.*) must NOT
    # land on GPU 0. We cross-reference Get-CimInstance (for CommandLine) with
    # the compute-apps list (for GPU placement).
    $projectNeedle = 'tools\.(watch_fibo_loop|quote_feed|detect_fibo_lines|filter_fibo_lines|mock_fibo_signal|capture_test|analyze_soak|calibrate_chart|gpu_check)'
    $projectPython = @()
    try {
        $projectPython = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and ($_.CommandLine -match $projectNeedle) })
    } catch {}

    if ($projectPython.Count -eq 0) {
        Write-Host "  [INFO]  no project python.exe currently running a tools.* module"
    } else {
        foreach ($py in $projectPython) {
            $onGpu = -1
            foreach ($idx in @($gpus.Keys | Sort-Object)) {
                $hit = $gpus[$idx].processes | Where-Object { $_.pid -eq [int]$py.ProcessId }
                if ($hit) { $onGpu = $idx; break }
            }
            $cmd = $py.CommandLine
            if ($cmd.Length -gt 120) { $cmd = $cmd.Substring(0, 117) + '...' }
            if ($onGpu -eq 0) {
                Write-Host ("  [DRIFT] project python pid={0} on GPU 0: {1}" -f $py.ProcessId, $cmd)
                $drift++
            } elseif ($onGpu -eq 1) {
                Write-Host ("  [OK]    project python pid={0} on GPU 1: {1}" -f $py.ProcessId, $cmd)
            } else {
                Write-Host ("  [INFO]  project python pid={0} not on any GPU (CPU-only path): {1}" -f $py.ProcessId, $cmd)
            }
        }
    }
}

# ----- 5. Exit -------------------------------------------------------------
Write-Host ""
if ($Strict) {
    if ($drift -gt 0) {
        Write-Host ("OVERALL : DRIFT ({0} violation(s) of project GPU convention)" -f $drift)
        Write-Host "          See docs/phase5e_watch_loop_runbook.md / README.md for the GPU partition rationale."
        exit 2
    } else {
        Write-Host "OVERALL : OK (partition matches project convention)"
        exit 0
    }
} else {
    Write-Host "OVERALL : PROFILE complete (informational; no strict checks performed)"
    Write-Host "          Pass -Strict to assert the partition matches project convention."
    exit 0
}
