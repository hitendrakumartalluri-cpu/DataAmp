$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root
$EnvFile = if ($env:ENV_FILE) { $env:ENV_FILE } else { ".env" }
docker compose --env-file $EnvFile -f docker-compose.yml down
