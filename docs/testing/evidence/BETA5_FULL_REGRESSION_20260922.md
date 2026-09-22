# Beta 5 Full Cumulative Regression Evidence — 2026-09-22

**Run ID:** `20260922T110649Z-48979`  
**Profile:** `full`  
**AMP version:** `0.9.0-beta.5.0.6`  
**Result:** **PASS**

## Stage results

| Stage | Result | Duration |
|---|---|---:|
| shell-syntax | PASS | 1s |
| preflight | PASS | 1s |
| readiness | PASS | 2s |
| baseline | PASS | 2s |
| smoke | PASS | 5s |
| event-smoke | PASS | 11s |
| catalogue-lifecycle | PASS | 171s |
| hcp-rest | PASS | 72s |
| package-placement | PASS | 7s |
| s3-interop | PASS | 58s |
| native-version | PASS | 18s |
| response-policy | PASS | 4s |
| migration-hydration | PASS | 27s |
| final-event-health | PASS | 29s |

## What this run proves

This single full-profile run cumulatively validated:

- shell/static test harness integrity;
- lab preflight and service readiness;
- external MinIO change capture into Kafka and AMP Catalogue;
- Catalogue create/update/delete lifecycle;
- HCP REST managed ingestion and annotations;
- AMP-managed and client-path package placement;
- S3 SigV4, range/list/metadata/tags and HCP<->S3 interoperability;
- backend-native VersionId ownership and explicit old-version retrieval;
- Gateway raw/normalized response policies;
- migration dry-run/copy and read-through hydration;
- final Kafka/event health with no regression introduced by the cumulative run.

## Verification boundary

This establishes the **Beta 5 functional Gateway/Catalogue/Migration baseline** for the tested lab topology.

It does **not** constitute production certification. Reconciliation depth, real HCP MQE/AWS integrations, production Hop/Solr pipelines, scale/WAN/HA/failover, identity/RBAC and formal security testing remain separate gates.

## Evidence location in the lab

The executing lab retained the full stage logs at:

```text
/root/projects/amp-enterprise-beta/.amp-test-results/20260922T110649Z-48979/
```

Those runtime logs are not committed to the repository. This document records the immutable summary and the verification scope.
