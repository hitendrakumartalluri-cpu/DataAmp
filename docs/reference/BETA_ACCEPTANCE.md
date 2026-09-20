# Enterprise Beta Acceptance Plan — Container-Sharded Catalogue

The beta is accepted when the following scenarios pass.

1. **Container segregation** — register two buckets/namespaces on one or more storage systems and confirm each has a distinct Catalogue Group and shard map.
2. **Discovery generation** — discover one source container and confirm only that catalogue advances generation state.
3. **Stable recon identity** — confirm discovery and the HOP simulator independently derive the same recon ID for the same source key/version.
4. **Independent HOP path** — clear catalogue objects or freeze a catalogue and confirm the indexing pipeline design does not require catalogue-object reads.
5. **Storage reconciliation** — delete a source payload and confirm `MISSING_FROM_STORAGE` is scoped to its catalogue group/shard.
6. **Tombstones** — delete a source payload, run two generations, confirm `MISSING` then `TOMBSTONED`.
7. **Index reconciliation** — remove an index record and confirm `MISSING_FROM_INDEX`; create an orphan and confirm `EXTRA_IN_INDEX`.
8. **AI reconciliation** — remove or stale an AI artifact and confirm the correct finding.
9. **Migration isolation** — migrate one object and confirm source and target have separate catalogue rows/recon IDs linked by `migration_links`.
10. **Hydration** — hydrate into another catalogue and confirm the target record is created only after successful physical write.
11. **Independent lifecycle** — archive/freeze the source catalogue without changing the target catalogue state.
12. **Restart** — restart AMP/PostgreSQL and confirm registry, catalogue generations/shards and reconciliation history persist.

## Production-candidate gates beyond beta

- S3 Inventory / HCP-native high-scale enumeration
- 100M / 1B+ row catalogue benchmark
- physical shard movement between PostgreSQL instances
- shard-specific worker scheduling and throttling
- SolrCloud recon at production scale
- HOP pipeline crash/retry and replay tests
- PostgreSQL HA / backup / PITR
- OIDC/RBAC and secret-manager integrations
- HCP REST compatibility certification
- sustained load and failure-injection testing

## Automated external-object Catalogue lifecycle acceptance

Run:

```bash
./scripts/lab-catalogue-lifecycle-test.sh
```

Default acceptance dataset: 500 externally-created objects, 50 external overwrites and 25 external deletions. The test passes only when Catalogue state converges to 475 ACTIVE + 25 TOMBSTONED rows, all rows have `source_mode=EVENT`, Recon IDs remain stable for modified/deleted objects, payload retrieval reflects the update, and the event pipeline has no FAILED records for the test prefix.

## Protocol interoperability acceptance

Run:

```bash
./scripts/lab-s3-interop-test.sh
```

Acceptance requires:

- boto3/SigV4 PutObject succeeds;
- GetObject, HeadObject, Range GET and ListObjectsV2 succeed;
- S3 metadata and tags are represented as managed sidecars;
- S3 PUT -> HCP GET returns the same logical payload;
- HCP PUT -> S3 GET returns the same logical payload;
- all new managed objects use `AMP_PACKAGE_V3`;
- no `.amp/objects/*` member appears as a business Catalogue object.
