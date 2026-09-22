# Project Status

**As of:** 2026-09-22  
**Development baseline:** 0.9.0-beta.6.0-dev.1  
**Frozen reference:** 0.9.0-beta.5.0.6  
**Lifecycle:** Enterprise beta; lab validation required; not production-certified

## Current objective

Keep the accepted Beta 5 reference frozen while building Beta 6 reconciliation, then performance/resilience and production adapters.

## Implemented in the baseline

- HCP REST and S3-compatible gateway subsets over one managed-object service.
- AMP package V3 with hashed or client-path placement and sidecar annotations.
- Per-route raw or normalized gateway responses and safe backend-header policies.
- Backend-native VersionId tracking and payload-to-annotation snapshot mapping.
- Container-scoped Catalogue Groups, 1024 virtual shards, generations, missing states, and tombstones.
- Kafka-based MinIO change capture with idempotency, stale-event handling, DLQ, and replay foundations.
- Discovery, migration, dry-run, read-through hydration, lineage, and reconciliation beta flows.
- Management UI and API for the implemented beta functions.
- Docker Compose lab, kind starter, Helm starter, unit tests, and scripted lab acceptance tests.

## Current verification state

- Unit suite passes from the governed repository layout: **19 passed** on 2026-09-19.
- The complete clean-lab acceptance sequence is documented under `lab/docs/ACCEPTANCE_TEST_PLAN.md`.
- Cumulative regression is the verification gate: `scripts/lab-cumulative-regression.sh` re-runs all previously proven functional stages and retains per-stage evidence under `.amp-test-results/`.
- **Full cumulative regression PASS** on 2026-09-22 for AMP `0.9.0-beta.5.0.6`, run `20260922T110649Z-48979`; all 14 stages passed.
- **Clean-lab full cumulative regression PASS** on 2026-09-22, run `20260922T113642Z-52430`; reset + all 15 stages passed. Evidence: `docs/testing/evidence/BETA5_CLEAN_LAB_FULL_REGRESSION_20260922.md`.
- The tested Gateway/Catalogue/Change-Capture/Managed-Package/Native-Version/Migration-Hydration functional baseline is now **Accepted for Beta 5 in the lab topology**. This is not production certification.
- No WAN, scale, HA, failover, security federation, or production HOP/Solr certification exists yet.

## Immediate next actions

1. Validate the new Storage↔Catalogue reconciliation stage, then promote `AMP-REC-001` only after cumulative evidence passes.
2. Add Index/Solr reconciliation, then AI artifact reconciliation.
3. Continue to run the cumulative regression gate after every increment; use individual stages only for fault isolation.
3. Implement real HCP adapter/MQE integration and production HOP-to-Solr pipelines.
4. Add OIDC/RBAC, secrets handling, metrics, tracing, and SLOs before production claims.

## Known blockers and risks

- Authentication is deliberately parked in Beta 5.
- AWS and HCP event adapters are scaffolds, not production integrations.
- Multipart S3, CopyObject, presigned URLs, and full version APIs are deferred.
- Historic-version migration and native compliance-state translation are not implemented.
- The Helm chart is a starter and does not establish HA or production sizing.

Status changes belong in GitHub Issues/Projects; this page summarizes direction and release readiness only.
