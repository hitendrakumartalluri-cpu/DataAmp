# Managed Object and Annotation Module

## Responsibility

Provides stable logical identity and a portable storage package that preserves payload, manifest and rich annotations across protocols and backends.

## Current Beta 5

AMP_PACKAGE_V3 uses one deterministic package GUID per Catalogue Group and logical key:

```text
<GUID>/
  payload
  manifest.json
  annotations/
```

Placement is either AMP-managed hash fan-out or client-path placement. Rewrites reuse the same keys so backend-native versioning creates history. AMP records payload versions, annotation versions and the annotation snapshot associated with a payload version.

## Boundaries

Client keys remain logical. Package members are system objects and must not appear as independent business objects in catalogue or search results.

## Complete-product scope

Schema-governed annotation families, configurable version limits, richer native-annotation mappings, schema evolution, validation, encryption/classification signals and cross-backend package compatibility.

## Primary code

- `services/control-plane/app/services/managed_objects.py`
- `services/control-plane/app/services/catalog.py`
- `services/control-plane/sql/postgres/001_init.sql`

Tracking: [Portable annotation sidecars #14](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/14).
