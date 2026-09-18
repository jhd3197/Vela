#Requires -Version 5.1
<#
.SYNOPSIS
    Windows entry point for the Vela development environment.
.DESCRIPTION
    Runs natively on Windows (no WSL): the backend is `python -m vela` from the
    repo .venv, the frontend is Vite from web/. Mirrors ./dev.sh.

    .\dev.ps1                 Start backend + frontend (Vite, with HMR)
    .\dev.ps1 backend         Start the Vela server only (serves web/dist)
    .\dev.ps1 frontend        Start Vite only, proxying to the backend port.
                              Handy for CSS/UI-only work against an already
                              running server.
    .\dev.ps1 build           Build the dashboard into web/dist and stop
    .\dev.ps1 check           Run the hub checks (npm --prefix web run check)
    .\dev.ps1 setup           First-run setup: venv, dependencies, build

    Options: -BackendPort 7700 -FrontendPort 5173 -NoAutoPort
    Environment: VELA_BACKEND_PORT, VELA_FRONTEND_PORT, VELA_DATA_DIR,
    VELA_VENV, VELA_KILL_PORTS=0 to disable port cleanup.
.EXAMPLE
    .\dev.ps1
    Start backend on 7700 and Vite on 5173 against a disposable data dir.
.EXAMPLE
    .\dev.ps1 frontend -BackendPort 7701
    Start Vite, proxying API calls to a backend already running on 7701.
#>

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$WebDir = Join-Path $ProjectRoot 'web'
$VenvDir = if ($env:VELA_VENV) { $env:VELA_VENV } else { Join-Path $ProjectRoot '.venv' }
$DevDataDir = if ($env:VELA_DATA_DIR) { $env:VELA_DATA_DIR } else { Join-Path $ProjectRoot '.local\dev-data' }

function Write-Header($text) {
    Write-Host
    Write-Host "=== $text ===" -ForegroundColor Cyan
    Write-Host
}

function Get-DevMode {
    foreach ($arg in $args) {
        if ($arg -in @('start', 'backend', 'frontend', 'build', 'check', 'setup')) {
            return $arg
        }
    }
    return 'start'
}

function Get-DevPort {
    param(
        [string[]]$Names,
        [string]$EnvName,
        [int]$Default,
        [string[]]$ArgList
    )

    for ($i = 0; $i -lt $ArgList.Count; $i++) {
        $arg = [string]$ArgList[$i]
        foreach ($name in $Names) {
            if ($arg -eq $name -and ($i + 1) -lt $ArgList.Count) {
                return [int]$ArgList[$i + 1]
            }
            if ($arg.StartsWith("$name=")) {
                return [int]$arg.Substring($name.Length + 1)
            }
        }
    }

    $item = Get-Item -Path "Env:$EnvName" -ErrorAction SilentlyContinue
    if ($item -and $item.Value) {
        return [int]$item.Value
    }

    return $Default
}

function Test-NoAutoPort([string[]]$ArgList) {
    return ($ArgList -contains '--no-auto-port') -or ($ArgList -contains '-NoAutoPort')
}

function Test-PortFree([int]$Port) {
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

function Stop-ListenersOnPort {
    param([int]$Port, [string]$Label)

    if ($env:VELA_KILL_PORTS -eq '0') { return }

    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -ne 0 -and $_ -ne $PID })

    foreach ($processId in $listeners) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        Write-Host "Stopping existing $Label listener on port $Port ($($proc.ProcessName) PID $processId)..." -ForegroundColor Yellow
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-DevPort {
    param(
        [string]$Name,
        [int]$Preferred,
        [int[]]$Reserved = @(),
        [bool]$NoAutoPort = $false
    )

    if ($Reserved -contains $Preferred) {
        throw "$Name port $Preferred is already reserved by another service in this launch."
    }
    if (Test-PortFree $Preferred) {
        return @{ Port = $Preferred; Changed = $false }
    }
    if ($NoAutoPort) {
        throw "$Name port $Preferred is already in use. Stop the other process or omit -NoAutoPort."
    }

    $upper = [Math]::Min($Preferred + 200, 65535)
    for ($candidate = $Preferred + 1; $candidate -le $upper; $candidate++) {
        if ($Reserved -contains $candidate) { continue }
        if (Test-PortFree $candidate) {
            return @{ Port = $candidate; Changed = $true }
        }
    }
    throw "Could not find a free $Name port near $Preferred."
}

function Find-Python {
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            & $cmd.Source -c 'import sys' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $cmd.Source }
        }
    }
    return $null
}

