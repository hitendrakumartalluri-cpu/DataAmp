# Beta 5 Clean-Lab Full Cumulative Regression Evidence — 2026-09-22

**Run ID:** `20260922T113642Z-52430`  
**Profile:** `full`  
**AMP version:** `0.9.0-beta.5.0.6`  
**Result:** **PASS**  
**Reset included:** **YES**

## Stage results

| Stage | Result | Duration |
|---|---|---:|
| shell-syntax | PASS | 0s |
| preflight | PASS | 1s |
| reset | PASS | 83s |
| readiness | PASS | 2s |
| baseline | PASS | 3s |
| smoke | PASS | 1s |
| event-smoke | PASS | 12s |
| catalogue-lifecycle | PASS | 164s |
| hcp-rest | PASS | 65s |
| package-placement | PASS | 7s |
| s3-interop | PASS | 54s |
| native-version | PASS | 17s |
| response-policy | PASS | 4s |
| migration-hydration | PASS | 27s |
| final-event-health | PASS | 19s |

## What this proves

This run validates the complete Beta 5 functional baseline from a destroyed/rebuilt lab, including:

- full lab teardown and rebuild;
- service/bootstrap readiness;
- external storage event capture and Catalogue convergence;
- Catalogue create/update/delete lifecycle;
- HCP REST managed ingest with annotations;
- AMP-managed and client-path package placement;
- S3 SigV4, range/list/metadata/tags and HCP<->S3 interoperability;
- backend-native version ownership and explicit historic-version retrieval;
- Gateway raw/normalized response policy;
- migration dry-run/copy and read-through hydration;
- final Kafka/event health after the cumulative run.

## Verification status

The tested Gateway + Catalogue + Change Capture + Managed Package + Native Versioning + Migration/Hydration baseline is **functionally accepted for Beta 5 in the lab topology**.

This is not production certification. Reconciliation depth, real HCP MQE/AWS integrations, production Hop/Solr pipelines, authentication/RBAC, scale/WAN/HA/failover, failure injection and formal security certification remain separate gates.

## Evidence location in the executing lab

```text
/root/projects/amp-enterprise-beta/.amp-test-results/20260922T113642Z-52430/
```

This repository document records the durable summary. Runtime logs remain in the lab.
