# AMP Continuous Catalogue Change Capture — beta.4

AMP does **not** keep very large catalogues current by repeatedly performing full bucket/namespace scans.
Each Catalogue Group uses three independent mechanisms:

1. **Baseline** — establish an authoritative starting catalogue.
2. **Change capture** — process storage-native deltas continuously.
3. **Integrity audit** — periodically prove that the event stream has not drifted from storage.

## Common event path

```text
HCP MQE operation polling ─┐
AWS S3 -> EventBridge/SQS ─┼─> AMP source adapter -> Kafka amp.storage.changes
MinIO bucket notifications ─┘                           |
                                                       v
                                            AMP catalogue event consumer
                                                       |
                                               HEAD/verify storage
                                                       |
                                           INSERT / UPDATE / TOMBSTONE
```

The normalized event contains tenant, Catalogue Group, source type, event type, object key,
version, native event identity/time, sequencer and raw payload. The event consumer verifies
create/update events against the source store before changing the catalogue.

## Kafka topics

The beta lab creates:

- `amp.raw.minio.primary`
- `amp.raw.minio.legacy`
- `amp.storage.changes`
- `amp.storage.changes.dlq`

Raw MinIO topics are storage-specific on purpose. Bucket names are not globally unique across
storage systems, so AMP must never route a raw event by bucket name alone. The MinIO adapter
matches the raw topic and bucket to a configured Catalogue Group, normalizes the event and then
publishes to the shared normalized topic.

Normalized events use `catalogue_group_id + object_key` as the Kafka message key. This keeps
changes for the same AMP source object on a stable partition. Catalogue workers use a Kafka
consumer group, so partitions can be processed in parallel.

## Delivery semantics

Storage notifications and Kafka are treated as **at least once**. AMP therefore implements:

- event idempotency keys
- persisted storage-event receipts
- duplicate suppression
- S3/MinIO sequencer watermark checks for stale/out-of-order deliveries
- explicit `APPLIED`, `DUPLICATE`, `IGNORED_STALE` and `FAILED` states
- dead-letter persistence plus a Kafka DLQ
- replay APIs
- catalogue-specific lag/checkpoint status

Kafka offsets remain authoritative in Kafka. AMP also records topic/partition/offset on each
received event for administration and audit.

## HCP

Mode: `HCP_MQE`

The beta includes a configurable MQE polling adapter with an overlap window and persisted
`change_time` checkpoint. It expects the configured MQE endpoint (or an HCP-side translation
proxy) to return JSON operations in the canonical fields described in `EventService.normalize_hcp_operations()`.
Native site-specific HCP authentication/XML request construction must still be validated against
the target HCP release before a production pilot.

Recommended production pattern:

```text
one baseline MQE object query
        +
continuous MQE operation polling with overlap
        +
periodic integrity audit
```

## AWS S3

Mode: `AWS_SQS`

Recommended production path:

```text
S3 -> EventBridge or S3 Event Notification -> SQS -> AMP AWS adapter -> Kafka
```

The adapter long-polls SQS, normalizes direct S3 notifications or EventBridge S3 events, publishes
them to Kafka and deletes the SQS message only after successful Kafka publication.

For billion-object buckets, baseline/integrity discovery should use **S3 Inventory**, not daily
`ListObjectsV2`. S3 Inventory ingestion is an integration target for the next hardening increment;
the current beta baseline implementation still uses the configured storage adapter enumeration.

## MinIO

Mode: `MINIO_KAFKA`

The lab configures each MinIO storage system with its own raw Kafka topic and then enables bucket
notifications. MinIO events are normalized into the same AMP event contract as AWS/HCP changes.

A periodic integrity audit remains required. Events are the live maintenance mechanism, not the
sole proof that the catalogue equals storage.

## Schedules

Change capture is continuous and has no cron interval. AMP beta.4 adds independent schedules for:

- `BASELINE`
- `INTEGRITY_AUDIT`
- `STORAGE_RECON`
- `INDEX_RECON`
- `AI_RECON`
- `ALL_RECON`

The scheduler is deliberately separate from the event consumers.

For very large AWS/MinIO sources the current `INTEGRITY_AUDIT` implementation is still a complete
storage-adapter enumeration. Production hardening should replace this with source-native inventory
or rolling discovery ranges where a full scan cannot fit the required audit window.

## Catalogue independence

HOP does not consume AMP Catalogue rows. It reads each namespace/bucket independently and writes
Solr/AI artifacts carrying the same deterministic AMP reconciliation identity. The catalogue can
therefore reconcile storage, Solr and AI without being on the user search/indexing path.
