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
