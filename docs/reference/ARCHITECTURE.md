# AMP Enterprise Beta Architecture — 0.9.0-beta.5.0

## 1. Product boundary

AMP separates the **administrative catalogue plane** from the **search/AI data plane**.

The AMP Catalogue exists primarily to prove what a storage container contains and to support administration, audit, migration validation and reconciliation. End-user search is served by Solr/AI indexes, not PostgreSQL catalogue queries.

## 2. Catalogue segregation boundary

The atomic logical catalogue is:

```text
Storage System + Namespace/Bucket/Container = Catalogue Group
```

Examples:

```text
HCP-LDN-01 + namespace/legal        -> CAT-LEGAL-LDN
AWS-UK-PROD + bucket/contracts      -> CAT-CONTRACTS-UK
MinIO-Lab + bucket/legacy-hcp       -> CAT-LAB-LEGACY
```

Each catalogue group has an independent lifecycle, generation history, job scope and reconciliation scope.

## 3. Control-plane registry

A small global control database stores only routing/administrative information:

- storage systems / clusters / accounts
- catalogue groups
- catalogue shard maps
- jobs
- source/target migration links
- policies and connector configuration

It is not the enterprise search catalogue.

## 4. Logical catalogue vs physical shards

Every storage container gets one **logical Catalogue Group**. Large groups are internally split into stable virtual shards and mapped to physical shards.

Default beta layout:

```text
1024 virtual shards
       |
       +--> physical shard 0 : 0..255
       +--> physical shard 1 : 256..511
       +--> physical shard 2 : 512..767
       +--> physical shard 3 : 768..1023
```

`virtual_shard = hash(recon_id) mod 1024`

The virtual shard count remains stable. Physical ranges can later be moved to different PostgreSQL schemas/databases without changing object identity.

## 5. Object identity

Catalogue identity is **source-container local**, not global across all storage systems.

Each Catalogue Group has a persistent `recon_namespace`. AMP and HOP independently derive a **stable logical object identity**:

```text
recon_id = UUIDv5(recon_namespace, logical_object_key)
```

Backend-native versions are observations beneath that stable logical object and have their own version recon IDs. AMP does not create synthetic version folders or own version pruning. A migrated copy in another bucket/namespace receives that target catalogue's recon ID; `origin_recon_id` and `migration_links` preserve source/target lineage.

This allows the source catalogue to be archived or decommissioned without invalidating the target catalogue.

## 6. Discovery generations and tombstones

A full/partial discovery produces a numbered catalogue generation.

```text
Generation N
  -> enumerate storage
  -> upsert observed objects
  -> mark unseen objects MISSING
  -> second consecutive miss -> TOMBSTONED
```

Tombstones are retained so Solr/AI reconciliation can identify stale downstream artifacts after the storage object is deleted.

## 7. HOP and search are independent of the Catalogue

Production indexing path:

```text
Storage namespace/bucket
        |
        v
      Apache HOP
        |
        +--> Tika / extraction
        +--> transformations
        +--> chunking / embeddings
        v
    Solr / AI indexes
```

HOP does not query `catalogue_objects`. It uses source configuration plus the deterministic recon-ID contract and writes recon fields into downstream documents.

AMP then compares the Catalogue against Solr/AI asynchronously.

## 8. Reconciliation axes

Each Catalogue Group, and optionally each physical shard, can run independently:

- **Storage recon**: catalogue key/version/size/checksum vs physical storage
- **Index recon**: catalogue recon ID/hash vs Solr recon ID/hash
- **AI recon**: catalogue recon ID/hash vs AI artifact source hash/model/pipeline version
- **Migration recon**: source catalogue vs target catalogue via migration links
- later: retention, ACL, annotation and governance recon

## 9. Migration and hydration

Migration copies between two independent Catalogue Groups.

```text
Source storage -> copy -> Target storage
Source catalogue       Target catalogue
      |                       |
 source recon ID ----link----> target recon ID
```

The target catalogue row is created only after the physical target write succeeds.

Read-through hydration follows the same rule: source read succeeds, target write succeeds, then target catalogue registration occurs.

## 10. Decommission lifecycle

