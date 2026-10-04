# ADR-0012: Managed archive ingestion alongside indexing and search

- Status: Accepted for 0.11 beta
- Date: 2026-10-04
- Context: Issue #27 and the explicit product decision to add archiving after retiring the gateway.

AMP accepts bounded ingestion jobs and writes archive payloads and metadata envelopes to configured destinations. It exposes an AMP job API, not a replacement S3, Azure or HCP protocol. This decision updates ADR-0011's exclusion of managed package placement for the archive job path only. Gateway APIs, translation and unified namespaces remain retired.

The archive receipt is independent of index visibility. A record is ARCHIVED only after payload and metadata readback hashes match and any requested native protection is verified. An index outage retains the archive receipt and queues index repair. Local storage provides integrity evidence; it cannot satisfy native WORM requirements.

Profiles pin destination configuration and mapping. Source data is copied, never deleted. Jobs use durable staging, content-bound idempotency, immutable logical IDs, leases, attempts and an indexing outbox. Search projections and evidence packs are derivative records, not substitutes for backend protection.

MFT systems deliver into supported landing zones or submit AMP packages. AMP owns validation, metadata mapping, archival and receipts; the MFT product owns partner onboarding and transfer orchestration. Native retention changes to existing objects and arbitrary bulk mutations require qualified executors and remain plan-only in this beta.

The beta deploys one application process and one worker. PostgreSQL and external source/destination endpoints require the qualification gates in the release notes before customer rollout. Distributed workers, native Solr routing and specialist records connectors are follow-on work.
