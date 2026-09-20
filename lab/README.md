# AMP Lab

Everything in this directory is lab-only. It is intentionally separated from product deployment and architecture.

## Contents

- `docker-compose.yml`: full local integration topology.
- `.env.example`: non-production example configuration.
- `scripts/`: setup, reset, smoke, compatibility, lifecycle, migration, and acceptance scripts.
- `deploy/kind/`: local Kubernetes manifests.
- `docs/SETUP_WINDOWS_WSL2.md`: Windows/WSL2 setup.
- `docs/ACCEPTANCE_TEST_PLAN.md`: ordered Beta 5 validation sequence.

## Quick start

From the repository root:

```bash
cd lab
./scripts/lab-preflight.sh
./scripts/lab-up.sh
```

Run acceptance tests one at a time and stop at the first failure. Lab credentials, local ports, simulators, and topology must never be treated as production defaults.

