#!/usr/bin/env bash
set -euo pipefail
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) KARCH=amd64 ;;
  aarch64|arm64) KARCH=arm64 ;;
  *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

KIND_VERSION="${KIND_VERSION:-v0.33.0}"

echo "==> Installing kubectl (current stable, architecture $KARCH)"
KUBECTL_VERSION="$(curl -L -s https://dl.k8s.io/release/stable.txt)"
curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KARCH}/kubectl"
curl -fsSLo /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KARCH}/kubectl.sha256"
echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum --check
chmod +x /tmp/kubectl
sudo install /tmp/kubectl /usr/local/bin/kubectl

echo "==> Installing kind ${KIND_VERSION}"
curl -fsSLo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${KARCH}"
chmod +x /tmp/kind
sudo install /tmp/kind /usr/local/bin/kind

echo "==> Installing Helm via official installer"
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-4 | bash

echo
kubectl version --client
kind version
helm version
