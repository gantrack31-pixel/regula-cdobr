$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot 'tmp\remote-access\state.json'
if (!(Test-Path -LiteralPath $statePath)) { Write-Host 'No saved remote session.'; exit }
$remoteState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$tunnelProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($remoteState.tunnel_pid)"
$expectedExecutable = Join-Path $projectRoot 'tmp\remote-access\cloudflared.exe'
if ($tunnelProcess -and $tunnelProcess.ExecutablePath -eq $expectedExecutable -and $tunnelProcess.CommandLine -match '127\.0\.0\.1:8767') {
    Stop-Process -Id $tunnelProcess.ProcessId
}
$listener = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if ($serverProcess.CommandLine -match 'uvicorn backend\.app\.main:app' -and $serverProcess.CommandLine -match '8767') {
        Stop-Process -Id $serverProcess.ProcessId
    } else { throw 'Unexpected process on 8767; not stopping it.' }
}
$remoteState | Add-Member -NotePropertyName verified -NotePropertyValue $false -Force
$remoteState | Add-Member -NotePropertyName stopped -NotePropertyValue $true -Force
$remoteState | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
Write-Host 'Remote access stopped. Local app on 8765 was not changed.'
