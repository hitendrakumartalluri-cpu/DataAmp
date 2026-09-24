# ADR-0011: Retire the client-facing storage gateway

- **Status:** Accepted
- **Date:** 2026-09-24

## Decision

AMP will not expose replacement HCP REST, S3 or cross-protocol client APIs. It will connect to AWS S3, Azure Blob Storage, HCP and VSP One Object through native inventory, read, event and governance APIs.

The accepted Gateway Beta implementation is preserved on `archive/gateway-beta-2026-09-24` and removed from the active runtime, UI, tests and generated Wiki.

## Consequences

- Customers continue using native storage APIs for object operations.
- AMP avoids becoming a data-path bottleneck or protocol-compatibility clone.
- Engineering focuses on indexing, search, analytics, PII, retention, holds and reconciliation.
- Managed package placement, protocol translation, migration hydration and gateway response policies are outside active scope.
