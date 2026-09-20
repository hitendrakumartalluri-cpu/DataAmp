# AMP Catalogue Sharding Strategy

## Purpose

AMP Catalogue is an administrative manifest for reconciliation/audit. It is not the end-user query store.

## Segregation rule

Create exactly one logical Catalogue Group for every:

```text
(storage_system_id, container_type, container_name)
```

`container_name` is an HCP namespace, S3 bucket, filesystem directory boundary or equivalent future container.

This is the decommission/rebuild/reconciliation boundary.

## Recon identity

Each group owns a persistent UUID `recon_namespace`.

```text
recon_id = UUIDv5(recon_namespace, object_key + "|" + native_version_id)
```

HOP receives/exported source configuration containing the same `storage_system_id`, container and recon namespace. Therefore HOP can generate recon IDs without querying the Catalogue.

## Virtual shards

Each Catalogue Group defaults to 1024 virtual shards:

```text
virtual_shard = first_64_bits(SHA256(recon_id)) mod 1024
```

The number of virtual shards does not change after group creation.

Benefits:

- uniform distribution independent of business key prefixes
- stable routing
- deterministic restart/replay
- easy parallel reconciliation
- future physical rebalancing without changing object IDs

## Physical shards

A Catalogue Group maps contiguous virtual-shard ranges onto physical shards. Example with eight physical shards:

```text
000 : 0-127
001 : 128-255
002 : 256-383
003 : 384-511
004 : 512-639
005 : 640-767
006 : 768-895
007 : 896-1023
```

The beta stores routing metadata and executes shard-scoped jobs. The enterprise implementation can map each physical range to a PostgreSQL schema/database/cluster.

## Recommended initial physical-shard counts

These are starting points to benchmark, not hard product limits:

| Active objects in one container | Starting physical shards |
|---:|---:|
| < 10 million | 1-2 |
| 10-100 million | 4-8 |
| 100-500 million | 8-32 |
| 500 million-2 billion | 32-128 |
| > 2 billion | 128+ after workload benchmark |

Other drivers can justify more shards: reconciliation SLA, write/update rate, PostgreSQL index size, regional constraints and job concurrency.

## Why not shard by prefix/date/business metadata?

These values are commonly skewed and can change. Hash routing is preferred for the administrative manifest.

## Important distinction: source enumeration

Hash sharding does **not** make S3/HCP source listing hash-addressable. Do not run 64 workers that each LIST the full bucket and discard 63/64 of results.

Use one of these discovery strategies:

1. **Inventory files** — split S3/HCP inventory output across workers; each row is hash-routed to its physical catalogue shard.
2. **Event deltas** — object create/update/delete events are directly hash-routed.
3. **Enumerator + workers** — one/scaled enumerator emits object observations to shard workers.
4. **Balanced prefix partitions** — only when the source key design provides known balanced prefixes.

## Job scope

Every administrative job carries:

```text
catalogue_group_id
optional shard_id
optional generation_id
```

This allows independent:

- storage reconciliation
- index reconciliation
- AI reconciliation
- governance reconciliation
- repair jobs
- export/archive

## Decommission

Recommended lifecycle:

```text
ACTIVE
 -> FROZEN
 -> final storage/index/AI reconciliation
 -> VERIFIED
 -> ARCHIVED
 -> DECOMMISSIONED
 -> drop/archive physical catalogue shards after audit retention
```

Solr/AI indexes are not implicitly deleted when a catalogue is decommissioned. Cleanup is a separate validated operation, which prevents accidental data loss.
