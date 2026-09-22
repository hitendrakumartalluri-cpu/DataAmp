# AMP Testing Cycles

This document defines the repeatable validation cycles used to promote AMP capabilities from **Implemented** to **Verified**.

## Testing principle

AMP uses cumulative regression: every new increment must re-run the previously proven feature set so that a new capability cannot silently break Gateway, Catalogue, package, migration, or event behavior.

The normal entry point is:

```bash
./scripts/lab-cumulative-regression.sh
```

Release-candidate validation should run the same suite against a clean lab:

```bash
AMP_REGRESSION_RESET=1 \
AMP_REGRESSION_PROFILE=full \
./scripts/lab-cumulative-regression.sh
```

Each run writes evidence under:

```text
.amp-test-results/<run-id>/
  <stage>.log
  summary.txt
  summary.json
```

The suite is fail-fast by default. Use `AMP_REGRESSION_CONTINUE=1` only when the objective is to collect all failures in one run.

## Profiles

| Profile | Catalogue lifecycle | HCP REST | S3/HCP interoperability | Purpose |
|---|---:|---:|---:|---|
| `quick` | 20 create / 4 update / 2 delete | 3 objects | 3 S3 + 1 HCP | Frequent developer regression |
| `full` | 50 / 10 / 5 | 10 objects | 10 S3 + 1 HCP | Milestone / release acceptance |
| `soak` | 500+ | larger set | larger set | Performance-oriented regression |

## Cycle 0 — Static and unit gate

**Runs:** shell syntax checks, unit suite, import/compile checks.

**Proves:**
- governed repository scripts are syntactically valid;
- core service logic still passes automated unit/regression tests;
- obvious breakage is caught before containers or storage are touched.

**Does not prove:** Docker integration, network behavior, backend compatibility, or scale.

## Cycle 1 — Platform readiness

**Runs:** preflight, clean start/reset when requested, Beta 5 readiness, base smoke checks.

**Proves:**
- PostgreSQL, Kafka, Primary/Legacy object stores, Solr, Tika, Hop and AMP are reachable;
- the expected AMP build is running;
- primary and legacy Catalogue Groups exist;
- raw Kafka routes/change-capture configuration exists;
- backend-native bucket versioning required by the lab is enabled.

## Cycle 2 — External change capture and Catalogue lifecycle

**Runs:** event smoke test plus Catalogue create/update/delete lifecycle.

**Proves:**
- objects written outside AMP are detected without a Gateway write;
- MinIO notification -> raw Kafka -> normalized Kafka -> Catalogue works;
- external create becomes `ACTIVE`;
- overwrite refreshes storage state while preserving the logical reconciliation identity;
- delete becomes `TOMBSTONED`;
- package/system members do not leak into the business Catalogue;
- Kafka consumers converge without unexplained failed or stuck events.

## Cycle 3 — HCP REST managed ingestion

**Runs:** HCP REST ingest harness.

**Proves:**
- HCP-style PUT/GET/HEAD/DELETE routes through the canonical AMP object service;
- payload plus default/legal/migration annotations round-trip;
- managed package and manifest are created;
- Catalogue source mode identifies the Gateway path;
- annotation/package members remain hidden from the logical business object namespace;
- response framing is correct for bodyless and payload operations.

## Cycle 4 — Managed package placement

**Runs:** package-placement test.

**Proves:**
- `AMP_MANAGED_HASH` creates two-level deterministic fan-out;
- `CLIENT_PATH` preserves the application-provided logical path;
- both modes retain the same package interior:
  `<GUID>/{payload,annotations,manifest.json}`;
- placement changes physical location, not the package-format contract.

## Cycle 5 — S3/HCP protocol interoperability

**Runs:** signed S3 interoperability harness.

**Proves:**
- S3 Put/Get/Head/Delete/ListObjectsV2 subset works through AMP;
- SigV4 lab validation works;
- Range GET wire framing is correct;
- user metadata and tags map to sidecars;
- S3-created objects can be read through HCP REST;
- HCP-created objects can be read through S3;
- reserved package members never appear in logical listings.

## Cycle 6 — Backend-native version ownership

**Runs:** native-version test.

**Proves:**
- rewriting one logical object reuses the same AMP Object ID, Recon ID, package GUID and physical payload key;
- the backend creates the native VersionIds;
- current reads return the latest backend version;
- explicit historic-version reads return the requested old backend version;
- annotation snapshots can be associated with observed payload versions;
- AMP does not create its own payload-version tree, prune versions, or own WORM/lifecycle behavior.

## Cycle 7 — Gateway response policy

**Runs:** response-policy test.

**Proves:**
- `AMP_NORMALIZED` returns a stable generic response model;
- `AMP_NORMALIZED_WITH_BACKEND` adds backend diagnostics without changing the backend outcome;
- `RAW_BACKEND` preserves backend-native error semantics for compatibility clients;
- `NONE`, `SELECTED`, and `ALL_SAFE` header policies can be applied safely;
- raw backend metadata can be captured for audit/support;
- backend representation headers cannot corrupt AMP-generated error, range, PUT, or DELETE response framing.

## Cycle 8 — Migration and read-through hydration

**Runs:** migration/hydration harness.

**Proves:**
- dry-run produces a copy plan without target writes;
- current logical payload plus annotations can migrate to a target AMP package;
- source and target Catalogues remain independent;
- lineage links the two physical representations;
- hydration writes the physical target first and registers the target Catalogue entry only after success;
- Gateway migration orchestration does not take ownership of backend-native WORM/version/lifecycle controls.

## Cycle 9 — Reconciliation

**Runs:** storage, index, and AI reconciliation suites as they mature.

**Proves:**
- Catalogue state agrees with authoritative storage;
- source and target migration evidence agrees where required;
- search/index representations are complete and not stale/orphaned;
- AI artifacts refer to the expected source content/version.

This cycle is the next functional acceptance area after the current Gateway/Catalogue baseline.

## Cycle 10 — Performance, soak, failure and resilience

**Runs:** increasing object/operation volumes, failure injection, WAN/latency, restart/replay, upgrade/rollback and HA tests.

**Proves:**
- sustained throughput and event-to-Catalogue latency;
- Kafka backlog/recovery behavior;
- DB and backend bottlenecks;
- safe retry/idempotency under failures;
- release-specific non-functional targets.

This cycle is required before production certification and is not implied by functional PASS results.

## Promotion rule

A feature can be marked **Implemented** when the code exists.

It can be promoted to **Verified** only when:
1. its dedicated acceptance stage passes;
2. the cumulative regression run also passes;
3. evidence is retained and linked to the relevant feature/issue;
4. any known limitation is explicitly documented.

## Current cumulative acceptance coverage

The cumulative runner currently covers:
- platform readiness;
- external event capture;
- Catalogue lifecycle;
- HCP REST ingestion and annotations;
- package placement;
- S3/HCP interoperability;
- backend-native versioning;
- Gateway response policy;
- migration and hydration;
- final event/Kafka/DLQ health.

Reconciliation and non-functional performance/resilience stages are added to the same runner as they are implemented.
