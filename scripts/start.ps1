param([switch]$Install, [switch]$Dev, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $venvPython)) {
    python -m venv .venv
    $Install = $true
}
if ($Install) {
    & $venvPython -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (!(Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
    Push-Location frontend
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    Pop-Location
}
if (!$Dev) {
    Push-Location frontend
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    Pop-Location
}
Write-Host "Regula: http://127.0.0.1:$Port"
if ($Dev) {
    Write-Host 'Start the UI in a second terminal: cd frontend; npm.cmd run dev'
    & $venvPython -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port --reload
} else {
    & $venvPython -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port
}
