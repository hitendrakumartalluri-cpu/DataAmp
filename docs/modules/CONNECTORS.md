# Storage Connectors

Connectors integrate with existing storage APIs; they do not expose a replacement storage API.

| Platform | Discovery | Change capture | Metadata | Governance |
|---|---|---|---|---|
| AWS S3 | Inventory / Metadata tables / listing | EventBridge or SQS | System metadata, user metadata, tags, annotations where enabled | Object Lock, retention, legal hold |
| Azure Blob | Blob Inventory | Change Feed / Event Grid | Properties, metadata and blob index tags | Immutability and legal hold |
| HCP | MQE / bounded scan | MQE operations | System metadata and custom metadata/annotations | Retention and legal hold where supported |
| VSP One Object | Native inventory or bounded scan | Native events or polling | S3/native metadata and tags | Product-supported retention/hold APIs |

Every connector publishes a canonical object observation and an explicit capability matrix. Missing backend support is never silently replaced with PostgreSQL-only compliance state.
