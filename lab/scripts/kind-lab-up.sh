#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
for c in docker kind kubectl; do command -v "$c" >/dev/null || { echo "missing command: $c"; exit 1; }; done

echo "==> Checking Docker"
docker info >/dev/null

if ! kind get clusters 2>/dev/null | grep -qx amp-lab; then
  echo "==> Creating Kubernetes kind cluster amp-lab"
  kind create cluster --name amp-lab --config deploy/kind/kind-config.yaml --wait 180s
else
  echo "==> kind cluster amp-lab already exists"
fi

echo "==> Building AMP image"
docker build -t amp-enterprise:0.9.0-beta.5.0 ../services/control-plane

echo "==> Loading AMP image into kind"
kind load docker-image amp-enterprise:0.9.0-beta.5.0 --name amp-lab

echo "==> Deploying lab platform"
kubectl apply -f deploy/kind/platform.yaml

echo "==> Waiting for core services"
kubectl -n amp-lab rollout status deploy/postgres --timeout=180s
kubectl -n amp-lab rollout status deploy/kafka --timeout=240s
kubectl -n amp-lab wait --for=condition=complete job/kafka-init --timeout=180s
kubectl -n amp-lab rollout status deploy/minio-primary --timeout=180s
kubectl -n amp-lab rollout status deploy/minio-legacy --timeout=180s
kubectl -n amp-lab rollout status deploy/tika --timeout=240s
kubectl -n amp-lab rollout status deploy/solr --timeout=240s
kubectl -n amp-lab rollout status deploy/hop --timeout=240s || echo "[warn] Hop is still starting; AMP beta can run without Hop executing jobs yet."
kubectl -n amp-lab rollout status deploy/amp --timeout=240s
kubectl -n amp-lab rollout status deploy/amp-events --timeout=180s
kubectl -n amp-lab rollout status deploy/amp-minio-primary-adapter --timeout=180s
kubectl -n amp-lab rollout status deploy/amp-minio-legacy-adapter --timeout=180s
kubectl -n amp-lab rollout status deploy/amp-scheduler --timeout=180s

echo "==> Bootstrapping data stores and AMP catalogue"
kubectl -n amp-lab exec deploy/amp -- python -m app.lab_bootstrap

echo
echo "Kubernetes AMP lab is ready:"
echo "  AMP UI / API     http://localhost:8080"
echo "  Primary S3       http://localhost:9000   console http://localhost:9001"
echo "  Legacy S3        http://localhost:9100   console http://localhost:9101"
echo "  Solr              http://localhost:8983"
echo "  Tika              http://localhost:9998"
echo "  Hop Server        http://localhost:8182"
echo "  Kafka             internal service kafka:9092"
echo "  kubectl context   kind-amp-lab"
