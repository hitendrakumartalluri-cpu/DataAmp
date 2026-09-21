# Migration and Hydration Module

## Responsibility

Moves managed object content and annotations between independent storage/catalogue domains while retaining verification evidence and lineage.

## Current Beta 5

- Current logical-object migration.
- Annotation copying into the target AMP package.
- Dry-run planning.
- Independent source and target Catalogue Groups.
- Source-to-target migration links.
- Primary-first reads with legacy fallback.
- Read-through hydration.
- Target registration only after successful physical write.

## Flow

```text
Plan -> read source -> verify -> write target package -> verify target
     -> register target catalogue -> record lineage/report
```

## Boundaries

AMP relies on backend replication rather than implementing dual-write replication. Backend-native compliance behavior remains authoritative.

## Complete-product scope

Scheduled filters, date ranges, priority, concurrency, throttling, retry policy, checksums, historic versions, compliance-state translation, WAN controls, resumability and detailed evidence reporting.

## Primary code

- `services/control-plane/app/services/operations.py`
- `services/control-plane/app/services/managed_objects.py`
- `services/control-plane/app/services/processing.py`
