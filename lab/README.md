# AMP Lab

Everything in this directory is lab-only. It is intentionally separated from product deployment and architecture.

## Contents

- `docker-compose.yml`: full local integration topology.
- `.env.example`: non-production example configuration.
- `scripts/`: setup, reset, smoke, connector lifecycle and reconciliation scripts.
- `deploy/kind/`: local Kubernetes manifests.

## Quick start

From the repository root:

```bash
cd lab
./scripts/lab-preflight.sh
./scripts/lab-up.sh
```

For cumulative regression after every increment:

```bash
./scripts/lab-cumulative-regression.sh
```

For the clean-lab release gate:

```bash
AMP_REGRESSION_RESET=1 AMP_REGRESSION_PROFILE=full \
./scripts/lab-cumulative-regression.sh
```

Use individual acceptance tests for fault isolation. Lab credentials, local ports, simulators, and topology must never be treated as production defaults.
