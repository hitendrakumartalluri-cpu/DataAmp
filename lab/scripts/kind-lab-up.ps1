$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root

foreach ($cmd in @("docker","kind","kubectl")) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Missing command: $cmd" }
}

docker info | Out-Null
$clusters = kind get clusters 2>$null
if ($clusters -notcontains "amp-lab") {
  Write-Host "==> Creating Kubernetes kind cluster amp-lab" -ForegroundColor Cyan
  kind create cluster --name amp-lab --config deploy/kind/kind-config.yaml --wait 180s
}

Write-Host "==> Building AMP image" -ForegroundColor Cyan
docker build -t amp-enterprise:0.9.0-beta.5.0 ../services/control-plane
Write-Host "==> Loading AMP image into kind" -ForegroundColor Cyan
kind load docker-image amp-enterprise:0.9.0-beta.5.0 --name amp-lab
Write-Host "==> Deploying platform" -ForegroundColor Cyan
kubectl apply -f deploy/kind/platform.yaml

foreach ($d in @("postgres","minio-primary","minio-legacy","tika","solr")) {
  kubectl -n amp-lab rollout status "deploy/$d" --timeout=240s
}
try { kubectl -n amp-lab rollout status deploy/hop --timeout=240s } catch { Write-Warning "Hop is still starting; beta can continue." }
kubectl -n amp-lab rollout status deploy/amp --timeout=240s

Write-Host "==> Bootstrapping lab" -ForegroundColor Cyan
kubectl -n amp-lab exec deploy/amp -- python -m app.lab_bootstrap

Write-Host "`nKubernetes AMP lab is ready:" -ForegroundColor Green
Write-Host "  AMP UI / API     http://localhost:8080"
Write-Host "  Primary S3       http://localhost:9000   console http://localhost:9001"
Write-Host "  Legacy S3        http://localhost:9100   console http://localhost:9101"
Write-Host "  Solr              http://localhost:8983"
Write-Host "  Tika              http://localhost:9998"
Write-Host "  Hop Server        http://localhost:8182"
