# Validation Model

AMP treats testing as part of the product architecture: every new increment must preserve the previously verified behavior of the Gateway, Catalogue, managed-object package, change-capture, and migration layers.

## Verification lifecycle

```text
Implemented
    ↓ dedicated acceptance test
Stage PASS
    ↓ cumulative regression
Regression PASS
    ↓ evidence linked
Verified
```

A feature is not promoted to **Verified** simply because the code exists.

## Functional validation cycles

| Cycle | What it proves |
|---|---|
| Platform readiness | The complete lab topology, expected build, Catalogue Groups, Kafka routes and backend prerequisites are available. |
| External change capture | Objects created/updated/deleted outside AMP reach the correct Catalogue through storage events without a full discovery scan. |
| HCP REST managed ingestion | Legacy HCP-style applications can write/read logical objects and annotations through the canonical AMP object service. |
| Package placement | AMP-managed hash fan-out and client-path placement preserve one stable package contract. |
| S3/HCP interoperability | S3 and HCP protocol adapters operate on the same logical AMP objects; range, list, metadata and tags behave correctly. |
| Backend-native versions | Backend storage owns VersionIds and lifecycle; AMP observes/maps versions without creating a parallel version store. |
| Response policy | Raw and normalized Gateway responses preserve the backend outcome while controlling client-visible headers/diagnostics safely. |
| Migration and hydration | Source/target Catalogues stay independent, lineage is recorded, and target state is registered only after a successful physical write. |
| Reconciliation | Storage, Catalogue, search/index and AI representations can be independently compared for completeness and drift. |
| Performance/resilience | Throughput, latency, backlog recovery, retry/idempotency, failure and HA behavior satisfy release targets. |

## Cumulative-regression rule

Every increment reruns all earlier functional cycles. This prevents a later change—for example response normalization—from silently breaking Range GET, HCP compatibility, Catalogue event processing, or migration.

The repository contains the executable test plan and detailed evidence. The Wiki documents this verification model and what each cycle establishes; individual run logs remain repository-only.

## Product boundary validated by the cycles

The tests repeatedly enforce these architectural guarantees:

- backend storage is authoritative for native versions, WORM, retention, legal hold, lifecycle, pruning and operation acceptance;
- AMP owns protocol translation, logical identity, package layout, Catalogue, migration orchestration, reconciliation, audit and presentation policy;
- the Catalogue is administrative/reconciliation state, not the end-user search index;
- package members remain hidden from the logical client namespace;
- backend outcome is preserved even when AMP changes the wire representation;
- target Catalogue state is never created before the physical target write succeeds.
