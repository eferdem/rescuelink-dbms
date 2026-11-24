# Starts the project-local PostgreSQL cluster (port 5433).
# The cluster lives in .pgdata and is independent of any system-wide
# PostgreSQL installation, so the project can be reproduced anywhere
# without touching the machine's existing server.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root '.pgdata'
$bin  = 'C:\Program Files\PostgreSQL\18\bin'

if (-not (Test-Path $data)) {
    throw "Cluster not found at $data. Run scripts\init_cluster.ps1 first."
}

& "$bin\pg_ctl.exe" -D $data status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "RescueLink PostgreSQL is already running on port 5433." -ForegroundColor Green
    exit 0
}

& "$bin\pg_ctl.exe" -D $data -o '-p 5433 -c listen_addresses=127.0.0.1' -l "$data\server.log" start
Write-Host "Started on 127.0.0.1:5433 (log: $data\server.log)" -ForegroundColor Green
