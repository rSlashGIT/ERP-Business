$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root 'frontend'
$port = 4177
$url = "http://127.0.0.1:$port/?v=navfix5#/demo"

function Test-SmartStockServer {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 1 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path (Join-Path $frontend 'index.html'))) {
    Write-Host "Could not find frontend\index.html. Please keep this launcher in the project root." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$python = Get-Command python -ErrorAction SilentlyContinue
$args = @('-m', 'http.server', "$port", '--bind', '127.0.0.1')

if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    $args = @('-3', '-m', 'http.server', "$port", '--bind', '127.0.0.1')
}

if (-not $python) {
    Write-Host "Python was not found. Please install Python or add it to PATH." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Test-SmartStockServer)) {
    Write-Host "Starting SmartStock demo server on http://127.0.0.1:$port ..." -ForegroundColor Cyan
    Start-Process -FilePath $python.Source -ArgumentList $args -WorkingDirectory $frontend -WindowStyle Minimized
    Start-Sleep -Seconds 2
}

Write-Host "Opening SmartStock demo..." -ForegroundColor Green
Start-Process $url

