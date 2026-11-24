# Stops the project-local PostgreSQL cluster.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bin  = 'C:\Program Files\PostgreSQL\18\bin'
& "$bin\pg_ctl.exe" -D (Join-Path $root '.pgdata') -m fast stop
