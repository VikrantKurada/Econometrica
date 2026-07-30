<#
.SYNOPSIS
    Start Econometrica: database, API and web app, in that order.

.DESCRIPTION
    One trigger for the whole stack. It brings up Postgres, applies migrations,
    then opens the backend and the frontend in their own windows so their logs
    stay readable and either can be stopped with Ctrl-C.

    Addresses are named rather than left to the OS, and that is not cosmetic.
    Vite binds ::1 by default, so the app is reachable at localhost:5173 and not
    at 127.0.0.1:5173; and a container on this machine holds the wildcard
    address on 8000, which answers 127.0.0.1:8000 as well, so the API is put
    somewhere else entirely rather than raced for.

.PARAMETER ApiPort
    Port for the API. The default is 8001, not 8000: a container on this
    machine holds the wildcard address on 8000 and answers requests to
    127.0.0.1:8000 with a 404 even while uvicorn is bound there. The script
    moves up from whatever it is given until it finds a port nobody is on, and
    points the frontend proxy at the one it used.

.PARAMETER PriceSource
    Where a run's prices come from: yahoo (real, needs network), synthetic
    (generated, every run flagged `synthetic_data`), or none (a run refuses).
    Overrides .env for this session only.

.PARAMETER SkipInstall
    Skip `uv sync` and `npm install`. Faster when nothing has changed.

.PARAMETER NoBrowser
    Do not open the app when it is ready.

.PARAMETER Stop
    Stop the API and the web app started by an earlier run. Leaves the database
    container up; `docker compose stop db` takes that down.

.EXAMPLE
    .\start.ps1
.EXAMPLE
    .\start.ps1 -PriceSource synthetic   # works with no network at all
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8001,
    [ValidateSet('yahoo', 'synthetic', 'none', 'fred')]
    [string]$PriceSource = 'yahoo',
    [switch]$SkipInstall,
    [switch]$NoBrowser,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$WebPort = 5173

# Both servers are launched detached so their logs stay readable, so stopping
# them means remembering them. Killing by port alone is not enough: `uv` is a
# trampoline that spawns the real interpreter, and node spawns children, so the
# listener's owner is rarely the process a window is attached to.
$PidFile = Join-Path $env:TEMP 'econometrica-start.pids'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

# Native tools write progress to stderr -- `docker compose` and `git` both do
# -- and under `$ErrorActionPreference = 'Stop'` PowerShell turns every one of
# those lines into a terminating NativeCommandError. So a healthy container
# reporting "Running" aborted the script. The exit code is the only honest
# success signal an external program gives, so that is what is checked.
function Invoke-Native {
    param([scriptblock]$Command, [string]$What)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Merged and re-emitted as plain text. Left on the error stream these
        # print in red with a stack trace, so a healthy container reporting
        # "Running" reads as a failure. The merge is safe only because the
        # preference is Continue for the duration.
        & $Command 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)." }
}

#: True when the command exits 0, with stderr tolerated as above. For the cases
#: where a non-zero exit is a question rather than a failure.
function Test-Native {
    param([scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command | Out-Null } catch { } finally { $ErrorActionPreference = $previous }
    return $LASTEXITCODE -eq 0
}

# Any listener at all counts as occupied, whatever address it claims. A socket
# on the wildcard address answers 127.0.0.1 traffic too -- that is what the
# container on 8000 does, and it wins the race often enough that uvicorn's own
# successful bind proves nothing about who serves the next request.
function Find-FreePort($start) {
    for ($port = $start; $port -lt $start + 20; $port++) {
        $taken = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if (-not $taken) { return $port }
    }
    throw "No free port in $start..$($start + 19)."
}

function Stop-Tree($processId, $label) {
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { return }
    Write-Ok "stopping $label (pid $processId) and its children"
    # /T because the window owns a tree -- powershell -> uv -> python, or
    # npm -> node. Killing the root alone orphans the child holding the port.
    #
    # No `2>&1`: redirecting a native command's stderr inside PowerShell wraps
    # each line in an ErrorRecord, which under `$ErrorActionPreference = 'Stop'`
    # would abort the stop on a process that had already exited.
    try { & taskkill /PID $processId /T /F | Out-Null } catch { }
}

# `taskkill /T` reaches current descendants only, and `uv` is a trampoline that
# exits once it has spawned the real interpreter -- so the uvicorn process is
# re-parented and survives its window being killed. It then holds the port,
# the next start silently moves up one, and every request reaches the old code.
# Matching on the module name makes this precise enough to be safe: it cannot
# touch the unrelated container that also listens on this range.
function Stop-StrayApi($port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq '127.0.0.1' } |
        ForEach-Object {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -like '*econometrica.main:app*') {
                Write-Ok "stopping orphaned API (pid $($proc.ProcessId)) on port $port"
                try { & taskkill /PID $proc.ProcessId /T /F | Out-Null } catch { }
            }
        }
}

if ($Stop) {
    Write-Step 'Stopping Econometrica'
    if (Test-Path $PidFile) {
        $recordedPort = $null
        foreach ($line in Get-Content $PidFile) {
            $label, $value = $line -split '=', 2
            if ($label -eq 'port') { $recordedPort = $value -as [int]; continue }
            if ($value -as [int]) { Stop-Tree ([int]$value) $label }
        }
        if ($recordedPort) { Stop-StrayApi $recordedPort }
        Remove-Item $PidFile -Force
    } else {
        Write-Warn "No record of a running stack ($PidFile is absent)."
    }
    Write-Host 'Stopped. The database container is still running.' -ForegroundColor Green
    exit 0
}

# --- prerequisites ----------------------------------------------------------

Write-Step 'Checking prerequisites'
foreach ($exe in 'docker', 'uv', 'npm') {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        throw "$exe is not on PATH. See the Prerequisites section of README.md."
    }
}
Write-Ok 'docker, uv and npm found'

