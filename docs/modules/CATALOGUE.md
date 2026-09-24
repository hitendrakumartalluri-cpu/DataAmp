# Catalogue Module

## Responsibility

Maintains AMP's administrative inventory for connector assurance and reconciliation. It is not the end-user search engine.

## Current foundation

- One Catalogue Group per storage system plus namespace/bucket/container.
- Persistent catalogue-local reconciliation namespace.
- Deterministic UUIDv5 logical object IDs.
- 1024 stable virtual shards mapped to physical shard ranges.
- Discovery generations, ACTIVE/MISSING/TOMBSTONED states.
- Native payload and annotation version inventory.
- Independent freeze, verify, archive and decommission lifecycle.
- Object, version, checksum, metadata and backend-state records.

## Flow

```text
Storage listing/events -> Catalogue Group -> virtual shard
                       -> generation/state -> reconciliation evidence
```

## Boundaries

PostgreSQL catalogue data is administrative truth and evidence, not content search.

## Complete-product scope

Distributed physical shard placement, inventory-driven billion-object baselines, richer compliance/ACL reconciliation, capacity automation and independently archived catalogue stores.

## Primary code

- `services/control-plane/app/services/catalog.py`
- `services/control-plane/app/db.py`
- `services/control-plane/sql/postgres/001_init.sql`
