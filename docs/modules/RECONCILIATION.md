# Reconciliation

## Purpose

Reconciliation is AMP's independent assurance plane. It compares authoritative storage and downstream representations against the administrative Catalogue without making those systems runtime-dependent on the Catalogue.

The Catalogue remains **administrative/audit state**. Search users continue to use Solr/AI indexes directly.

## Core rule

> Reconciliation detects and records drift. It does not silently repair data.

Any repair, re-index, re-catalogue or migration action must be an explicit controlled operation with its own audit trail.

## Reconciliation scopes

Every run is scoped to one:

- tenant;
- Catalogue Group = storage system + namespace/bucket;
- optional physical Catalogue shard;
- optional logical prefix;
- bounded object limit.

This matches AMP's sharding model so multiple reconciliation jobs can run independently and a Catalogue shard can be retired independently.

## Storage reconciliation modes

### TARGETED

Normal frequent mode.

For each bounded Catalogue object AMP performs a point verification against the authoritative physical payload.

Checks include:

- object/payload existence;
- size;
- checksum when the backend exposes a comparable checksum;
- native VersionId drift;
- AMP package manifest existence and manifest identity;
- managed annotation sidecar existence/size/checksum.

This is the preferred daily/continuous assurance path because it can be divided by physical shard and logical prefix.

### TALLY

Explicit inventory/count pre-check.

Compares the number of logical objects represented by authoritative storage inventory with the active Catalogue count.

A mismatch creates `COUNT_MISMATCH` evidence and is a signal to run a deeper scoped investigation.

The lab implementation obtains inventory through the generic backend listing API. Production adapters should use source-native scalable inventory:

- **HCP:** MQE tally/window queries;
- **AWS S3:** S3 Inventory and event/checkpoint state rather than repeated full ListObjects over billion-object buckets;
- **MinIO:** event ledger for deltas, with explicit listing only for baseline/integrity scans.

### FULL

Expensive bidirectional integrity mode.

Runs TARGETED validation and authoritative inventory comparison, additionally detecting objects physically present in storage that are not represented by an active Catalogue row.

FULL is intended for:

- initial assurance;
- scheduled integrity audits;
- mismatch investigation after TALLY;
- pre/post migration evidence;
- shard decommission validation.

It is **not** the normal daily billion-object strategy.

## Current storage finding taxonomy

| Finding | Severity | Meaning |
|---|---|---|
| `MISSING_FROM_STORAGE` | HIGH | Active/MISSING Catalogue object has no authoritative payload |
| `SIZE_MISMATCH` | MEDIUM | Catalogue and backend payload sizes differ |
| `CHECKSUM_MISMATCH` | HIGH | Comparable payload checksums differ |
| `VERSION_DRIFT` | MEDIUM | Current backend native version differs from observed Catalogue version |
| `MANIFEST_MISSING` | HIGH | AMP package manifest is absent |
| `MANIFEST_INVALID` | HIGH | Manifest cannot be parsed/validated |
| `MANIFEST_DRIFT` | HIGH | Manifest logical identity/package/payload mapping disagrees with Catalogue |
| `ANNOTATION_MISSING` | HIGH | Active managed annotation sidecar is absent |
| `ANNOTATION_SIZE_MISMATCH` | MEDIUM | Managed annotation size differs |
| `ANNOTATION_CHECKSUM_MISMATCH` | HIGH | Comparable annotation checksums differ |
| `COUNT_MISMATCH` | MEDIUM | Authoritative logical inventory count differs from Catalogue |
| `EXTRA_IN_STORAGE` | HIGH | Storage contains a logical object with no active Catalogue object |
| `TOMBSTONED_BUT_PRESENT` | HIGH | Catalogue says deleted but authoritative storage still exposes the object |
| `VERIFY_FAILED` | MEDIUM | Point verification could not be completed |
| `INVENTORY_READ_ERROR` | MEDIUM | Explicit inventory scan encountered an AMP-managed package/inventory error |

ETag is retained as diagnostic information but is not treated as a universal content hash because ETag semantics vary by backend and multipart implementation.

## Backend authority

Reconciliation must never cause AMP to become the compliance authority.

Backend storage remains authoritative for:

- native versions and pruning;
- WORM / retention;
- legal hold / Object Lock;
- lifecycle;
- replication and durability;
- acceptance or rejection of storage operations.

AMP observes these states and can report drift, but does not emulate them in PostgreSQL.

## Index reconciliation

The next reconciliation increment compares Catalogue objects with the search representation using:

- stable Recon ID;
- source content hash;
- pipeline version;
- indexed timestamp;
- missing/stale/orphan detection.

Production Solr/HOP reconciliation must remain storage-direct: Hop does not require Catalogue access during indexing. Reconciliation joins the independently produced index state back to Catalogue evidence afterwards.

## AI reconciliation

The subsequent increment compares AI artifacts with source content using:

- Recon ID;
- source content hash;
- model/model version;
- pipeline version;
- artifact status.

This lets AMP identify stale embeddings/summaries/classifications after source data changes.

## Scheduling

Existing schedule types remain:

- `STORAGE_RECON`;
- `INDEX_RECON`;
- `AI_RECON`;
- `ALL_RECON`.

Frequent schedules should use bounded TARGETED checks. TALLY/FULL integrity scans should be less frequent and use native inventory providers at production scale.

## API

`POST /api/v1/reconciliation/run` accepts:

```json
{
  "tenant_id": "demo",
  "catalogue_group_id": "<uuid>",
  "target": "STORAGE",
  "shard_id": null,
  "prefix": "contracts/2026/",
  "storage_mode": "TARGETED",
  "verify_package_members": true,
  "limit": 10000
}
```

Storage modes: `TARGETED`, `TALLY`, `FULL`.

Findings can be filtered by Catalogue Group, job, target and severity.

## Acceptance

Run:

```bash
./lab/scripts/lab-reconciliation-test.sh
```

The acceptance test proves:

1. clean TARGETED reconciliation produces zero findings;
2. missing payload is detected;
3. physical payload drift is detected;
4. missing managed annotation is detected;
5. missing manifest is detected;
6. TALLY detects logical count drift;
7. after controlled restoration, FULL reconciliation returns clean.

The reconciliation stage is also part of the cumulative regression runner on the development line.
