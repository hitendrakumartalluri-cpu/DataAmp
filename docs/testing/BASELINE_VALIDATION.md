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

