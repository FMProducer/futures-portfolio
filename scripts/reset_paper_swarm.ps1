[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Project Python not found' }
$env:PATH = "$env:LOCALAPPDATA\hermes\node;$env:APPDATA\npm;$env:PATH"
$taskPm2 = (Get-Command pm2.cmd -ErrorAction Stop).Source
$env:PYTHONPATH = "$(Split-Path $taskRoot);$taskRoot"
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-TaskPm2 {
    param([string[]]$Arguments)
    & $taskPm2 @Arguments
    if ($LASTEXITCODE -ne 0) { throw "PM2 failed: $($Arguments -join ' ')" }
}

function Get-TaskProcesses {
    $taskRaw = & $taskPm2 jlist
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read PM2 inventory' }
    # Python preserves case-sensitive environment keys that Windows PowerShell rejects.
    $taskSafe = $taskRaw | & $taskPython (Join-Path $PSScriptRoot 'reset_paper_support.py') inventory
    if ($LASTEXITCODE -ne 0) { throw 'Invalid PM2 inventory' }
    foreach ($taskEntry in ($taskSafe | ConvertFrom-Json)) { Write-Output $taskEntry }
}

$taskConfig = Get-Content -LiteralPath 'config.json' -Raw | ConvertFrom-Json
if ($taskConfig.paper_mode -ne $true -or $taskConfig.max_bots -ne $taskConfig.paper_mode_bots) {
    throw 'Reset requires PAPER-only configuration with zero REAL slots'
}
$taskSlots = [int]$taskConfig.paper_mode_bots
$taskCapital = [double]$taskConfig.portfolios[0].paper_initial_capital
if ($taskSlots -le 0 -or $taskCapital -le 0 -or [double]::IsNaN($taskCapital) -or [double]::IsInfinity($taskCapital)) {
    throw 'Invalid PAPER budget'
}
$taskProcesses = @(Get-TaskProcesses)
if (@($taskProcesses | Where-Object { $_.name -like 'real-*' -and $_.status -ne 'stopped' }).Count) {
    throw 'Active REAL processes detected'
}
Write-Host "PAPER budget: $taskSlots x $taskCapital = $($taskSlots * $taskCapital) USDT; config unchanged"
if ($CheckOnly) { Write-Host 'Preflight passed; nothing changed'; return }

$taskPaper = @($taskProcesses | Where-Object { $_.name -like 'paper-*' })
$taskWriters = @($taskProcesses | Where-Object {
    $_.name -like 'paper-*' -or $_.name -in @('supervisor-service','swarm-observer','swarm-dashboard','swarm-aggregator')
})
foreach ($taskProcess in $taskWriters) { Invoke-TaskPm2 -Arguments @('stop', $taskProcess.name) }
Start-Sleep -Seconds 3
$taskRemaining = @(Get-TaskProcesses | Where-Object {
    $_.name -in $taskWriters.name -and ($_.status -ne 'stopped' -or $_.pid -gt 0)
})
if ($taskRemaining.Count) { throw 'Writers still active; no files moved' }

# Probe SQLite handles before moving any state, including lingering readers outside PM2.
foreach ($taskDatabase in @(Get-ChildItem core -File -Filter 'paper_budget.sqlite*')) {
    try {
        $taskHandle = [IO.File]::Open($taskDatabase.FullName, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $taskHandle.Dispose()
    } catch {
        throw "Budget still locked: $($taskDatabase.FullName). No files moved in this attempt. Close external SQLite readers before retrying."
    }
}
$taskArchive = Join-Path $taskRoot ('reports\paper_clean_slate_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss_fff'))
New-Item -ItemType Directory -Path $taskArchive | Out-Null
Copy-Item -LiteralPath 'config.json' -Destination (Join-Path $taskArchive 'config.start.json')
$taskFiles = @(Get-ChildItem core -File | Where-Object {
    $_.Name -like 'paper_state_*' -or $_.Name -like 'paper_shadow_*' -or $_.Name -like 'paper_budget.sqlite*'
})
$taskFiles += @(Get-ChildItem history -File -ErrorAction SilentlyContinue | Where-Object Name -Match 'paper')
$taskFiles += @(Get-ChildItem core/logs -File -ErrorAction SilentlyContinue | Where-Object Name -Like 'paper_*')
$taskFiles += @(Get-Item logs/supervisor.log -ErrorAction SilentlyContinue)
foreach ($taskFile in $taskFiles) {
    $taskRelative = $taskFile.FullName.Substring($taskRoot.Length + 1)
    $taskDest = Join-Path $taskArchive $taskRelative
    New-Item -ItemType Directory -Force -Path (Split-Path $taskDest) | Out-Null
    Move-Item -LiteralPath $taskFile.FullName -Destination $taskDest
}
foreach ($taskFolder in @('reports\paper_swarm_continuous','reports\paper_swarm_hourly','reports\backtest_gate')) {
    if (Test-Path -LiteralPath $taskFolder) {
        $taskSource = (Resolve-Path -LiteralPath $taskFolder).Path
        if (-not $taskSource.StartsWith($taskRoot + '\')) { throw 'Archive source outside project' }
        $taskDest = Join-Path $taskArchive $taskFolder
        New-Item -ItemType Directory -Force -Path (Split-Path $taskDest) | Out-Null
        Move-Item -LiteralPath $taskSource -Destination $taskDest
    }
}
& $taskPython (Join-Path $PSScriptRoot 'reset_paper_support.py') initialize $taskRoot
if ($LASTEXITCODE -ne 0) { throw "Budget creation failed; archive: $taskArchive" }
foreach ($taskProcess in $taskPaper) { Invoke-TaskPm2 -Arguments @('delete', $taskProcess.name) }

foreach ($taskService in @(
    @{Name='swarm-observer'; Script='scripts/watch_paper_swarm.py'},
    @{Name='swarm-dashboard'; Script='dashboard/dashboard.py'},
    @{Name='supervisor-service'; Script='supervisor/supervisor_service.py'}
)) {
    if ($taskService.Name -in $taskProcesses.name) {
        Invoke-TaskPm2 -Arguments @('restart', $taskService.Name, '--update-env')
    } else {
        $taskArgs = @('start', $taskService.Script, '--name', $taskService.Name, '--cwd', $taskRoot, '--interpreter', $taskPython)
        if ($taskService.Name -eq 'swarm-dashboard') { $taskArgs += @('--','--host','127.0.0.1','--port','8080','--production') }
        Invoke-TaskPm2 -Arguments $taskArgs
    }
}
if ('swarm-aggregator' -in $taskWriters.name) { Invoke-TaskPm2 -Arguments @('restart','swarm-aggregator') }
Write-Host "Reset complete. Archive: $taskArchive"
Write-Host 'Supervisor is selecting candidates; online services do not yet prove PAPER startup.'
Invoke-TaskPm2 -Arguments @('list')
