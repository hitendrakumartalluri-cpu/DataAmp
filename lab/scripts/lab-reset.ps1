$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root
$EnvFile = if ($env:ENV_FILE) { $env:ENV_FILE } else { ".env" }
if (-not (Test-Path $EnvFile)) { Copy-Item ".env.example" $EnvFile }
Write-Host "Removing all AMP lab Postgres, S3 and Solr data volumes..." -ForegroundColor Yellow
docker compose --env-file $EnvFile -f docker-compose.yml down -v --remove-orphans
& "$PSScriptRoot\lab-up.ps1"