Catalogue groups can move independently through:

```text
ACTIVE -> FROZEN -> VERIFIED -> ARCHIVED -> DECOMMISSIONED
```

A final storage/index/AI reconciliation should precede decommissioning. The corresponding PostgreSQL shard/database can then be archived or dropped without affecting other catalogue groups or the search data plane.

## 11. Large-scale discovery note

Hash sharding is ideal for catalogue storage and reconciliation jobs, but object stores do not generally support listing by hash range. Large-scale source enumeration should therefore use one of:

- source-native inventory files split into independently consumable parts
- event streams for deltas
- prefix/range partitioning where the key design supports balanced enumeration
- an enumerator that dispatches discovered objects to hash-sharded workers

AMP should not make every physical hash shard independently LIST the entire namespace.

## Protocol-neutral managed ingest

```text
Legacy HCP app                         S3 application / boto3 / AWS SDK
      |                                           |
      v                                           v
HCP REST adapter                          S3 compatibility adapter
      \                                           /
       \                                         /
        +------ AMP Managed Object Service ------+
                       |
                       +-> deterministic logical identity
                       +-> Catalogue registration
                       +-> AMP_PACKAGE_V3
                       +-> annotation/metadata sidecars
                       +-> audit/outbox
                       |
                       v
            S3-compatible backend storage
```

### AMP_PACKAGE_V3

Client keys remain logical. New managed payloads are physically isolated below AMP's reserved namespace:

```text
AMP_MANAGED_HASH: .amp/objects/<2hex>/<2hex>/<GUID>/
CLIENT_PATH:      <logical-client-key>/<GUID>/

Both contain:
  payload
  annotations/*
  manifest.json
```

`package-id` is UUIDv5-derived from the Catalogue Group recon namespace and logical key, so ordinary overwrites and protocol switching reuse the same package root. The Catalogue's `object_key` remains the original client key.

This removes a V1 collision where a valid client key such as `foo/payload` could overlap with AMP's physical package convention.

### Search/indexing implication

HOP still does not read Catalogue rows. It can discover V3 objects through package manifests and derive the same logical/recon identity. For very large managed estates, production should use storage events/inventory feeds rather than repeatedly enumerate `.amp/objects/*` for logical-prefix work.

## Configurable AMP_PACKAGE_V3 placement

The logical client key is independent of physical package placement. A Catalogue Group selects one of two placement modes for future managed writes:

- `AMP_MANAGED_HASH` (default): `.amp/objects/<2hex>/<2hex>/<GUID>/...`
- `CLIENT_PATH`: `<logical-client-key>/<GUID>/...`

In both cases the GUID directory contains the same members: `payload`, `annotations/`, and `manifest.json`. This keeps HOP, reconstruction, annotation lifecycle, and reconciliation independent of the placement policy. Layout changes are non-destructive and apply only to new managed writes.

The default two-level fan-out yields 65,536 leaf prefixes before the GUID package root, avoiding a single massive `.amp/objects/` prefix on object stores that benefit from balanced prefix distribution.


## Backend-authoritative storage semantics

AMP does not implement WORM, retention, legal hold, Object Lock, version pruning, replication or storage lifecycle. Those remain authoritative backend capabilities. AMP observes and catalogues backend VersionIds/compliance state, translates client protocols, and reconciles source/target state. If a backend rejects a PUT/DELETE because of native policy, AMP preserves the backend HTTP outcome and presents it according to the configured Gateway Response Policy.

A repeated write to a logical object reuses the same package GUID and payload/annotation keys. Native backend versioning, when enabled, creates the historic versions.

## Gateway response policy

Each Gateway Route can choose `RAW_BACKEND`, `AMP_NORMALIZED`, or `AMP_NORMALIZED_WITH_BACKEND` and a backend-header policy of `NONE`, `SELECTED`, or `ALL_SAFE`. See `GATEWAY_RESPONSE_POLICY.md`.

## Authentication boundary for this beta

Authentication federation/LDAP/OIDC is intentionally parked. The lab retains simple API/S3 test credentials. The data-model and response-policy work in beta.5 does not attempt to become an IAM/compliance authority.
