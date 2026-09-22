# AMP 0.9.0-beta.5.0.6 — Build Freeze

**Freeze date:** 2026-09-22  
**Status:** Functional Beta 5 baseline frozen and accepted in the clean lab topology  
**Production certification:** No

## Frozen scope

The freeze covers the tested Beta 5 functional foundation:

- protocol-neutral managed-object service;
- HCP REST compatibility subset;
- S3-compatible subset with SigV4 lab authentication;
- HCP <-> S3 cross-protocol access;
- AMP_PACKAGE_V3 and configurable package placement;
- Catalogue Groups, sharding, external change capture and object lifecycle;
- backend-native VersionId observation and historic-version retrieval;
- backend-authoritative WORM/retention/legal-hold/lifecycle boundary;
- configurable Gateway response policies;
- migration dry-run/copy, lineage and read-through hydration;
- cumulative event/DLQ/Kafka health checks.

## Clean-lab acceptance evidence

```text
Run ID      : 20260922T113642Z-52430
Profile     : full
AMP version : 0.9.0-beta.5.0.6
Reset       : included
Result      : 15/15 stages PASS
```

Evidence summary:

- `docs/testing/evidence/BETA5_CLEAN_LAB_FULL_REGRESSION_20260922.md`

## Tested artifact identity

The final local Beta 5.0.6 build artifact used during the acceptance cycle had SHA-256:

```text
32368212b8f63ade4991d138b67529e53214a2d8b8c90aab4f90e587bf980fa1
```

Artifact name:

```text
amp-enterprise-beta-0.9.0-beta.5.0.6-error-response-framing-hotfix.zip
```

The governed GitHub repository reorganizes the same product source under `services/`, `lab/`, `deployments/` and `docs/`; lab-only path adjustments and repository test dependencies are intentionally retained.

## Freeze rule

No further feature development belongs on this Beta 5 freeze line.

Changes after this point must be one of:

1. documentation/evidence corrections that do not alter product behavior;
2. critical defect corrections explicitly identified as Beta 5 hotfixes;
3. new work on the normal development line for reconciliation, performance/resilience, or Beta 6.

Every behavioral hotfix must rerun:

```bash
AMP_REGRESSION_RESET=1 \
AMP_REGRESSION_PROFILE=full \
./scripts/lab-cumulative-regression.sh
```

before the freeze evidence is superseded.

## Not included in this freeze

- production HCP MQE/native adapter certification;
- production AWS event integration;
- production Hop-to-Solr pipelines;
- reconciliation depth beyond the current beta flow;
- OIDC/LDAP/AD/RBAC;
- multipart S3, CopyObject and presigned URLs;
- scale/WAN/HA/failover/failure-injection certification;
- formal security certification.

These remain later release gates.
