# Contribution and Governance

## Source-of-truth rules

1. Every product capability has a stable ID in `docs/features/FEATURE_TRACKER.md`.
2. GitHub Issues are the authoritative live status and ownership records.
3. Material architectural choices use ADRs in `docs/adr/`.
4. Options still under evaluation belong in `docs/decision-records/DECISION_MATRIX.md`.
5. A capability is **Implemented** only when code exists and **Verified** only when acceptance evidence passes.
6. Superseded decisions are retained and linked; history is not silently rewritten.
7. Code, tests, docs, ADRs, status, and changelog must move together when affected.
8. The GitHub Wiki is a generated view. Edit canonical repository Markdown, never the Wiki directly.

## Branch and commit convention

- Branch: `feature/AMP-MIG-001-short-description`
- Commit: `AMP-MIG-001: add migration retry policy`
- Pull requests must link features, issues, ADRs, tests, and migration/rollback considerations.

## Definition of done

- Acceptance criteria satisfied.
- Unit/integration tests added and passing.
- Relevant docs and ADRs updated.
- Security, compatibility, upgrade, and operational impact reviewed.
- Test evidence linked from the Issue.

## Lab isolation

Lab-only code belongs under `lab/`. Product services must not depend on lab bootstrap logic, fixed lab credentials, local ports, simulators, or lab topology.
