$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root
$EnvFile = if ($env:ENV_FILE) { $env:ENV_FILE } else { ".env" }
if (-not (Test-Path $EnvFile)) { Copy-Item ".env.example" $EnvFile }

Write-Host "==> Building and starting AMP full lab" -ForegroundColor Cyan
docker compose --env-file $EnvFile -f docker-compose.yml up -d --build

Write-Host "==> Waiting for AMP" -ForegroundColor Cyan
for ($i=0; $i -lt 90; $i++) {
  try {
    docker compose --env-file $EnvFile -f docker-compose.yml exec -T amp python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=2)' | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
  } catch {}
  Start-Sleep -Seconds 2
}

Write-Host "==> Bootstrapping S3 stores and AMP catalogue" -ForegroundColor Cyan
docker compose --env-file $EnvFile -f docker-compose.yml exec -T amp python -m app.lab_bootstrap

Write-Host "`nAMP lab is ready:" -ForegroundColor Green
Write-Host "  AMP UI / API     http://localhost:8080"
Write-Host "  Primary S3       http://localhost:9000   console http://localhost:9001"
Write-Host "  Legacy S3        http://localhost:9100   console http://localhost:9101"
Write-Host "  Solr              http://localhost:8983"
Write-Host "  Tika              http://localhost:9998"
Write-Host "  Hop Server        http://localhost:8182"
