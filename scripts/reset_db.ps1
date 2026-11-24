# Rebuilds the database from scratch: schema -> seed -> indexes.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\reset_db.ps1 [-NoIndexes]
param([switch]$NoIndexes)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bin  = 'C:\Program Files\PostgreSQL\18\bin'
$env:PGPASSWORD = 'rescue_dev_pw'

& "$bin\psql.exe" -h 127.0.0.1 -p 5433 -U rescue_admin -d postgres `
    -v ON_ERROR_STOP=1 `
    -c "DROP DATABASE IF EXISTS rescuelink;" `
    -c "CREATE DATABASE rescuelink WITH ENCODING 'UTF8';"
if ($LASTEXITCODE -ne 0) { throw 'Could not recreate database' }

& "$bin\psql.exe" -h 127.0.0.1 -p 5433 -U rescue_admin -d rescuelink `
    -v ON_ERROR_STOP=1 -q -f (Join-Path $root 'sql\01_schema.sql')
if ($LASTEXITCODE -ne 0) { throw 'Schema load failed' }

py -3.11 (Join-Path $root 'sql\03_seed.py')
if ($LASTEXITCODE -ne 0) { throw 'Seeding failed' }

if (-not $NoIndexes) {
    & "$bin\psql.exe" -h 127.0.0.1 -p 5433 -U rescue_admin -d rescuelink `
        -v ON_ERROR_STOP=1 -q -f (Join-Path $root 'sql\02_indexes.sql')
    if ($LASTEXITCODE -ne 0) { throw 'Index creation failed' }
    Write-Host 'Indexes applied.' -ForegroundColor Green
} else {
    Write-Host 'Baseline state (no secondary indexes) for EXPLAIN measurements.' -ForegroundColor Yellow
}
