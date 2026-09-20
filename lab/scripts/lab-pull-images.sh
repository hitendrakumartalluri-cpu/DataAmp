#!/usr/bin/env bash
set -euo pipefail
images=(
  "postgres:17.11-alpine"
  "quay.io/minio/minio:RELEASE.2025-05-24T17-08-30Z"
  "apache/kafka:3.9.1"
  "solr:10.0.0"
  "apache/tika:4.0.0-1-full"
  "apache/hop:2.19.0"
)

echo "==> Pulling AMP lab dependency images"
for image in "${images[@]}"; do
  echo
  echo "--> $image"
  if ! docker pull "$image"; then
    echo
    echo "ERROR: unable to pull $image"
    echo "Check registry access, proxy/VPN settings, DNS, and Docker daemon connectivity."
    exit 1
  fi
done

echo
echo "All AMP lab dependency images are available locally."
