# Baseline Validation

## 2026-09-19 repository initialization

| Check | Result | Notes |
|---|---|---|
| Python unit suite | PASS | 19 tests passed |
| Python compile/import path | PASS | Covered by unit suite |
| Lab acceptance | NOT RUN | Requires Docker/WSL lab; follow `lab/docs/ACCEPTANCE_TEST_PLAN.md` |
| Compose rendering | NOT RUN | Docker CLI unavailable in repository-initialization runtime |
| Production certification | NOT APPLICABLE | Beta 5 is lab-only |

Warnings from the unit run are dependency deprecations around Starlette `TestClient`, AnyIO aliases, and FastAPI `on_event`; they do not fail the baseline but should be tracked as technical debt.


## 2026-09-22 full cumulative regression

| Check | Result | Notes |
|---|---|---|
| Full cumulative profile | PASS | 14/14 stages passed on `0.9.0-beta.5.0.6` |
| Run ID | PASS | `20260922T110649Z-48979` |
| Gateway/Catalogue/Migration functional baseline | VERIFIED (lab) | See `docs/testing/evidence/BETA5_FULL_REGRESSION_20260922.md` |
| Production certification | NOT ACHIEVED | Reconciliation depth, real adapters, scale/WAN/HA, identity/RBAC and formal security gates remain |

## 2026-09-22 clean-lab release-gate run

| Check | Result | Notes |
|---|---|---|
| Full cumulative profile with reset | PASS | 15/15 stages passed on `0.9.0-beta.5.0.6` |
| Run ID | PASS | `20260922T113642Z-52430` |
| Gateway/Catalogue/Migration clean-lab gate | ACCEPTED (lab) | See `docs/testing/evidence/BETA5_CLEAN_LAB_FULL_REGRESSION_20260922.md` |
| Production certification | NOT ACHIEVED | Reconciliation depth, real adapters, scale/WAN/HA, identity/RBAC and formal security gates remain |


## 2026-09-23 Beta 6 Storage reconciliation

| Check | Result | Notes |
|---|---|---|
| Dedicated Storage↔Catalogue reconciliation harness | PASS | TARGETED, TALLY, FULL and controlled drift scenarios completed successfully |
| Cumulative regression with reconciliation stage | PASS | Operator confirmed full regression remained green |
| AMP-REC-001 | VERIFIED (lab) | See `docs/testing/evidence/BETA6_STORAGE_RECONCILIATION_20260923.md` |
| Production-scale reconciliation | NOT CERTIFIED | HCP MQE/AWS Inventory, scale, resumability and parallel shard execution remain future gates |
