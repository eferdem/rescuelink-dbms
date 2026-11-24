# Creates a brand-new project-local PostgreSQL cluster in .pgdata.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\init_cluster.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root '.pgdata'
$bin  = 'C:\Program Files\PostgreSQL\18\bin'
$port = 5433
$user = 'rescue_admin'
$pass = 'rescue_dev_pw'

if (Test-Path $data) {
    Write-Host "Cluster already exists at $data - skipping initdb." -ForegroundColor Yellow
} else {
    $pwFile = Join-Path $env:TEMP 'rescuelink_pw.txt'
    Set-Content -Path $pwFile -Value $pass -NoNewline -Encoding ascii
    & "$bin\initdb.exe" -D $data -U $user -A scram-sha-256 --pwfile=$pwFile -E UTF8 --locale=C
    Remove-Item $pwFile
}

& "$bin\pg_ctl.exe" -D $data status *> $null
if ($LASTEXITCODE -ne 0) {
    & "$bin\pg_ctl.exe" -D $data -o "-p $port -c listen_addresses=127.0.0.1" -l "$data\server.log" start
}

$env:PGPASSWORD = $pass
& "$bin\psql.exe" -h 127.0.0.1 -p $port -U $user -d postgres `
    -c "DROP DATABASE IF EXISTS rescuelink;" -c "CREATE DATABASE rescuelink WITH ENCODING 'UTF8';"
Write-Host "Cluster ready: postgresql://${user}@127.0.0.1:${port}/rescuelink" -ForegroundColor Green