function Get-VenvPython {
    foreach ($candidate in @(
        (Join-Path $VenvDir 'Scripts\python.exe'),
        (Join-Path $VenvDir 'bin\python')
    )) {
        if (Test-Path $candidate) {
            & $candidate -c 'import sys' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}

$script:VelaPy = $null

function Ensure-Venv {
    $py = Get-VenvPython
    if ($py) { $script:VelaPy = $py; return }

    $systemPython = Find-Python
    if (-not $systemPython) {
        throw 'python is required for local development. Install Python 3.10+ and retry.'
    }
    Write-Host "Virtualenv missing or not runnable; creating $VenvDir" -ForegroundColor Yellow
    & $systemPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the venv.' }
    $py = Get-VenvPython
    if (-not $py) { throw 'The venv was created but its Python is not runnable.' }
    $script:VelaPy = $py
}

function Get-RequirementsHash {
    $requirements = Join-Path $ProjectRoot 'requirements.txt'
    if (-not (Test-Path $requirements)) { return '' }
    return (Get-FileHash -Algorithm SHA256 $requirements).Hash.ToLower()
}

# Install when the venv cannot import the core packages, or when
# requirements.txt changed since the last successful install.
function Ensure-BackendPackages {
    $marker = Join-Path $VenvDir '.requirements-sha256'

    & $script:VelaPy -c 'import fastapi, uvicorn' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $want = Get-RequirementsHash
        $have = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { '' }
        if (-not $want -or $want -eq $have) { return }
        Write-Host 'requirements.txt changed since the last install; syncing...' -ForegroundColor Yellow
    }

    Write-Host "Installing Python dependencies into $VenvDir..." -ForegroundColor Yellow
    & $script:VelaPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    & $script:VelaPy -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

    $want = Get-RequirementsHash
    if ($want) { Set-Content -Path $marker -Value $want }
}

function Ensure-WebPackages {
    if (Test-Path (Join-Path $WebDir 'node_modules')) { return }
    Write-Host 'web/node_modules is missing; running npm ci...' -ForegroundColor Yellow
    & npm --prefix $WebDir ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
}

function Build-Dashboard {
    Ensure-WebPackages
    Write-Host 'Building the dashboard into web/dist...' -ForegroundColor Yellow
    & npm --prefix $WebDir run build
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard build failed.' }
}

$mode = Get-DevMode @args
$backendPort = Get-DevPort -Names @('--backend-port', '-BackendPort') -EnvName 'VELA_BACKEND_PORT' -Default 7700 -ArgList $args
$frontendPort = Get-DevPort -Names @('--frontend-port', '-FrontendPort') -EnvName 'VELA_FRONTEND_PORT' -Default 5173 -ArgList $args
$noAutoPort = Test-NoAutoPort $args
$isHelp = $args -contains '-h' -or $args -contains '--help'

if ($isHelp) {
    Get-Help $PSCommandPath -Detailed
    exit 0
}

function Start-BackendProcess {
    param([int]$Port, [switch]$PassThru)

    Ensure-Venv
    Ensure-BackendPackages
    New-Item -ItemType Directory -Force -Path $DevDataDir | Out-Null

    $env:VELA_DATA_DIR = $DevDataDir
    $argv = @('-m', 'vela', '--no-open-browser', '--host', '127.0.0.1', '--port', "$Port")
    if ($PassThru) {
        return Start-Process -FilePath $script:VelaPy -ArgumentList $argv -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    }
    & $script:VelaPy @argv
    exit $LASTEXITCODE
}

function Start-FrontendProcess {
    param([int]$Port, [string]$BackendUrl, [switch]$PassThru)

    Ensure-WebPackages
    $env:VELA_BACKEND_URL = $BackendUrl
    $argv = @('--prefix', $WebDir, 'run', 'dev', '--', '--host', '127.0.0.1', '--port', "$Port", '--strictPort')
    if ($PassThru) {
        return Start-Process -FilePath 'npm.cmd' -ArgumentList $argv -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    }
    & npm.cmd @argv
    exit $LASTEXITCODE
}

switch ($mode) {
    'backend' {
        Stop-ListenersOnPort -Port $backendPort -Label 'backend'
        $resolved = Resolve-DevPort -Name 'Backend' -Preferred $backendPort -NoAutoPort $noAutoPort
        Write-Header "Starting Vela backend (http://localhost:$($resolved.Port))"
        Write-Host "  Data dir: $DevDataDir"
        if (-not (Test-Path (Join-Path $WebDir 'dist'))) {
            Write-Host '  Note: web/dist is missing; run .\dev.ps1 build or start the frontend for the dashboard.' -ForegroundColor Yellow
        }
        if ($resolved.Changed) {
            Write-Host "  Note: backend port $backendPort was busy; using $($resolved.Port)." -ForegroundColor Yellow
        }
        Write-Host
        Start-BackendProcess -Port $resolved.Port
    }
    'frontend' {
        Stop-ListenersOnPort -Port $frontendPort -Label 'frontend'
        $resolved = Resolve-DevPort -Name 'Frontend' -Preferred $frontendPort -NoAutoPort $noAutoPort
        Write-Header "Starting Vite (http://localhost:$($resolved.Port))"
        Write-Host "  API target: http://localhost:$backendPort"
        Write-Host '  (expects a Vela backend there; .\dev.ps1 backend starts one)'
        if ($resolved.Changed) {
            Write-Host "  Note: frontend port $frontendPort was busy; using $($resolved.Port)." -ForegroundColor Yellow
        }
        Write-Host
        Start-FrontendProcess -Port $resolved.Port -BackendUrl "http://localhost:$backendPort"
    }
    'build' {
        Write-Header 'Building the dashboard'
        Build-Dashboard
        Write-Host
        Write-Host 'Done. web/dist is fresh; restart the backend (or reload the page) to see it.' -ForegroundColor Green
    }
    'check' {
        Write-Header 'Vela checks'
        Ensure-WebPackages
        $venvPy = Get-VenvPython
        if ($venvPy -and -not $env:VELA_TEST_PYTHON) { $env:VELA_TEST_PYTHON = $venvPy }
        & npm --prefix $WebDir run check
        exit $LASTEXITCODE
    }
    'setup' {
        Write-Header 'Vela first-run setup'
        Ensure-Venv
        Ensure-BackendPackages
        Ensure-WebPackages
        New-Item -ItemType Directory -Force -Path $DevDataDir | Out-Null
        Build-Dashboard
        Write-Host
        Write-Host 'Setup complete. Start the dev environment with .\dev.ps1' -ForegroundColor Green
    }
    'start' {
        Stop-ListenersOnPort -Port $backendPort -Label 'backend'
        Stop-ListenersOnPort -Port $frontendPort -Label 'frontend'
        $backendResolved = Resolve-DevPort -Name 'Backend' -Preferred $backendPort -NoAutoPort $noAutoPort
        $frontendResolved = Resolve-DevPort -Name 'Frontend' -Preferred $frontendPort -Reserved @($backendResolved.Port) -NoAutoPort $noAutoPort
        $backendUrl = "http://localhost:$($backendResolved.Port)"

        Write-Host
        Write-Host 'Vela Dev Server' -ForegroundColor Cyan
        Write-Host "  Dashboard (Vite, HMR): http://localhost:$($frontendResolved.Port)"
        Write-Host "  Backend:               $backendUrl"
        Write-Host "  Health:                $backendUrl/api/health"
        Write-Host "  Data dir:              $DevDataDir"
        if ($backendResolved.Changed) {
            Write-Host "  Note: backend port $backendPort was busy; using $($backendResolved.Port)." -ForegroundColor Yellow
        }
        if ($frontendResolved.Changed) {
            Write-Host "  Note: frontend port $frontendPort was busy; using $($frontendResolved.Port)." -ForegroundColor Yellow
        }
        Write-Host

        # Prepare dependencies before spawning, so both processes only serve.
        Ensure-Venv
        Ensure-BackendPackages
        Ensure-WebPackages
        New-Item -ItemType Directory -Force -Path $DevDataDir | Out-Null

        $env:VELA_DATA_DIR = $DevDataDir
        $backendProc = Start-Process -FilePath $script:VelaPy -NoNewWindow -PassThru -WorkingDirectory $ProjectRoot `
            -ArgumentList @('-m', 'vela', '--no-open-browser', '--host', '127.0.0.1', '--port', "$($backendResolved.Port)")

        Start-Sleep -Seconds 2

        $env:VELA_BACKEND_URL = $backendUrl
        $frontendProc = Start-Process -FilePath 'npm.cmd' -NoNewWindow -PassThru -WorkingDirectory $ProjectRoot `
            -ArgumentList @('--prefix', $WebDir, 'run', 'dev', '--', '--host', '127.0.0.1', '--port', "$($frontendResolved.Port)", '--strictPort')

        Write-Host 'Press Ctrl+C to stop...' -ForegroundColor DarkGray
        try {
            Wait-Process -Id $backendProc.Id, $frontendProc.Id -Any -ErrorAction SilentlyContinue
            if (-not $backendProc.HasExited -and -not $frontendProc.HasExited) {
                # No process has exited yet; idle until one does or Ctrl+C lands.
                while (-not $backendProc.HasExited -and -not $frontendProc.HasExited) {
                    Start-Sleep -Seconds 1
                    $backendProc.Refresh()
                    $frontendProc.Refresh()
                }
            }
            Write-Host
            Write-Host 'One dev process stopped; shutting down the rest.' -ForegroundColor Yellow
        } finally {
            foreach ($proc in @($backendProc, $frontendProc)) {
                if ($proc -and -not $proc.HasExited) {
                    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                }
            }
            # Vite spawns child node processes; make sure nothing keeps the port.
            Stop-ListenersOnPort -Port $frontendResolved.Port -Label 'frontend'
        }
        Write-Host 'Stopped.'
    }
}