if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Ok 'created .env from .env.example'
}

# --- database ---------------------------------------------------------------

Write-Step 'Starting the database'

# Docker Desktop does not autostart on this machine, and a stopped engine looks
# like ~40 unrelated test errors if it is discovered later instead of here.
if (-not (Test-Native { docker info --format '{{.ServerVersion}}' })) {
    $desktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { throw 'Docker is not running and Docker Desktop was not found.' }
    Write-Ok 'Docker engine is down -- starting Docker Desktop (this takes a minute)'
    Start-Process $desktop
    $deadline = (Get-Date).AddMinutes(3)
    $ready = $false
    while (-not $ready -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $ready = Test-Native { docker info --format '{{.ServerVersion}}' }
    }
    if (-not $ready) { throw 'Docker Desktop did not become ready within 3 minutes.' }
}

Push-Location $Root
try {
    Invoke-Native { docker compose up -d db --wait } 'docker compose up -d db'
} finally { Pop-Location }
Write-Ok 'econometrica-db is healthy on port 5433'

# --- backend ----------------------------------------------------------------

Push-Location "$Root\backend"
try {
    if (-not $SkipInstall) {
        Write-Step 'Syncing backend dependencies'
        Invoke-Native { uv sync --extra dev } 'uv sync'
    }

    Write-Step 'Applying migrations'
    Invoke-Native { uv run alembic upgrade head } 'alembic upgrade head'
} finally { Pop-Location }

# --- frontend deps ----------------------------------------------------------

if (-not $SkipInstall -and -not (Test-Path "$Root\frontend\node_modules")) {
    Write-Step 'Installing frontend dependencies'
    Push-Location "$Root\frontend"
    try {
        Invoke-Native { npm install } 'npm install'
    } finally { Pop-Location }
}

# --- servers ----------------------------------------------------------------

$chosen = Find-FreePort $ApiPort
if ($chosen -ne $ApiPort) { Write-Warn "Port $ApiPort is in use -- using $chosen instead." }
$ApiPort = $chosen

Write-Step "Starting the API on http://127.0.0.1:$ApiPort"
Write-Ok "price source: $PriceSource"
if ($PriceSource -eq 'none') {
    Write-Warn 'With no price source an analysis refuses rather than inventing data.'
}
if ($PriceSource -eq 'synthetic') {
    Write-Warn 'Synthetic prices are generated, not market data. Every run says so.'
}

# No --reload. The reloader binds the socket in the parent and hands it to a
# child, so stopping the parent leaves an orphan holding port 8000 -- the next
# start fails with [Errno 10048] and every request reaches the old code.
$apiCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Econometrica API'
Set-Location '$Root\backend'
`$env:ECONOMETRICA_PRICE_SOURCE = '$PriceSource'
uv run uvicorn econometrica.main:app --host 127.0.0.1 --port $ApiPort
"@
$api = Start-Process powershell -ArgumentList '-NoExit', '-Command', $apiCmd -PassThru
# The port is recorded too, so `-Stop` can sweep an orphan off it.
Set-Content -Path $PidFile -Value @("api=$($api.Id)", "port=$ApiPort") -Encoding utf8

# A cold start imports statsmodels, arch and linearmodels; on a first run, with
# those DLLs unread and the virus scanner interested, it has taken over two
# minutes. Warm, it is a few seconds.
Write-Host -NoNewline '    waiting for the API'
$deadline = (Get-Date).AddSeconds(240)
do {
    Start-Sleep -Seconds 2
    Write-Host -NoNewline '.'
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2
    } catch { $health = $null }
} while (-not $health -and (Get-Date) -lt $deadline)
Write-Host ''
if (-not $health) { throw "The API did not answer /api/health within 240s. Read its window." }
# Assert it is ours. A 200 from something else on the port would otherwise read
# as success, which is the failure mode this whole port dance exists to avoid.
if ($health.status -ne 'ok') { throw "Something other than Econometrica is on port $ApiPort." }
Write-Ok "API ready (version $($health.version))"

Write-Step "Starting the web app on http://localhost:$WebPort"
# vite.config.ts proxies /api to ECONOMETRICA_API_URL, defaulting to port 8000.
# The API may not be there, so the proxy is told where it actually is.
$webCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Econometrica web'
Set-Location '$Root\frontend'
`$env:ECONOMETRICA_API_URL = 'http://127.0.0.1:$ApiPort'
npm run dev
"@
$web = Start-Process powershell -ArgumentList '-NoExit', '-Command', $webCmd -PassThru
Add-Content -Path $PidFile -Value "web=$($web.Id)" -Encoding utf8

# localhost, not 127.0.0.1: Vite binds only the first address the OS resolves,
# which is ::1 here, so the IPv4 address is refused outright.
$deadline = (Get-Date).AddSeconds(90)
$up = $false
do {
    Start-Sleep -Seconds 2
    try {
        Invoke-WebRequest "http://localhost:$WebPort/" -TimeoutSec 2 -UseBasicParsing | Out-Null
        $up = $true
    } catch { }
} while (-not $up -and (Get-Date) -lt $deadline)
if (-not $up) { throw "The web app did not answer within 90s. Read its window." }

Write-Host ''
Write-Host "Econometrica is up." -ForegroundColor Green
Write-Host "  app      http://localhost:$WebPort" -ForegroundColor Green
Write-Host "  API      http://127.0.0.1:$ApiPort/docs"
Write-Host "  charts   http://localhost:$WebPort/gallery.html"
Write-Host ''
Write-Host "Stop with Ctrl-C in either window, or: .\start.ps1 -Stop"

if (-not $NoBrowser) { Start-Process "http://localhost:$WebPort" }
